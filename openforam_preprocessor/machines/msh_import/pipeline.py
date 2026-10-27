"""The run of an imported-mesh project (M1-MSH; docs/msh_import_notes.md).

    validate (the file and the configuration, before any OpenFOAM command)
    -> import: re-save (binary Gmsh), convert at scale 1, transformPoints to
       metres, the boundary surface (topoSet, surfaceMeshExtract; M2a), the
       checks on what the converter wrote (cases/imported/)
    -> assemble: copy, createPatch (renamed patches), foamDictionary (AMI
       pairs under their own names, patch types) (cases/merged/)
    -> checkMesh, AMI weights
    -> mesh views: the merged mesh's surface and two cuts, for the 3D pane
    -> mesh validation -> case setup (with physics)

The same run lock, status file, run record, artifact manifest, environment
check and resource preflight as an STL project (MachinePipeline). Each step
is reused only when its recorded inputs and outputs verify: the import on the
file's content hash, the converter, the scale and the tool; the assembly on
the imported mesh and the patch settings, so a patch-type change never
converts again, and a physics change re-runs case setup only (invariant 14).
A converter's exit code 0 is not success: the import checks the mesh it
wrote, and checkMesh's failed checks are classified (quality.py, M2a): a
BLOCKING one fails the mesh whatever the exit code (owner review of E0), a
quality-only failure is a WARNING with the cells past each limit per cell
zone. Every mesh is written binary and never read whole in Python (M2a):
areas, radii and boxes come from the surface.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from case_setup.validation import check_physics
from core.artifacts.hashing import files_under, hash_files, sha256_json
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from machines.assembly import AMI_WEIGHTS_DICT, Step
from machines.case_generator import _fv_schemes, _fv_solution
from machines.config import ImportedMesh, MachineParts
from machines.heavy_lock import HeavyLock
from machines.mesh_folder import (
    MeshExpectation,
    check_ami_pairing,
    check_ami_weights,
    check_patches,
    parse_ami_weights,
    read_patches,
    region_count,
)
from machines.msh_import import interfaces, quality, resources, surface
from machines.msh_import.ami_detail import pair_details
from machines.msh_import.cases import (
    BAFFLES_LOG,
    IMPORTED_CASE,
    MERGED_CASE,
    SCRATCH_CASE,
    ImportCases,
    binary_control_dict,
    convert_steps,
    converter,
    coupled_pairs,
    import_cases,
    needs_resave,
    scale_step,
    used_names,
)
from machines.msh_import.checks import (
    UNGROUPED,
    check_baffles,
    check_converted,
    check_zones,
    face_zone_names,
    has_mesh,
    mesh_cells,
    mesh_zone_names,
    unused_face_zones,
)
from machines.msh_import.detect import MeshFileInfo
from machines.msh_import.validation import read_mesh_file
from machines.operations import ASSEMBLE, CASE_SETUP, CHECK_MESH, VALIDATE, VALIDATE_MESH
from machines.pipeline import (
    _EXCLUDED,
    _MESH_NOT_READY,
    _NO_PHYSICS,
    AMI_WEIGHTS_LOG,
    CHECK_MESH_LOG,
    MESH_REPORT,
    POLY_MESH,
    PREFLIGHT_REPORT,
    QUALITY_ZONES,
    SUPERSEDED_MESH_REPORT,
    VERIFIED_VERSIONS,
    MachinePipeline,
    MachineRunResult,
    StageResult,
    StageStatus,
    _issue,
    _read,
    _Run,
    _Stop,
)
from machines.previews import ACROSS, ALONG, imported_cut_planes
from machines.project_store import write_last_meshed
from machines.validation import check_all
from mesh.estimator import CellEstimate, ResourceStatus
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityValidator, QualityStatus, ValidityStatus
from openfoam.commands import MeshingStep
from openfoam.dictionary import OpenFOAMFileWriter
from openfoam.environment import tool_identity, validate_environment
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus
from vawt.status import RunState

IMPORT = "machine_import_mesh"
MESH_VIEWS = "machine_mesh_views"  # the merged mesh's surface and cuts (3D pane)
OPERATION_STEPS = {IMPORT: "import (convert, surface)", ASSEMBLE: "assembly",
                   CHECK_MESH: "checkMesh", MESH_VIEWS: "views for the 3D pane"}
STEP_KINDS = {IMPORT: ("convert", "surface"), CHECK_MESH: ("checkMesh", "amiWeights"),
              MESH_VIEWS: ("views",)}  # resources.py's steps of each operation
IMPORT_REPORT = "reports/mesh_import.json"
STEP_RESOURCES = "reports/step_resources.json"  # each heavy step's estimate (resources.py)
QUALITY_REPORT = QUALITY_ZONES  # per cell zone (quality.py)
SETS_DIR = "postProcessing/constant"  # checkMesh -writeSets vtk, in cases/merged/
ZERO_CASE = MERGED_CASE + quality.ZERO_SUFFIX  # the second quality pass (quality.py)
AREA_AFTER_IMPORT = "INLET_AREA_AFTER_IMPORT"  # case_setup.validation
# postProcess AMIWeights reads the mesh as converted: the rotor not turned.
AT_REST = "at rest (0 deg)"
SCRATCH_RECORD = "scratch.json"  # in cases/imported_scratch/
PROMOTE_LOG = "00_promote.log"
CONVERTERS = ("fluent3DMeshToFoam", "gmshToFoam")
SURFACE_EXECUTABLES = ("topoSet", "surfaceMeshExtract")  # surface.py
IMPORT_EXECUTABLES = (*CONVERTERS, "transformPoints", *SURFACE_EXECUTABLES)
ASSEMBLE_EXECUTABLES = ("createPatch", "foamDictionary")
CHECK_EXECUTABLES = ("checkMesh", "postProcess")
VIEW_EXECUTABLES = (*SURFACE_EXECUTABLES, "postProcess")
# Heuristic only: tetrahedra per node of a Gmsh mesh, for the preflight when the
# file declares no cell count (a tetrahedral mesh has about 5-6 per node).
CELLS_PER_GMSH_NODE = 6
_GENERATED = ("system/createPatchDict", AMI_WEIGHTS_DICT)  # removed when not written
_STAGE = {"fluent3DMeshToFoam": IssueStage.MESH_IMPORT, "gmshToFoam": IssueStage.MESH_IMPORT,
          "transformPoints": IssueStage.MESH_IMPORT, "createPatch": IssueStage.PATCH_CREATION,
          "foamDictionary": IssueStage.PATCH_CREATION, "checkMesh": IssueStage.CHECK_MESH,
          "postProcess": IssueStage.MESH_VALIDATION, "topoSet": IssueStage.MESH_IMPORT,
          "surfaceMeshExtract": IssueStage.MESH_IMPORT}


def import_operations(physics: bool) -> tuple[str, ...]:
    return (VALIDATE, IMPORT, ASSEMBLE, CHECK_MESH, MESH_VIEWS, VALIDATE_MESH,
            *((CASE_SETUP,) if physics else ()))


def import_executables(info: MeshFileInfo, factor: float) -> tuple[str, ...]:
    return (converter(info), *(("transformPoints",) if factor != 1.0 else ()),
            *SURFACE_EXECUTABLES)


def _write(root: Path, case: str, files: Mapping[str, str]) -> None:
    for relative, body in files.items():
        OpenFOAMFileWriter.write_if_changed(root / "cases" / case / relative, body)
    for relative in _GENERATED:
        if relative not in files:
            (root / "cases" / case / relative).unlink(missing_ok=True)


def _mesh_files(root: Path, case: str) -> list[Path]:
    return files_under(root / "cases" / case / POLY_MESH, exclude_dirs=_EXCLUDED)


@dataclass(frozen=True)
class _Plan:
    info: MeshFileInfo | None  # None for a machine of several files (parts_run.py)
    digest: str
    factor: float
    cases: ImportCases


def preflight_cells(info: MeshFileInfo) -> tuple[int | None, str]:
    """Cells for the resource check, and where the number comes from: the file's
    declared count (Fluent), else nodes x CELLS_PER_GMSH_NODE (Gmsh, heuristic),
    else None (a binary Gmsh file: not assessed)."""
    if info.declared_cells is not None:
        return info.declared_cells, "declared by the file"
    if info.nodes:
        return info.nodes * CELLS_PER_GMSH_NODE, (f"heuristic: {info.nodes} nodes x "
                                                  f"{CELLS_PER_GMSH_NODE}")
    return None, "the file gives no cell or node count"


class ImportRun:
    """One imported-mesh run inside MachinePipeline.run (its lock, status and record)."""

    def __init__(self, pipe: MachinePipeline, run: _Run) -> None:
        self.pipe, self.run = pipe, run
        mesh = run.config.imported_mesh
        assert mesh is not None
        self.mesh: ImportedMesh | MachineParts = mesh
        self.heavy = HeavyLock()
        self.key = ""  # the file's content hash, once read (the resources history)
        self.estimates: dict[str, resources.Estimate] = {}

    # --- cache inputs ------------------------------------------------------------------

    def _tool(self, names: tuple[str, ...]) -> str:
        return self.pipe._tool(self.run.config, names)

    # --- what a several-file run (parts_run.py) does its own way ---------------------------

    def _plan(self) -> _Plan:
        mesh = self.mesh
        assert isinstance(mesh, ImportedMesh)
        info, digest = read_mesh_file(mesh.source_path)
        assert digest is not None  # validation: the file is readable and unchanged
        return _Plan(info, digest, mesh.source_units.to_metres, import_cases(self.run.config))

    def _import_executables(self, plan: _Plan) -> tuple[str, ...]:
        assert plan.info is not None
        return import_executables(plan.info, plan.factor)

    def _step_kinds(self, op: str) -> tuple[str, ...]:
        return STEP_KINDS.get(op, ())

    def _file_bytes(self, plan: _Plan) -> int | None:
        return plan.info.size if plan.info is not None else None

    def _declared_cells(self, plan: _Plan) -> int | None:
        assert plan.info is not None
        return preflight_cells(plan.info)[0]

    def _converter(self, plan: _Plan) -> str:
        assert plan.info is not None
        return converter(plan.info)

    def _inputs(self, plan: _Plan, op: str) -> dict[str, str]:
        root = self.run.root
        if op == IMPORT:
            assert plan.info is not None
            dictionaries = [root / "cases" / IMPORTED_CASE / r for r in sorted(plan.cases.imported)]
            return {**hash_files(root, dictionaries), "source": plan.digest,
                    "converter": converter(plan.info), "resave": str(needs_resave(plan.info)),
                    "scale": repr(plan.factor),
                    "tool": self._tool(import_executables(plan.info, plan.factor))}
        if op == ASSEMBLE:
            merged = root / "cases" / MERGED_CASE
            files = [*_mesh_files(root, IMPORTED_CASE),
                     *(merged / r for r in sorted(plan.cases.merged))]
            return {**hash_files(root, files), "retype": sha256_json(plan.cases.retype),
                    "ami": sha256_json(plan.cases.ami),
                    "tool": self._tool(ASSEMBLE_EXECUTABLES)}
        merged = root / "cases" / MERGED_CASE
        if op == MESH_VIEWS:
            return {**hash_files(root, _mesh_files(root, MERGED_CASE)),
                    "planes": sha256_json(self._planes()),
                    "tool": self._tool(VIEW_EXECUTABLES)}
        files = [*_mesh_files(root, MERGED_CASE), merged / "system/meshQualityDict",
                 *((merged / AMI_WEIGHTS_DICT,) if coupled_pairs(self.mesh) else ())]
        return {**hash_files(root, files), "tool": self._tool(CHECK_EXECUTABLES)}

    def _planes(self) -> list[tuple[str, str, surface.Point, surface.Point]]:
        """The cuts for the 3D pane: (file name, label, point, normal), through the
        first mesh zone, at its mid-span on the imported surface."""
        folder = self.run.root / "cases" / IMPORTED_CASE
        zones = self.mesh.zones
        points = surface.zone_points(folder, zones[0].cell_zone) if zones else None
        planes = imported_cut_planes(self.run.config, points)
        return [(name, p.name, p.origin, p.normal)
                for name, p in zip((ALONG, ACROSS), planes, strict=False)]

    # --- the run -----------------------------------------------------------------------

    async def run_all(self, force: bool, allow_risk: bool) -> MachineRunResult:
        run, pipe, config = self.run, self.pipe, self.run.config
        run.operations = import_operations(config.physics is not None)
        run.status(VALIDATE)
        run.executed.append(VALIDATE)
        run.issues.extend(check_all(config, pipe.thresholds).issues)
        if has_stopping_issue(run.issues):
            run.mesh = StageResult(StageStatus.SKIPPED, "VALIDATION_FAILED",
                                   "Validation found problems; the mesh was not imported.")
            raise _Stop("Validation found problems that stop the run; no OpenFOAM command "
                        "was started.")
        physics = check_physics(config, (), pipe.case_thresholds)
        run.issues.extend(physics)
        plan = self._plan()
        self.key = plan.digest
        _write(run.root, IMPORTED_CASE, plan.cases.imported)
        _write(run.root, MERGED_CASE, plan.cases.merged)

        ops = (IMPORT, ASSEMBLE, CHECK_MESH, MESH_VIEWS)
        names = {IMPORT: self._import_executables(plan), ASSEMBLE: ASSEMBLE_EXECUTABLES,
                 CHECK_MESH: plan.cases.check_executables(), MESH_VIEWS: VIEW_EXECUTABLES}
        may_run: dict[str, bool] = {}
        for op in ops:
            stale = force or not run.store.check(op, self._inputs(plan, op)).reusable
            may_run[op] = stale or any(may_run.values())
        executables = sorted({e for op in ops if may_run[op] for e in names[op]})
        if executables:
            found = validate_environment(config.openfoam_profile, executables, pipe._env(),
                                         verified_versions=VERIFIED_VERSIONS)
            if has_stopping_issue(found):
                raise _Stop("The OpenFOAM environment is not usable; nothing was run.", *found)
            run.issues.extend(found)
        # The heavy-step lock first, so the free RAM is measured with no other heavy
        # step running (review of M2a); then each step's E0 model, then M1's check.
        if any(may_run.values()):
            await self._take_heavy_lock()
        try:
            if any(may_run.values()):
                self._step_preflight(plan, [op for op in ops if may_run[op]])
            if may_run[IMPORT]:
                self._preflight(plan, allow_risk)
            if any(may_run.values()):
                current = run.root / MESH_REPORT
                if current.is_file():
                    current.replace(run.root / SUPERSEDED_MESH_REPORT)
            await self._operations(plan, ops, force)
        finally:
            self.heavy.release()
        # Every run: the zone radii follow the configured axis and origin, also
        # when the import itself is reused.
        self._report(plan)

        run.status(VALIDATE_MESH)
        run.executed.append(VALIDATE_MESH)
        report_path, succeeded, status = self._validate_mesh(plan)
        write_last_meshed(run.root, run.run_id, config, status)
        message = f"Mesh validation completed with status: {status}."
        run.mesh = (StageResult(StageStatus.SUCCESS, message=message) if succeeded else
                    StageResult(StageStatus.FAILED, "MESH_VALIDATION_FAILED", message))
        if config.physics is None:
            run.case_setup = _NO_PHYSICS
        elif not succeeded:
            run.case_setup = _MESH_NOT_READY
        else:
            run.status(CASE_SETUP)
            # Again with the areas measured from the mesh (a flow-rate inlet).
            known = {(i.code, i.message) for i in physics}
            physics = check_physics(config, (), pipe.case_thresholds,
                                    mesh_areas=self.areas())
            now = {(i.code, i.message) for i in physics}
            # The INFO "measured when it is imported" is answered now.
            run.issues[:] = [i for i in run.issues if i.code != AREA_AFTER_IMPORT
                             or (i.code, i.message) in now]
            run.issues.extend(i for i in physics if (i.code, i.message) not in known)
            stopping = sorted({i.code for i in physics if i.is_stopping})
            if stopping:
                run.case_setup = StageResult(
                    StageStatus.SKIPPED, "PHYSICS_INVALID",
                    f"The physics settings have problems: {', '.join(stopping)}.")
                return pipe._result(run, False, message + " Case setup was not done: the "
                                    "physics settings have problems; the mesh is reused "
                                    "next time.", RunState.FAILED, report=report_path)
            pipe._case_setup(run, (), force)
            message += " The case is set up in case/."
        return pipe._result(run, succeeded, message,
                            RunState.SUCCEEDED if succeeded else RunState.FAILED,
                            report=report_path)

    async def _operations(self, plan: _Plan, ops: tuple[str, ...], force: bool) -> None:
        """Each operation, reused when its record verifies; the heavy-step lock's
        record names the step running."""
        run = self.run
        for op in ops:
            run.status(op)
            inputs = self._inputs(plan, op)
            if not force and run.store.check(op, inputs).reusable:
                run.reused.append(op)
                continue
            self.heavy.describe(run.root, OPERATION_STEPS.get(op, op), self._seconds(op))
            run.store.invalidate(op)  # a failed or cancelled run never leaves a record
            run.executed.append(op)
            if op == MESH_VIEWS:
                made = await self._views()
                if made is not None:
                    run.store.record(op, inputs, made)
                continue
            outputs = await self._execute(plan, op, force)
            run.store.record(op, inputs, outputs)

    def _seconds(self, op: str) -> float | None:
        found = [self.estimates[k].seconds for k in self._step_kinds(op)
                 if k in self.estimates]
        return sum(found) if found else None

    # --- one heavy step at a time, and their resources (M2a) -------------------------------

    async def _take_heavy_lock(self) -> None:
        """The machine-wide heavy-step lock (heavy_lock.py), waiting while another
        project or check holds it; the run says for whom."""
        run = self.run

        def waiting(who: str) -> None:
            run.status(run.stage, message=f"Waiting for another heavy step to finish: {who}.")

        def cancelled() -> bool:
            return run.cancel_event is not None and run.cancel_event.is_set()

        if not await self.heavy.wait(cancelled, waiting):
            raise _Stop("Run cancelled while waiting for another heavy step.", _issue(
                IssueSeverity.ERROR, IssueCategory.TIMEOUT_CANCELLATION,
                IssueStage.RESOURCE_PREFLIGHT, "RUN_CANCELLED",
                "The run was cancelled while it waited for another heavy step.",
                "Start the run again when the other step has finished."),
                state=RunState.CANCELLED)

    def _cells(self, plan: _Plan) -> tuple[int | None, float | None]:
        """Cells and the share of hexahedra: from the last checkMesh when the
        import report says it was of this same file, else the file's own count
        (types unknown). A log of another file is never used (review of M2a)."""
        try:
            report = json.loads((self.run.root / IMPORT_REPORT).read_text("utf-8"))
            same = isinstance(report, dict) and report.get("sha256") == plan.digest
        except (OSError, ValueError):
            same = False
        log = _read(self.run.root / "logs" / MERGED_CASE / CHECK_MESH_LOG) if same else ""
        cells = re.search(r"^\s*cells:\s+(\d+)", log, re.MULTILINE)
        hexes = re.search(r"^\s*hexahedra:\s+(\d+)", log, re.MULTILINE)
        if cells and int(cells[1]):
            return int(cells[1]), (int(hexes[1]) / int(cells[1]) if hexes else None)
        return self._declared_cells(plan), None

    def _step_preflight(self, plan: _Plan, ops: list[str]) -> None:
        """Each heavy step's RAM and time from the E0 models (resources.py),
        against the free RAM: over it BLOCKED, over 70 % a WARNING. Written to
        reports/step_resources.json."""
        run = self.run
        cells, hexes = self._cells(plan)
        free = self.pipe.system_probe(run.root).available_ram_bytes
        history = resources.read_history(run.root)
        estimates = [resources.estimate(kind, cells or 0, hexes, self._file_bytes(plan),
                                        history, self.key)
                     for op in ops for kind in self._step_kinds(op)
                     if cells or kind == "convert"]
        rows = [{**e.as_dict(), "label": resources.LABELS[e.step]} for e in estimates]
        path = run.root / STEP_RESOURCES
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"free_ram_bytes": free, "cells": cells,
                                    "hex_share": hexes, "steps": rows,
                                    "note": resources.NOTE}, indent=2) + "\n",
                        encoding="utf-8")
        self.estimates = {e.step: e for e in estimates}
        if free is None or not estimates:
            return
        worst = max(estimates, key=lambda e: e.ram_bytes)
        needed = f"{worst.ram_bytes / 1e9:.1f} GB of {free / 1e9:.1f} GB free"
        if worst.ram_bytes > free:
            raise _Stop("Resource preflight BLOCKED the run.", _issue(
                IssueSeverity.BLOCKING, IssueCategory.RESOURCE_RISK,
                IssueStage.RESOURCE_PREFLIGHT, "STEP_RAM_BLOCKED",
                f"The {resources.LABELS[worst.step]} needs about {needed} "
                f"({worst.basis}, with a 1.25 margin).",
                "Close other programs or give WSL more memory (.wslconfig), then run "
                "again.", **worst.as_dict()))
        if worst.ram_bytes > resources.WARN_SHARE * free:
            run.issues.append(_issue(
                IssueSeverity.WARNING, IssueCategory.RESOURCE_RISK,
                IssueStage.RESOURCE_PREFLIGHT, "STEP_RAM_HIGH",
                f"The {resources.LABELS[worst.step]} needs about {needed} "
                f"({worst.basis}, with a 1.25 margin).",
                "Avoid other memory-hungry work while it runs.", **worst.as_dict()))

    def areas(self) -> dict[str, float]:
        """Every configured patch's area in m^2, measured from the imported mesh's
        surface (renaming a patch does not change its faces), under its name."""
        return surface.config_areas(self.run.root / "cases" / IMPORTED_CASE, self.run.config)

    # --- preflight -----------------------------------------------------------------------

    def _preflight(self, plan: _Plan, allow_risk: bool) -> None:
        """The resource check on the file's own cell count (preflight_cells)."""
        run, config = self.run, self.run.config
        assert plan.info is not None
        cells, basis = preflight_cells(plan.info)
        path = run.root / PREFLIGHT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        if cells is None:
            path.write_text(json.dumps({"status": "NOT_ASSESSED", "reason": "the file gives no "
                                        "cell or node count"}, indent=2) + "\n",
                            encoding="utf-8")
            run.issues.append(_issue(
                IssueSeverity.INFO, IssueCategory.RESOURCE_RISK, IssueStage.RESOURCE_PREFLIGHT,
                "RESOURCE_NOT_ASSESSED", "The file gives no cell or node count, so the "
                "resource check was not done before converting.",
                "Watch the conversion; the converted mesh's size is in the import report."))
            return
        estimator = self.pipe.estimator
        assessment = estimator.assess_cells(
            CellEstimate(background=cells, surface=0, feature=0, layers=0),
            self.pipe.system_probe(run.root), config.max_global_cells, peak_cells=cells)
        path.write_text(json.dumps({**assessment.as_dict(), "estimated_cells": cells,
                                    "basis": basis}, indent=2) + "\n", encoding="utf-8")
        if assessment.status is ResourceStatus.BLOCKED:
            raise _Stop("Resource preflight BLOCKED the import.", *assessment.issues)
        if assessment.status is ResourceStatus.HIGH_RESOURCE_RISK and not allow_risk:
            raise _Stop(
                "Resource preflight reported HIGH RESOURCE RISK; the import was not started.",
                *assessment.issues,
                _issue(IssueSeverity.BLOCKING, IssueCategory.RESOURCE_RISK,
                       IssueStage.RESOURCE_PREFLIGHT, "HIGH_RESOURCE_RISK_NOT_ACKNOWLEDGED",
                       "Importing needs explicit acknowledgement of the high resource risk.",
                       "Start the run with allow_high_resource_risk to proceed anyway."))
        run.issues.extend(assessment.issues)

    # --- operations ----------------------------------------------------------------------

    async def _execute(self, plan: _Plan, op: str, force: bool) -> list[Path]:
        run = self.run
        if op == IMPORT:
            try:
                return await self._import(plan, force)
            except _Stop:
                # Nothing of a stopped import stays where a later step could read it.
                shutil.rmtree(run.root / "cases" / IMPORTED_CASE / POLY_MESH,
                              ignore_errors=True)
                raise
        if op == ASSEMBLE:
            merged = run.root / "cases" / MERGED_CASE
            for index, step in enumerate(plan.cases.merged_steps(), start=1):
                if step.kind == "copy_mesh":
                    target = merged / POLY_MESH
                    shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(run.root / step.source / POLY_MESH, target,
                                    ignore=shutil.ignore_patterns(*_EXCLUDED))
                    continue
                await self.pipe._command(run, step, index)
            patches, unreadable = read_patches(merged, "merged")
            stopping = [unreadable] if unreadable else [
                i for i in check_patches(patches, self._expectation(plan)) if i.is_stopping]
            if stopping:
                raise _Stop("The assembled mesh is not the intended one.", *stopping)
            return _mesh_files(run.root, MERGED_CASE)
        # checkMesh writes a set only when it has faces: the sets of an earlier
        # check would show problems the mesh no longer has (review of M2a).
        merged = run.root / "cases" / MERGED_CASE
        shutil.rmtree(merged / SETS_DIR, ignore_errors=True)
        shutil.rmtree(merged / POLY_MESH / "sets", ignore_errors=True)
        zero_log = run.root / "logs" / ZERO_CASE / CHECK_MESH_LOG
        zero_log.unlink(missing_ok=True)  # a pass of an earlier check is not this one's
        steps = plan.cases.check_steps(bool(coupled_pairs(self.mesh)))
        logs = [Path((await self._step(step, index, plan.cases.check_kind(step))).log_path)
                for index, step in enumerate(steps, start=1)]
        if quality.needs_zero_pass(_read(logs[0])):
            logs.append(await self._zero_pass())
        return [*logs, self._zone_quality()]

    async def _zero_pass(self) -> Path:
        """checkMesh with ZERO_QUALITY_DICT in cases/merged_zero/ (links to the
        merged mesh): the faces at or below 0 in tet quality and determinant
        (owner, after M2a). The case is removed after; its log stays."""
        case = quality.prepare_zero_case(self.run.root / "cases" / MERGED_CASE,
                                         converter_files())
        try:
            result = await self._step(Step(f"{ZERO_CASE}_checkMesh", "run",
                                           f"cases/{ZERO_CASE}",
                                           ("checkMesh", "-case", ".", "-meshQuality")), 1, None)
        finally:
            shutil.rmtree(case, ignore_errors=True)
        return Path(result.log_path)

    def _zone_quality(self) -> Path:
        """reports/mesh_quality_zones.json: per cell zone, the cells past each
        quality limit and the worst value, from the fields checkMesh wrote; then
        the fields and the region sets are removed (the vtk sets stay)."""
        run = self.run
        merged = run.root / "cases" / MERGED_CASE
        values = dict(re.findall(r"^\s*(\w+)\s+([^;\s]+);", _read(
            merged / "system/meshQualityDict"), re.MULTILINE))
        limits = quality.limits_of(values)
        found = quality.zone_quality(merged, set(quality.FIELDS), limits)
        removed = quality.remove_outputs(merged)
        path = run.root / QUALITY_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "run_id": run.run_id,
            "limits": {kind: {"past": side, "limit": value}
                       for kind, (side, value) in limits.items()},
            "by_zone": {kind: [row.as_dict() for row in rows] for kind, rows in found.items()},
            "note": "cells whose checkMesh field (-writeFields) is past the meshQualityDict "
                    "limit; a cell takes the worst of its faces",
            "sets": f"cases/{MERGED_CASE}/{SETS_DIR}/ (VTK, for ParaView)",
            "removed": removed}, indent=2) + "\n", encoding="utf-8")
        return path

    async def _import(self, plan: _Plan, force: bool) -> list[Path]:
        run = self.run
        assert plan.info is not None and isinstance(self.mesh, ImportedMesh)
        case = run.root / "cases" / IMPORTED_CASE
        shutil.rmtree(case / POLY_MESH, ignore_errors=True)
        (case / "resaved.msh").unlink(missing_ok=True)
        steps = convert_steps(plan.info, self.mesh.source_path, IMPORTED_CASE)
        if force or not self._promote(plan):  # force: convert again, reuse nothing
            for index, step in enumerate(steps, start=1):
                await self._step(step, index, "convert" if step.name.endswith("_convert")
                                 else None)
        scale = scale_step(plan.factor)
        if scale is not None:
            await self.pipe._command(run, scale, len(steps) + 1)
        # The surface only for a mesh the other checks accept; faces in no group
        # are reported with their box, read from it.
        found = check_converted(case, plan.info, run.config, "m")
        if not [i for i in found if i.is_stopping and i.code != UNGROUPED]:
            await self._surface(IMPORTED_CASE)
            found = check_converted(case, plan.info, run.config, "m")
        stopping = [i for i in found if i.is_stopping]
        if stopping:
            raise _Stop("The converted mesh is not usable; it was not kept for reuse. "
                        f"Review logs/{IMPORTED_CASE}/.", *stopping)
        run.issues.extend(found)
        return [*_mesh_files(run.root, IMPORTED_CASE), *surface.outputs(case)]

    async def _step(self, step: Step, index: int, kind: str | None) -> CommandResult:
        """A step, its peak memory measured (the process's VmHWM, read while it
        runs) and recorded for the preflight of this mesh (resources.py)."""
        peaks: list[int] = []
        done = threading.Event()

        def watch(pid: int) -> None:
            def sample() -> None:
                while not done.is_set():
                    found = resources.peak_rss(pid)
                    if found is None:
                        return
                    peaks.append(found)
                    done.wait(0.5)
            threading.Thread(target=sample, daemon=True, name=f"peak-{pid}").start()

        try:
            result = await self.pipe._command(self.run, step, index, on_pid=watch)
        finally:
            done.set()
        if kind is not None and peaks:
            resources.record(self.run.root, self.key, kind, max(peaks),
                             result.duration_seconds)
        return result

    async def _surface(self, case: str) -> None:
        """The boundary surface of cases/<case>/ (surface.py), or stop."""
        run = self.run
        folder = run.root / "cases" / case
        zones = sorted(mesh_zone_names(folder))
        surface.prepare(folder, zones, converter_files())
        log = ""
        for index, step in enumerate(surface.steps(case, zones), start=1):
            log = _read(Path((await self._step(step, index, "surface")).log_path))
        if not surface.index(folder, log):
            raise _Stop(f"The boundary surface of the {case} mesh could not be read.", _issue(
                IssueSeverity.BLOCKING, IssueCategory.EXECUTION, IssueStage.MESH_IMPORT,
                "MESH_SURFACE_UNREADABLE", f"surfaceMeshExtract wrote no surface of the {case} "
                "mesh that the app can read.",
                f"Read logs/{surface.surface_case(case)}/; report the log if it ends normally."))

    async def _views(self) -> list[Path] | None:
        """The merged mesh's surface and the two cuts for the 3D pane. Display
        only: a failure is a WARNING and the run goes on (nothing is recorded, so
        the next run tries again); a cancellation stops it."""
        run = self.run
        folder = run.root / "cases" / MERGED_CASE
        planes = self._planes()
        try:
            await self._surface(MERGED_CASE)
            made: list[Path] = []
            if planes:
                surface.write_cuts(folder, planes)
                await self._step(surface.cut_step(MERGED_CASE), 3, "views")
                made = [surface.surface_dir(folder) / surface.CUTS_RECORD,
                        *(surface.cut_file(folder, name) for name, *_ in planes)]
        except _Stop as stop:
            if stop.state is RunState.CANCELLED:
                raise
            run.issues.append(_issue(
                IssueSeverity.WARNING, IssueCategory.EXECUTION, IssueStage.VISUALIZATION,
                "MESH_VIEW_FAILED", "The 3D view of the mesh could not be made: "
                f"{stop.message}", f"Read logs/{surface.surface_case(MERGED_CASE)}/. The mesh "
                "and its checks are not affected."))
            return None
        return [*surface.outputs(folder), *(p for p in made if p.is_file())]

    def _promote(self, plan: _Plan) -> bool:
        """Move the scratch conversion's polyMesh into cases/imported/ when its
        record says it is this file, converted by this tool at scale 1 (owner
        answer 4: no second conversion). It is checked like any conversion."""
        run = self.run
        assert plan.info is not None
        scratch = run.root / "cases" / SCRATCH_CASE
        expected = scratch_record(plan.info, plan.digest, self._tool(
            (converter(plan.info),)))
        if _scratch(scratch, expected) is None:
            return False
        target = run.root / "cases" / IMPORTED_CASE / POLY_MESH
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(scratch / POLY_MESH), str(target))
        (scratch / SCRATCH_RECORD).unlink()
        logs = run.root / "logs" / IMPORTED_CASE
        logs.mkdir(parents=True, exist_ok=True)
        (logs / PROMOTE_LOG).write_text(
            f"Promoted the scratch conversion of {plan.info.path} (sha256 {plan.digest}, "
            f"{expected['converter']}, scale 1) from cases/{SCRATCH_CASE}/ instead of "
            "converting again. Its conversion log is in "
            f"logs/{SCRATCH_CASE}/.\n", encoding="utf-8")
        run.reused.append(f"{IMPORT}:scratch")
        return True

    def _report(self, plan: _Plan) -> None:
        """reports/mesh_import.json: the converter, the OpenFOAM version, the file
        and what the converted mesh holds (owner answer 3)."""
        run = self.run
        assert plan.info is not None and isinstance(self.mesh, ImportedMesh)
        case = run.root / "cases" / IMPORTED_CASE
        identity = tool_identity(run.config.openfoam_profile,
                                 import_executables(plan.info, plan.factor), self.pipe._env())
        patches, _ = read_patches(case, IMPORTED_CASE)
        radii = {z.name: surface.config_zone_radius(case, z) for z in self.mesh.zones}
        payload = {
            "run_id": run.run_id, "source": str(self.mesh.source_path),
            "sha256": plan.digest, "format": plan.info.format.value if plan.info.format else None,
            "version": plan.info.version, "binary": plan.info.binary,
            "converter": converter(plan.info), "resaved_as_msh22": needs_resave(plan.info),
            "openfoam": {"project": identity["WM_PROJECT"],
                         "version": identity["WM_PROJECT_VERSION"],
                         "executables": identity["executables"]},
            "units": self.mesh.source_units.value, "scale_to_metres": plan.factor,
            "cells": mesh_cells(case), "declared_cells": plan.info.declared_cells,
            "patches": {p.name: {"type": p.patch_type, "faces": p.n_faces} for p in patches},
            # Original name -> name used in the mesh (an AMI patch keeps its own).
            "names_used": used_names(run.config),
            "cell_zones": sorted(mesh_zone_names(case)),
            "zone_radius_m": radii, "zone_radius_note": "computed from the zone: the largest "
            "distance of its points from the rotation axis (on its outer surface, extracted "
            "by surfaceMeshExtract)",
            "patch_area_m2": self.areas(), "patch_area_note": "measured from the mesh: the "
            "sum of the patch's face areas, after renaming and scaling",
            "logs": f"logs/{IMPORTED_CASE}/",
        }
        path = run.root / IMPORT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # --- mesh validation -----------------------------------------------------------------

    def _partial_pairs(self) -> tuple[tuple[str, str], ...]:
        """The cyclicACMI pairs (a machine of several files, M2b-2)."""
        return interfaces.partial(self.mesh) if isinstance(self.mesh, MachineParts) else ()

    DROPS_FACE_ZONES = False  # the option is a machine-parts one (PartsRun)

    def _face_zone_owners(self) -> dict[str, str]:
        """faceZone -> the file it came from (for the INFO on unused zones)."""
        name = getattr(self.mesh, "source_path", None)
        folder = self.run.root / "cases" / IMPORTED_CASE
        return dict.fromkeys(face_zone_names(folder), Path(name).name if name else "the file")

    def _expected_regions(self) -> int | None:
        """How many unconnected regions the mesh should have (None: not checked)."""
        return None

    def _expectation(self, plan: _Plan) -> MeshExpectation:
        return MeshExpectation(
            name="imported", patches=plan.cases.expected,
            cell_zones=tuple(z.cell_zone for z in self.mesh.zones),
            ami_pairs=coupled_pairs(self.mesh))

    def _validate_mesh(self, plan: _Plan) -> tuple[Path, bool, str]:
        run = self.run
        merged = run.root / "cases" / MERGED_CASE
        logs = run.root / "logs" / MERGED_CASE
        check_text = _read(logs / CHECK_MESH_LOG)
        ami_text = _read(logs / AMI_WEIGHTS_LOG)
        metrics = CheckMeshParser().parse_text(check_text, logs / CHECK_MESH_LOG)
        report = MeshQualityValidator().assess(metrics, run.config.quality)
        findings = quality.classify(check_text, _read(
            run.root / "logs" / ZERO_CASE / CHECK_MESH_LOG))
        report = self._two_levels(report, findings)
        expected = self._expectation(plan)
        patches, unreadable = read_patches(merged, "merged")
        extra: list[Issue] = [unreadable] if unreadable else list(check_patches(patches,
                                                                                expected))
        extra += check_zones(merged, expected.cell_zones, expected.name)
        extra += check_ami_pairing(merged)
        if expected.ami_pairs:
            partial = self._partial_pairs()
            ranged = tuple(p for p in expected.ami_pairs if p not in partial)
            extra += [self._ami_detail(i) for i in check_ami_weights(
                ami_text, ranged, self.pipe.thresholds.imported_ami_weights_range,
                when=AT_REST)]
            extra += interfaces.weights_notes(partial, ami_text, AT_REST)
        baffles = _read(run.root / "logs" / IMPORTED_CASE / BAFFLES_LOG)
        extra += check_baffles(check_text) or check_baffles(baffles)
        extra += unused_face_zones(check_text, self._face_zone_owners(),
                                   can_drop=self.DROPS_FACE_ZONES)
        regions, expected_regions = region_count(check_text), self._expected_regions()
        if expected_regions is not None and regions is not None and regions != expected_regions:
            extra.append(_issue(
                IssueSeverity.BLOCKING, IssueCategory.MESHING, IssueStage.MESH_VALIDATION,
                "MACHINE_REGIONS", f"The assembled mesh has {regions} separate regions; its "
                f"{expected_regions} part(s) should give {expected_regions}.",
                f"Read logs/{MERGED_CASE}/{CHECK_MESH_LOG} and each part's checkMesh log "
                "(logs/part_<prefix>/): a part is in pieces.", found=regions,
                expected=expected_regions))
        if any(f.level == quality.BLOCKING for f in findings) and \
                report.validity is not ValidityStatus.INVALID:
            # Never reached: a BLOCKING finding keeps CHECKMESH_FAILURE (owner review).
            report = replace(report, validity=ValidityStatus.INVALID, status="failed")
        if any(i.is_stopping for i in extra):
            report = replace(report, validity=ValidityStatus.INVALID, status="failed")
        issues = (*report.issues, *extra)
        # An unused faceZone takes no part in the case: noted, not a reason to review.
        to_review = [i for i in issues if i.code != "MSH_UNUSED_FACE_ZONE"]
        report = replace(report, issues=issues, status=(
            "failed" if report.status == "failed" or has_stopping_issue(issues)
            else "review" if to_review else "passed"))
        path = run.root / MESH_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = report.as_dict()
        payload["run_id"] = run.run_id
        payload["config_sha256"] = sha256_json(run.config.model_dump(mode="json"))
        payload["regions"] = {"expected": expected_regions, "found": regions}
        payload["ami_weights"] = [asdict(w) for w in parse_ami_weights(ami_text)]
        payload["ami_weights_when"] = AT_REST
        payload["ami_weights_range"] = list(self.pipe.thresholds.imported_ami_weights_range)
        partial = self._partial_pairs()
        if partial:  # M2b-2: their weights are an INFO, not ranged
            payload["acmi_pairs"] = [list(p) for p in partial]
        payload["imported"] = {"report": IMPORT_REPORT, "converter": self._converter(plan)}
        payload["checkmesh_findings"] = [asdict(f) for f in findings]
        payload["quality_by_zone"] = self._by_zone()
        payload["quality_sets"] = f"cases/{MERGED_CASE}/{SETS_DIR}/"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        run.issues.extend(report.issues)
        return path, report.status != "failed", report.status


    def _by_zone(self) -> dict[str, Any]:
        try:
            found = json.loads((self.run.root / QUALITY_REPORT).read_text("utf-8"))
            return dict(found["by_zone"])
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def _two_levels(self, report: Any, findings: tuple[quality.Finding, ...]) -> Any:
        """checkMesh's failed checks in two levels (owner, M2a): without a
        BLOCKING one the mesh is valid and each quality class is a WARNING
        with its cells per zone; with one, CHECKMESH_FAILURE names them."""
        if not findings:
            return report
        blocking = [f for f in findings if f.level == quality.BLOCKING]
        by_zone = self._by_zone()
        warnings = []
        for kind in dict.fromkeys(f.kind for f in findings if f.level == quality.WARNING):
            texts = [f.text for f in findings if f.kind == kind]
            rows = by_zone.get(kind) or []
            where = "; ".join(
                f"{r['zone']} {r['cells_past_limit']:,} of {r['cells']:,} cells "
                f"({r['percent']:.3g} %), worst {r['worst']:.4g}"
                for r in rows if r["cells_past_limit"]) if rows else ""
            warnings.append(_issue(
                IssueSeverity.WARNING, IssueCategory.MESH_QUALITY, IssueStage.MESH_VALIDATION,
                "MESH_QUALITY_WARNING", f"Mesh quality, {kind}: {'; '.join(texts)}."
                + (f" By cell zone: {where}." if where else
                   " No per-zone field for this check: checkMesh's face count only."
                   if kind not in quality.FIELDS else ""),
                f"Open cases/{MERGED_CASE}/{SETS_DIR}/ in ParaView for the faces and cells; "
                "improve the mesh there if the solver struggles. An imported mesh is not "
                "stopped for quality alone (owner, M2a).", kind=kind, checkmesh=texts,
                by_zone=rows))
        others = tuple(i for i in report.issues if i.code != "CHECKMESH_FAILURE")
        if not blocking and report.metrics.recognized:
            issues = (*others, *warnings)
            return replace(report, validity=ValidityStatus.VALID, quality=QualityStatus.REVIEW,
                           issues=issues, status=("failed" if has_stopping_issue(issues)
                                                  else "review"))
        failure = next((i for i in report.issues if i.code == "CHECKMESH_FAILURE"), None)
        if failure is not None:
            kinds = ", ".join(dict.fromkeys(f.kind for f in blocking))
            failure = replace(failure, message=(
                f"checkMesh reported {report.metrics.failed_checks} failed mesh check(s); "
                f"BLOCKING: {kinds}."), details={
                **failure.details, "blocking": [asdict(f) for f in blocking]})
        return replace(report, issues=(*others, *((failure,) if failure else ()), *warnings))

    def _ami_detail(self, issue: Issue) -> Issue:
        """An AMI weights WARNING with where each side falls short (ami_detail.py)."""
        pair = issue.details.get("pair")
        zones = self.mesh.zones
        if issue.code != "AMI_WEIGHTS_OUT_OF_RANGE" or not pair or not zones:
            return issue
        zone = zones[0]
        low = self.pipe.thresholds.imported_ami_weights_range[0]
        sides = pair_details(self.run.root / "cases" / MERGED_CASE, (pair[0], pair[1]), low,
                             (zone.origin.x, zone.origin.y, zone.origin.z),
                             surface.axis_vector(zone.axis))
        if not sides:
            return issue
        text = " ".join(s.sentence(low, zone.axis.value) for s in sides)
        return replace(issue, message=f"{issue.message} {text}",
                       details={**issue.details, "sides": [s.as_dict() for s in sides]})


