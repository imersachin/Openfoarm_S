"""The run of an imported-mesh project (M1-MSH; docs/msh_import_notes.md).

    validate (the file and the configuration, before any OpenFOAM command)
    -> import: re-save (binary Gmsh), convert at scale 1, transformPoints to
       metres, the checks on what the converter wrote (cases/imported/)
    -> assemble: copy, createPatch (renamed patches), foamDictionary (AMI
       pairs under their own names, patch types) (cases/merged/)
    -> checkMesh, AMI weights -> mesh validation -> case setup (with physics)

The same run lock, status file, run record, artifact manifest, environment
check and resource preflight as an STL project (MachinePipeline). Each step
is reused only when its recorded inputs and outputs verify: the import on the
file's content hash, the converter, the scale and the tool; the assembly on
the imported mesh and the patch settings, so a patch-type change never
converts again, and a physics change re-runs case setup only (invariant 14).
A converter's exit code 0 is not success: the import checks the mesh it
wrote, and checkMesh's "Failed N mesh checks" fails the mesh whatever the
exit code (owner review of E0).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from case_setup.validation import check_physics
from core.artifacts.hashing import files_under, hash_files, sha256_json
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from machines.assembly import AMI_WEIGHTS_DICT, Step
from machines.case_generator import _control_dict, _fv_schemes, _fv_solution
from machines.mesh_folder import (
    MeshExpectation,
    check_ami_pairing,
    check_ami_weights,
    check_patches,
    parse_ami_weights,
    read_patches,
    region_count,
)
from machines.msh_import.cases import (
    IMPORTED_CASE,
    MERGED_CASE,
    SCRATCH_CASE,
    ImportCases,
    convert_steps,
    converter,
    import_cases,
    needs_resave,
    scale_step,
    used_names,
)
from machines.msh_import.checks import (
    check_baffles,
    check_converted,
    check_zones,
    mesh_cells,
    mesh_zone_names,
    patch_areas,
    zone_radius,
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
from machines.project_store import write_last_meshed
from machines.validation import check_all
from mesh.estimator import CellEstimate, ResourceStatus
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityValidator, ValidityStatus
from openfoam.commands import MeshingStep
from openfoam.dictionary import OpenFOAMFileWriter
from openfoam.environment import tool_identity, validate_environment
from openfoam.runner import OpenFOAMRunner, RunStatus
from vawt.status import RunState
from visualization.foam_reader import FoamReadError, read_points

IMPORT = "machine_import_mesh"
IMPORT_REPORT = "reports/mesh_import.json"
AREA_AFTER_IMPORT = "INLET_AREA_AFTER_IMPORT"  # case_setup.validation
# postProcess AMIWeights reads the mesh as converted: the rotor not turned.
AT_REST = "at rest (0 deg)"
SCRATCH_RECORD = "scratch.json"  # in cases/imported_scratch/
PROMOTE_LOG = "00_promote.log"
CONVERTERS = ("fluent3DMeshToFoam", "gmshToFoam")
IMPORT_EXECUTABLES = (*CONVERTERS, "transformPoints")
ASSEMBLE_EXECUTABLES = ("createPatch", "foamDictionary")
CHECK_EXECUTABLES = ("checkMesh", "postProcess")
# Heuristic only: tetrahedra per node of a Gmsh mesh, for the preflight when the
# file declares no cell count (a tetrahedral mesh has about 5-6 per node).
CELLS_PER_GMSH_NODE = 6
_GENERATED = ("system/createPatchDict", AMI_WEIGHTS_DICT)  # removed when not written
_STAGE = {"fluent3DMeshToFoam": IssueStage.MESH_IMPORT, "gmshToFoam": IssueStage.MESH_IMPORT,
          "transformPoints": IssueStage.MESH_IMPORT, "createPatch": IssueStage.PATCH_CREATION,
          "foamDictionary": IssueStage.PATCH_CREATION, "checkMesh": IssueStage.CHECK_MESH,
          "postProcess": IssueStage.MESH_VALIDATION}


def import_operations(physics: bool) -> tuple[str, ...]:
    return (VALIDATE, IMPORT, ASSEMBLE, CHECK_MESH, VALIDATE_MESH,
            *((CASE_SETUP,) if physics else ()))


def import_executables(info: MeshFileInfo, factor: float) -> tuple[str, ...]:
    return (converter(info), *(("transformPoints",) if factor != 1.0 else ()))


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
    info: MeshFileInfo
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
        self.mesh = mesh

    # --- cache inputs ------------------------------------------------------------------

    def _tool(self, names: tuple[str, ...]) -> str:
        return self.pipe._tool(self.run.config, names)

    def _inputs(self, plan: _Plan, op: str) -> dict[str, str]:
        root = self.run.root
        if op == IMPORT:
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
        files = [*_mesh_files(root, MERGED_CASE), merged / "system/meshQualityDict",
                 *((merged / AMI_WEIGHTS_DICT,) if self.mesh.ami_pairs else ())]
        return {**hash_files(root, files), "tool": self._tool(CHECK_EXECUTABLES)}

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
        info, digest = read_mesh_file(self.mesh.source_path)
        assert digest is not None  # validation: the file is readable and unchanged
        plan = _Plan(info, digest, self.mesh.source_units.to_metres, import_cases(config))
        _write(run.root, IMPORTED_CASE, plan.cases.imported)
        _write(run.root, MERGED_CASE, plan.cases.merged)

        ops = (IMPORT, ASSEMBLE, CHECK_MESH)
        names = {IMPORT: import_executables(info, plan.factor), ASSEMBLE: ASSEMBLE_EXECUTABLES,
                 CHECK_MESH: CHECK_EXECUTABLES}
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
        if may_run[IMPORT]:
            self._preflight(info, allow_risk)
        if any(may_run.values()):
            current = run.root / MESH_REPORT
            if current.is_file():
                current.replace(run.root / SUPERSEDED_MESH_REPORT)
        for op in ops:
            run.status(op)
            inputs = self._inputs(plan, op)
            if not force and run.store.check(op, inputs).reusable:
                run.reused.append(op)
                continue
            run.store.invalidate(op)  # a failed or cancelled run never leaves a record
            run.executed.append(op)
            outputs = await self._execute(plan, op, force)
            run.store.record(op, inputs, outputs)
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

    def areas(self) -> dict[str, float]:
        """Every configured patch's area in m^2, measured from the assembled mesh."""
        return patch_areas(self.run.root / "cases" / MERGED_CASE,
                           [p.name for p in self.run.config.patches])

    # --- preflight -----------------------------------------------------------------------

    def _preflight(self, info: MeshFileInfo, allow_risk: bool) -> None:
        """The resource check on the file's own cell count (preflight_cells)."""
        run, config = self.run, self.run.config
        cells, basis = preflight_cells(info)
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
        steps = plan.cases.check_steps(bool(self.mesh.ami_pairs))
        return [Path((await self.pipe._command(run, step, index)).log_path)
                for index, step in enumerate(steps, start=1)]

    async def _import(self, plan: _Plan, force: bool) -> list[Path]:
        run = self.run
        case = run.root / "cases" / IMPORTED_CASE
        shutil.rmtree(case / POLY_MESH, ignore_errors=True)
        (case / "resaved.msh").unlink(missing_ok=True)
        steps = convert_steps(plan.info, self.mesh.source_path, IMPORTED_CASE)
        if force or not self._promote(plan):  # force: convert again, reuse nothing
            for index, step in enumerate(steps, start=1):
                await self.pipe._command(run, step, index)
        scale = scale_step(plan.factor)
        if scale is not None:
            await self.pipe._command(run, scale, len(steps) + 1)
        found = check_converted(case, plan.info, run.config, "m")
        stopping = [i for i in found if i.is_stopping]
        if stopping:
            raise _Stop("The converted mesh is not usable; it was not kept for reuse. "
                        f"Review logs/{IMPORTED_CASE}/.", *stopping)
        run.issues.extend(found)
        return _mesh_files(run.root, IMPORTED_CASE)

    def _promote(self, plan: _Plan) -> bool:
        """Move the scratch conversion's polyMesh into cases/imported/ when its
        record says it is this file, converted by this tool at scale 1 (owner
        answer 4: no second conversion). It is checked like any conversion."""
        run = self.run
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
        case = run.root / "cases" / IMPORTED_CASE
        identity = tool_identity(run.config.openfoam_profile,
                                 import_executables(plan.info, plan.factor), self.pipe._env())
        patches, _ = read_patches(case, IMPORTED_CASE)
        radii = {z.name: zone_radius(case, z) for z in self.mesh.zones}
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
            "distance of its points from the rotation axis",
            "patch_area_m2": self.areas(), "patch_area_note": "measured from the mesh: the "
            "sum of the patch's face areas, after renaming and scaling",
            "logs": f"logs/{IMPORTED_CASE}/",
        }
        path = run.root / IMPORT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # --- mesh validation -----------------------------------------------------------------

    def _expectation(self, plan: _Plan) -> MeshExpectation:
        return MeshExpectation(
            name="imported", patches=plan.cases.expected,
            cell_zones=tuple(z.cell_zone for z in self.mesh.zones),
            ami_pairs=tuple((p.first, p.second) for p in self.mesh.ami_pairs))

    def _validate_mesh(self, plan: _Plan) -> tuple[Path, bool, str]:
        run = self.run
        merged = run.root / "cases" / MERGED_CASE
        logs = run.root / "logs" / MERGED_CASE
        check_text = _read(logs / CHECK_MESH_LOG)
        ami_text = _read(logs / AMI_WEIGHTS_LOG)
        metrics = CheckMeshParser().parse_text(check_text, logs / CHECK_MESH_LOG)
        report = MeshQualityValidator().assess(metrics, run.config.quality)
        expected = self._expectation(plan)
        patches, unreadable = read_patches(merged, "merged")
        extra: list[Issue] = [unreadable] if unreadable else list(check_patches(patches,
                                                                                expected))
        extra += check_zones(merged, expected.cell_zones, expected.name)
        extra += check_ami_pairing(merged)
        if expected.ami_pairs:
            extra += check_ami_weights(ami_text, expected.ami_pairs,
                                       self.pipe.thresholds.imported_ami_weights_range,
                                       when=AT_REST)
        extra += check_baffles(check_text)
        if metrics.failed_checks and report.validity is not ValidityStatus.INVALID:
            # Never reached with the current validator; kept explicit (owner review).
            report = replace(report, validity=ValidityStatus.INVALID, status="failed")
        if any(i.is_stopping for i in extra):
            report = replace(report, validity=ValidityStatus.INVALID, status="failed")
        report = replace(report, issues=(*report.issues, *extra))
        path = run.root / MESH_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = report.as_dict()
        payload["run_id"] = run.run_id
        payload["config_sha256"] = sha256_json(run.config.model_dump(mode="json"))
        payload["regions"] = {"expected": None, "found": region_count(check_text)}
        payload["ami_weights"] = [asdict(w) for w in parse_ami_weights(ami_text)]
        payload["ami_weights_when"] = AT_REST
        payload["ami_weights_range"] = list(self.pipe.thresholds.imported_ami_weights_range)
        payload["imported"] = {"report": IMPORT_REPORT, "converter": converter(plan.info)}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        run.issues.extend(report.issues)
        return path, report.status != "failed", report.status


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
                          tool: str) -> ScratchResult:
    """Convert at scale 1 into cases/imported_scratch/ and read the box of the
    converted mesh ("from the converted mesh"). The caller holds the run lock."""
    case = root / "cases" / SCRATCH_CASE
    shutil.rmtree(case, ignore_errors=True)
    for relative, body in _converter_files().items():
        OpenFOAMFileWriter.write_if_changed(case / relative, body)
    logs = root / "logs" / SCRATCH_CASE
    log: Path | None = None
    for index, step in enumerate(convert_steps(info, info.path, SCRATCH_CASE), start=1):
        result = await runner.run_step(
            _meshing_step(step, index, SCRATCH_CASE), case_root=case, env=env, logs_dir=logs)
        log = Path(result.log_path)
        if result.status is not RunStatus.SUCCESS:
            return ScratchResult(None, converter(info), log, (*result.issues, _issue(
                IssueSeverity.BLOCKING, IssueCategory.EXECUTION, IssueStage.MESH_IMPORT,
                "MSH_CONVERT_FAILED", f"{step.argv[0]} failed on {info.path.name}.",
                f"Read logs/{SCRATCH_CASE}/; export the mesh again.",
                log=str(log), tail=list(result.stderr_tail or result.tail)[-20:])))
    try:
        points = read_points(case / POLY_MESH / "points")
    except FoamReadError as error:
        return ScratchResult(None, converter(info), log, (_issue(
            IssueSeverity.BLOCKING, IssueCategory.EXECUTION, IssueStage.MESH_IMPORT,
            "MSH_CONVERT_FAILED", f"The converter wrote no readable mesh: {error}",
            f"Read logs/{SCRATCH_CASE}/; export the mesh again.", log=str(log)),))
    low, high = points.min(axis=0).tolist(), points.max(axis=0).tolist()
    record = {**scratch_record(info, digest, tool), "box": [low, high]}
    (case / SCRATCH_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return ScratchResult(((low[0], low[1], low[2]), (high[0], high[1], high[2])),
                         converter(info), log)


def _converter_files() -> dict[str, str]:
    """controlDict, fvSchemes, fvSolution for the converter (no quality settings)."""
    return {"system/controlDict": _control_dict(), "system/fvSchemes": _fv_schemes(),
            "system/fvSolution": _fv_solution()}


def _meshing_step(step: Step, index: int, case: str) -> MeshingStep:
    name = step.name.removeprefix(case + "_")
    return MeshingStep(step.argv, f"{index:02d}_{name}.log",
                       _STAGE.get(Path(step.argv[0]).name, IssueStage.MESH_IMPORT))