# --- the scratch conversion (owner answer 4) ------------------------------------------------

def scratch_record(info: MeshFileInfo, digest: str, tool: str) -> dict[str, Any]:
    return {"source": str(info.path), "sha256": digest, "converter": converter(info),
            "resave": needs_resave(info), "tool": tool, "scale": 1.0}


Box = tuple[tuple[float, float, float], tuple[float, float, float]]


def _scratch(case: Path, expected: Mapping[str, Any]) -> Box | None:
    """The box of the scratch conversion in `case` when its record is `expected`
    and its mesh is there; None otherwise."""
    try:
        found = json.loads((case / SCRATCH_RECORD).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict):
        return None
    box = found.pop("box", None)
    if found != dict(expected) or not (case / POLY_MESH / "boundary").is_file():
        return None
    try:
        (x0, y0, z0), (x1, y1, z1) = box
        return (float(x0), float(y0), float(z0)), (float(x1), float(y1), float(z1))
    except (TypeError, ValueError):
        return None


def scratch_box(root: Path, info: MeshFileInfo, digest: str, tool: str) -> Box | None:
    """The box "from the converted mesh" of a scratch conversion of this file."""
    return _scratch(root / "cases" / SCRATCH_CASE, scratch_record(info, digest, tool))


@dataclass(frozen=True)
class ScratchResult:
    """A conversion at scale 1 made to show the box: never a verified artifact."""

    box: Box | None
    converter: str
    log: Path | None
    issues: tuple[Issue, ...] = ()


async def convert_scratch(root: Path, info: MeshFileInfo, digest: str, *,
                          runner: OpenFOAMRunner, env: Mapping[str, str] | None,
                          tool: str, name: str = SCRATCH_CASE) -> ScratchResult:
    """Convert at scale 1 into cases/<name>/ (imported_scratch, or a part's
    conversion, M2b) and read the box of the converted mesh ("from the
    converted mesh"). The caller holds the run lock."""
    case = root / "cases" / name
    shutil.rmtree(case, ignore_errors=True)
    for relative, body in converter_files().items():
        OpenFOAMFileWriter.write_if_changed(case / relative, body)
    logs = root / "logs" / name
    log: Path | None = None
    for index, step in enumerate(convert_steps(info, info.path, name), start=1):
        result = await runner.run_step(
            _meshing_step(step, index, name), case_root=case, env=env, logs_dir=logs)
        log = Path(result.log_path)
        if result.status is not RunStatus.SUCCESS:
            return ScratchResult(None, converter(info), log, (*result.issues, _issue(
                IssueSeverity.BLOCKING, IssueCategory.EXECUTION, IssueStage.MESH_IMPORT,
                "MSH_CONVERT_FAILED", f"{step.argv[0]} failed on {info.path.name}.",
                f"Read logs/{name}/; export the mesh again.",
                log=str(log), tail=list(result.stderr_tail or result.tail)[-20:])))
    box = await _scratch_surface(root, case, runner=runner, env=env, name=name)
    if box is None:
        return ScratchResult(None, converter(info), log, (_issue(
            IssueSeverity.BLOCKING, IssueCategory.EXECUTION, IssueStage.MESH_IMPORT,
            "MSH_CONVERT_FAILED", "The converter wrote no mesh whose boundary could be read.",
            f"Read logs/{name}/ and logs/{surface.surface_case(name)}/; "
            "export the mesh again.", log=str(log)),))
    low, high = box
    record = {**scratch_record(info, digest, tool), "box": [list(low), list(high)]}
    (case / SCRATCH_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return ScratchResult(box, converter(info), log)


async def _scratch_surface(root: Path, case: Path, *, runner: OpenFOAMRunner,
                           env: Mapping[str, str] | None, name: str = SCRATCH_CASE,
                           ) -> Box | None:
    """The surface of the scratch conversion and the box of its boundary
    points (the mesh's box); None if either cannot be made."""
    if not has_mesh(case):
        return None
    zones = sorted(mesh_zone_names(case))
    surface.prepare(case, zones, converter_files())
    made = surface.surface_case(name)
    text = ""
    for index, step in enumerate(surface.steps(name, zones), start=1):
        result = await runner.run_step(_meshing_step(step, index, made),
                                       case_root=root / "cases" / made, env=env,
                                       logs_dir=root / "logs" / made)
        if result.status is not RunStatus.SUCCESS:
            return None
        text = _read(Path(result.log_path))
    return surface.mesh_box(case) if surface.index(case, text) else None


def converter_files() -> dict[str, str]:
    """controlDict (binary), fvSchemes, fvSolution for the converter and the
    surface cases (no quality settings)."""
    return {"system/controlDict": binary_control_dict(), "system/fvSchemes": _fv_schemes(),
            "system/fvSolution": _fv_solution()}


_converter_files = converter_files  # its M2a name (revolution.py, the real tests)


def _meshing_step(step: Step, index: int, case: str) -> MeshingStep:
    name = step.name.removeprefix(case + "_")
    return MeshingStep(step.argv, f"{index:02d}_{name}.log",
                       _STAGE.get(Path(step.argv[0]).name, IssueStage.MESH_IMPORT))
