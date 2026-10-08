"""VAWT meshing pipeline (spec sections 9-12; method A, proven in V0 and V2).

One run under the project run lock:

    validate (gate) -> geometry artifact -> dictionaries
    -> environment check and resource preflight (when meshing may run)
    -> outer mesh / rotor features / rotor mesh / single mesh / assembly
    -> checkMesh -> mesh validation

Each OpenFOAM operation is cached in the project's artifact manifest and is
reused only when its recorded inputs and outputs verify (same rule as the
generic pipeline). After each meshing operation its result is checked (V0
R3/R4): an operation whose check fails is not recorded, and its previous
record was removed before it ran, so the mesh is never reused. Commands run
from their sub-case directory (V0 R9); logs go to logs/<sub-case>/. The run
writes .preprocessor/status.json at every stage boundary and a run record.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.artifacts import ArtifactStore
from core.artifacts.hashing import files_under, hash_files, sha256_file, sha256_json
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from core.version import APP_VERSION
from core.workflow.run_lock import RunLock
from core.workflow.run_records import write_run_record
from geometry.transformer import ARTIFACT_FORMAT_VERSION, GeometryTransform, write_stl_artifact
from geometry.validator import TrimeshGeometryValidator
from mesh.estimator import ResourceEstimator, ResourceStatus, SystemResources
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityValidator, ValidityStatus
from openfoam.commands import (
    MeshingStep,
    block_mesh_step,
    check_mesh_step,
    create_patch_step,
    feature_extraction_step,
    merge_meshes_step,
    snappy_step,
    topo_set_step,
)
from openfoam.environment import tool_identity, validate_environment
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus
from vawt.case_generator import (
    AMI_PATCHES,
    CASES_DIR,
    CELL_ZONE_NAME,
    INTERFACE_OUTER,
    INTERFACE_ROTOR,
    MERGED,
    OUTER,
    ROTOR,
    ZONE_SURFACE,
    VawtCaseGenerator,
    case_layout,
)
from vawt.config import InterfaceType, VawtProjectConfig
from vawt.operations import DOWNSTREAM, VawtOperation, feature_case, operations_for
from vawt.preflight import assess
from vawt.rotor_metrics import compute_rotor_metrics
from vawt.status import RunState, write_status
from vawt.validation import ValidationThresholds, check_config, load_rotor
from visualization.foam_reader import FoamReadError, read_boundary

Op = VawtOperation

GEOMETRY_REPORT = "reports/geometry_report.json"
PREFLIGHT_REPORT = "reports/resource_preflight.json"
MESH_REPORT = "reports/mesh_quality_report.json"
SUPERSEDED_MESH_REPORT = "reports/mesh_quality_report.superseded.json"
POLY_MESH = "constant/polyMesh"
_EXCLUDED = frozenset({"sets"})  # checkMesh diagnostics, not part of the mesh
_REGIONS = re.compile(r"Number of regions:\s*(\d+)")

_EXECUTABLES: Mapping[VawtOperation, tuple[str, ...]] = {
    Op.OUTER_MESH: ("blockMesh", "snappyHexMesh"),
    Op.ROTOR_FEATURES: ("surfaceFeatureExtract",),
    Op.ROTOR_MESH: ("blockMesh", "snappyHexMesh", "topoSet"),
    Op.SINGLE_MESH: ("blockMesh", "snappyHexMesh"),
    Op.ASSEMBLE: ("mergeMeshes", "createPatch"),
    Op.CHECK_MESH: ("checkMesh",),
}
_MESHING = frozenset({Op.OUTER_MESH, Op.ROTOR_MESH, Op.SINGLE_MESH})


@dataclass(frozen=True)
class VawtRunResult:
    succeeded: bool
    message: str
    run_id: str
    state: RunState
    issues: tuple[Issue, ...] = ()
    executed: tuple[VawtOperation, ...] = ()
    reused: tuple[VawtOperation, ...] = ()
    final_case: str | None = None
    mesh_report_path: Path | None = None
    run_record_path: Path | None = None


class _Stop(Exception):
    """Ends the run early with a result (failure or cancellation)."""

    def __init__(self, message: str, *issues: Issue,
                 state: RunState = RunState.FAILED) -> None:
        super().__init__(message)
        self.message, self.issues, self.state = message, issues, state


def expected_regions(config: VawtProjectConfig) -> int:
    """checkMesh region count of a correct final mesh.

    AMI meshes have two regions joined only by the cyclicAMI pair (V0 E1:
    487,630 + 121,296 cells = the outer and rotor meshes). Every other layout
    is one connected region (V0 E2a, E1 outer and rotor).
    """
    layout = case_layout(config)
    return 2 if OUTER in layout.sub_cases else 1


def _issue(severity: IssueSeverity, category: IssueCategory, stage: IssueStage, code: str,
           message: str, action: str, *, log: Path | None = None,
           explanation: str = "", **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=stage, code=code,
                 message=message, explanation=explanation, suggested_action=action,
                 log_reference=str(log) if log else None, details=dict(details))


def _patch_faces(case: Path) -> dict[str, int]:
    try:
        return {p.name: p.n_faces for p in read_boundary(case / POLY_MESH / "boundary")}
    except FoamReadError:
        return {}


def _zone_names(case: Path, kind: str) -> set[str]:
    """Names in constant/polyMesh/cellZones or faceZones (empty if absent)."""
    path = case / POLY_MESH / kind
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"^\s*([A-Za-z_]\w*)\s*\n\s*\{", text, re.MULTILINE)) - {"FoamFile"}


@dataclass
class _Run:
    """State of one run."""

    root: Path
    config: VawtProjectConfig
    run_id: str
    started_at: str
    operations: tuple[VawtOperation, ...]
    store: ArtifactStore
    cancel_event: asyncio.Event | None
    executed: list[VawtOperation] = field(default_factory=list)
    reused: list[VawtOperation] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    commands: list[CommandResult] = field(default_factory=list)
    stage: str = "starting"
    step: int = 0  # 1-based index of the last operation reached; 0 before the first

    def case(self, name: str) -> Path:
        return self.root / CASES_DIR / name

    def status(self, operation: VawtOperation | str, state: RunState = RunState.RUNNING,
               message: str = "") -> None:
        name = operation.value if isinstance(operation, VawtOperation) else operation
        self.stage = name
        if isinstance(operation, VawtOperation) and operation in self.operations:
            self.step = self.operations.index(operation) + 1
        write_status(self.root, run_id=self.run_id, state=state, stage=name, step=self.step,
                     total=len(self.operations), started_at=self.started_at, message=message)


class VawtPipeline:
    def __init__(
        self,
        runner: OpenFOAMRunner | None = None,
        environment: Mapping[str, str] | None = None,
        estimator: ResourceEstimator | None = None,
        system_probe: Callable[[Path], SystemResources] | None = None,
        thresholds: ValidationThresholds | None = None,
    ) -> None:
        self.runner = runner or OpenFOAMRunner()
        self.environment = environment  # None: the process environment at run time
        self.estimator = estimator or ResourceEstimator()
        self.system_probe = system_probe or SystemResources.detect
        self.thresholds = thresholds or ValidationThresholds()
        self.generator = VawtCaseGenerator()
        self.geometry_validator = TrimeshGeometryValidator()

    def _env(self) -> Mapping[str, str]:
        return self.environment if self.environment is not None else os.environ

    def _tool(self, config: VawtProjectConfig, *executables: str) -> str:
        return sha256_json(tool_identity(config.openfoam_profile, executables, self._env()))

    # --- public entry points -------------------------------------------------------

    def run_sync(self, root: Path, config: VawtProjectConfig, **kwargs: Any) -> VawtRunResult:
        return asyncio.run(self.run(root, config, **kwargs))

    async def run(
        self,
        root: Path,
        config: VawtProjectConfig,
        *,
        force: bool = False,
        allow_high_resource_risk: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> VawtRunResult:
        """Mesh the configuration, re-running only what is not verifiably current.

        force=True ignores every cached result. HIGH RESOURCE RISK stops unless
        allow_high_resource_risk; BLOCKED always stops. Setting cancel_event
        stops the running command and starts no further step.
        """
        lock = RunLock(root)
        busy = lock.acquire()
        run_id = uuid.uuid4().hex
        if busy is not None:  # another run owns the status file; leave it alone
            return VawtRunResult(False, "Another run is in progress for this project.",
                                 run_id, RunState.FAILED, issues=(busy,))
        clock = time.monotonic()
        run = _Run(root, config, run_id, datetime.now(UTC).isoformat(),
                   operations_for(config), ArtifactStore(root), cancel_event)
        result: VawtRunResult | None = None
        try:
            run.status("starting")
            try:
                result = await self._run(run, force, allow_high_resource_risk)
            except _Stop as stop:
                result = self._result(run, False, stop.message, stop.state, stop.issues)
            run.status(run.stage, result.state, result.message)
            return replace(result, run_record_path=self._record(run, result, clock, None))
        except BaseException as error:  # keep evidence of unexpected failures
            if result is None:
                run.status(run.stage, RunState.FAILED, f"Internal error: {error!r}")
                self._record(run, None, clock, error)
            raise
        finally:
            lock.release()

    # --- the run -----------------------------------------------------------------------

    def _result(self, run: _Run, succeeded: bool, message: str, state: RunState,
                extra: Sequence[Issue] = (), report: Path | None = None) -> VawtRunResult:
        return VawtRunResult(
            succeeded=succeeded, message=message, run_id=run.run_id, state=state,
            issues=(*run.issues, *extra), executed=tuple(run.executed),
            reused=tuple(run.reused), final_case=case_layout(run.config).final,
            mesh_report_path=report,
        )

    async def _run(self, run: _Run, force: bool, allow_risk: bool) -> VawtRunResult:
        config = run.config
        layout = case_layout(config)

        # 1. Validation gate: ERROR or BLOCKING stops before any OpenFOAM command.
        run.status(Op.VALIDATE)
        mesh, geometry_issues = load_rotor(config)
        run.executed.append(Op.VALIDATE)
        run.issues.extend(geometry_issues)
        if mesh is None:
            raise _Stop("Geometry could not be used; no OpenFOAM command was started.")
        metrics = compute_rotor_metrics(mesh.vertices, config.rotor.axis)
        run.issues.extend(check_config(config, mesh, metrics, self.thresholds))
        if has_stopping_issue(run.issues):
            raise _Stop("Validation found problems that stop the run; no OpenFOAM "
                        "command was started.")

        # 2. Geometry artifact, written once and copied to every sub-case that reads it.
        run.status(Op.IMPORT_GEOMETRY)
        rotor_area = self._geometry(run, mesh, force)

        # 3. Dictionaries (cheap; write-if-changed).
        run.status(Op.GENERATE_CASES)
        generated = self.generator.generate(run.root, config)
        run.executed.append(Op.GENERATE_CASES)
        run.issues.extend(generated.issues)
        if has_stopping_issue(generated.issues):
            raise _Stop("Case generation failed; no OpenFOAM command was started.")

        # 4. Which OpenFOAM operations may run (cached ones verify first).
        may_run: dict[VawtOperation, bool] = {}
        for op in run.operations:
            if op not in _EXECUTABLES:
                continue
            upstream = any(may_run.get(u) for u, down in DOWNSTREAM.items() if op in down)
            may_run[op] = force or upstream or not run.store.check(
                op, self._inputs(run, op)).reusable

        # 5. Environment check for every executable that may run.
        executables = sorted({e for op, runs in may_run.items() if runs
                              for e in _EXECUTABLES[op]})
        if executables:
            found = validate_environment(config.openfoam_profile, executables, self._env())
            if has_stopping_issue(found):
                raise _Stop("The OpenFOAM environment is not usable; nothing was run.",
                            *found)
            run.issues.extend(found)

        # 6. Resource preflight before expensive meshing.
        if any(may_run.get(op) for op in _MESHING):
            self._preflight(run, rotor_area, allow_risk)

        # 7. OpenFOAM operations, in order. The previous mesh report no longer
        #    describes the meshes once any of them runs; set it aside first.
        if any(may_run.values()):
            self._supersede_report(run)
        for op in run.operations:
            if op not in _EXECUTABLES:
                continue
            run.status(op)
            inputs = self._inputs(run, op)
            if not force and run.store.check(op, inputs).reusable:
                run.reused.append(op)
                continue
            run.store.invalidate(op)  # a failed or cancelled run never leaves a record
            run.executed.append(op)
            outputs = await self._execute(run, op)
            run.store.record(op, inputs, outputs)

        # 8. Validation against acceptance limits and the region rule (cheap).
        run.status(Op.VALIDATE_MESH)
        run.executed.append(Op.VALIDATE_MESH)
        report_path, succeeded, status = self._validate_mesh(run, layout.final)
        return self._result(run, succeeded, f"Mesh validation completed with status: {status}.",
                            RunState.SUCCEEDED if succeeded else RunState.FAILED,
                            report=report_path)

    # --- geometry --------------------------------------------------------------------

    def _geometry_inputs(self, config: VawtProjectConfig) -> dict[str, str]:
        return {
            "source": sha256_file(config.geometry.source_path),
            "config:geometry": sha256_json(config.geometry.model_dump(mode="json")),
            "artifact_format": str(ARTIFACT_FORMAT_VERSION),
            "validator": sha256_json(asdict(self.geometry_validator.dimension_limits)),
        }

    def _artifact_paths(self, run: _Run) -> list[Path]:
        """Where each sub-case that meshes the rotor expects the surface."""
        patch = run.config.geometry.patch_name
        consumers = [ROTOR] if ROTOR in case_layout(run.config).sub_cases else [MERGED]
        return [run.case(c) / "constant" / "triSurface" / f"{patch}.stl" for c in consumers]

    def _geometry(self, run: _Run, mesh: Any, force: bool) -> float:
        """Write the ASCII artifact once (if its inputs changed) and copy it to every
        sub-case that needs it. Returns the rotor surface area for the preflight."""
        config, store = run.config, run.store
        inputs = self._geometry_inputs(config)
        report_path = run.root / GEOMETRY_REPORT
        cached = None if force else store.check(Op.IMPORT_GEOMETRY, inputs)
        targets = self._artifact_paths(run)
        if cached is not None and cached.reusable and cached.record is not None:
            # The recorded artifact (possibly in another sub-case) is the source of copies.
            recorded = [run.root / p for p in cached.record.outputs if p.endswith(".stl")]
            artifact = recorded[0]
            run.reused.append(Op.IMPORT_GEOMETRY)
        else:
            store.invalidate(Op.IMPORT_GEOMETRY)
            run.executed.append(Op.IMPORT_GEOMETRY)
            artifact = targets[0]
            transform = GeometryTransform.from_config(config.geometry)
            write_stl_artifact(mesh, artifact, config.geometry.patch_name)
            # Re-validate the artifact as written: this is what meshing consumes.
            checked = self.geometry_validator.validate(artifact, check_dimensions_in_metres=True)
            report = {
                "source_path": str(config.geometry.source_path),
                "transformation": transform.as_dict(),
                "artifact_path": str(artifact.relative_to(run.root).as_posix()),
                "artifact": checked.as_dict(),
            }
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            if has_stopping_issue(checked.issues):
                raise _Stop("The transformed geometry failed validation; no OpenFOAM "
                            "command was started.", *checked.issues)
            store.record(Op.IMPORT_GEOMETRY, inputs, [artifact, report_path],
                         details={"issues": [i.as_dict() for i in checked.issues]})
        expected = sha256_file(artifact)
        for target in targets:
            if target != artifact and sha256_file(target) != expected:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact, target)  # a copy, not a second encoding
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            return float(data["artifact"]["surface_area"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    # --- cache inputs ---------------------------------------------------------------

    def _system_files(self, run: _Run, sub_case: str) -> list[Path]:
        system = run.case(sub_case) / "system"
        return sorted(p for p in system.iterdir() if p.is_file()) if system.is_dir() else []

    def _mesh_files(self, run: _Run, sub_case: str) -> list[Path]:
        return files_under(run.case(sub_case) / POLY_MESH, exclude_dirs=_EXCLUDED)

    def _inputs(self, run: _Run, op: VawtOperation) -> dict[str, str]:
        config, root = run.config, run.root
        patch = config.geometry.patch_name
        files: list[Path] = []
        if op is Op.OUTER_MESH:
            files = self._system_files(run, OUTER)
        elif op is Op.ROTOR_FEATURES:
            case = run.case(feature_case(config))
            files = [case / "system/surfaceFeatureExtractDict", case / "system/controlDict",
                     case / "constant/triSurface" / f"{patch}.stl"]
        elif op in (Op.ROTOR_MESH, Op.SINGLE_MESH):
            case = run.case(ROTOR if op is Op.ROTOR_MESH else MERGED)
            files = [*self._system_files(run, case.name),
                     case / "constant/triSurface" / f"{patch}.stl"]
            if config.refinement.extract_features:
                files.append(case / "constant/triSurface" / f"{patch}.eMesh")
        elif op is Op.ASSEMBLE:
            files = [*self._mesh_files(run, OUTER), *self._mesh_files(run, ROTOR),
                     run.case(MERGED) / "system/createPatchDict",
                     run.case(MERGED) / "system/controlDict"]
        elif op is Op.CHECK_MESH:
            final = case_layout(config).final
            files = [*self._mesh_files(run, final),
                     run.case(final) / "system/meshQualityDict"]
        inputs = {**hash_files(root, files), "tool": self._tool(config, *_EXECUTABLES[op])}
        if op is Op.CHECK_MESH:
            final_case = run.case(case_layout(config).final)
            argv = check_mesh_step(final_case).argv
            inputs["checkMesh_options"] = " ".join(
                a for a in argv[1:] if a not in ("-case", str(final_case)))
        return inputs

    # --- preflight -------------------------------------------------------------------

    def _preflight(self, run: _Run, rotor_area: float, allow_risk: bool) -> None:
        estimate, assessment = assess(run.config, rotor_area, self.system_probe(run.root),
                                      self.estimator)
        path = run.root / PREFLIGHT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            **assessment.as_dict(),
            "estimated_cells_by_mesh": {**asdict(estimate), "outer": estimate.outer,
                                        "rotor": estimate.rotor, "total": estimate.total},
        }, indent=2) + "\n", encoding="utf-8")
        if assessment.status is ResourceStatus.BLOCKED:
            raise _Stop("Resource preflight BLOCKED meshing.", *assessment.issues)
        if assessment.status is ResourceStatus.HIGH_RESOURCE_RISK and not allow_risk:
            raise _Stop(
                "Resource preflight reported HIGH RESOURCE RISK; meshing was not started.",
                *assessment.issues,
                _issue(IssueSeverity.BLOCKING, IssueCategory.RESOURCE_RISK,
                       IssueStage.RESOURCE_PREFLIGHT, "HIGH_RESOURCE_RISK_NOT_ACKNOWLEDGED",
                       "Meshing needs explicit acknowledgement of the high resource risk.",
                       "Reduce the mesh settings, or start the run with "
                       "allow_high_resource_risk to proceed anyway."),
            )
        run.issues.extend(assessment.issues)

    # --- OpenFOAM operations ------------------------------------------------------------

    async def _command(self, run: _Run, step: MeshingStep, case: Path) -> CommandResult:
        if run.cancel_event is not None and run.cancel_event.is_set():
            raise _Stop("Run cancelled.", _issue(
                IssueSeverity.ERROR, IssueCategory.TIMEOUT_CANCELLATION, step.stage,
                "RUN_CANCELLED", "The run was cancelled before this step started.",
                "Start the run again when ready; completed steps are reused."),
                state=RunState.CANCELLED)
        result = await self.runner.run_step(
            step, case_root=case, env=self.environment, cancel_event=run.cancel_event,
            logs_dir=run.root / "logs" / case.name,
        )
        run.commands.append(result)
        if result.status is RunStatus.CANCELLED:
            raise _Stop("Run cancelled.", *result.issues, state=RunState.CANCELLED)
        if not result.succeeded:
            raise _Stop(f"{step.argv[0]} failed in {case.name}. Review logs/{case.name}/.",
                        *result.issues)
        return result

    async def _execute(self, run: _Run, op: VawtOperation) -> list[Path]:
        config = run.config
        if op is Op.OUTER_MESH:
            case = run.case(OUTER)
            await self._command(run, block_mesh_step(case), case)
            snappy = await self._command(run, snappy_step(case), case)
            self._check_patches(run, op, case, self._domain_patches(config) | {
                INTERFACE_OUTER}, snappy)
            return self._mesh_files(run, OUTER)
        if op is Op.ROTOR_FEATURES:
            case = run.case(feature_case(config))
            extract = await self._command(run, self._feature_step(case), case)
            emesh = case / "constant/triSurface" / f"{config.geometry.patch_name}.eMesh"
            if not emesh.is_file():
                raise _Stop("Feature extraction produced no .eMesh.", _issue(
                    IssueSeverity.ERROR, IssueCategory.EXECUTION, IssueStage.FEATURE_EXTRACTION,
                    "FEATURE_OUTPUT_MISSING", f"surfaceFeatureExtract produced no {emesh.name}.",
                    "Inspect the command log.", log=Path(extract.log_path)))
            return [emesh]
        if op is Op.ROTOR_MESH:
            case = run.case(ROTOR)
            boundary = (INTERFACE_ROTOR if case_layout(config).final == MERGED
                        else ZONE_SURFACE)
            await self._command(run, block_mesh_step(case), case)
            snappy = await self._command(run, snappy_step(case), case)
            self._check_patches(run, op, case, {config.geometry.patch_name, boundary}, snappy)
            topo = await self._command(run, topo_set_step(case, "05_topoSet.log"), case)
            self._check_zones(run, op, case, topo, cell_zone=True, face_zone=False)
            return self._mesh_files(run, ROTOR)
        if op is Op.SINGLE_MESH:
            case = run.case(MERGED)
            await self._command(run, block_mesh_step(case), case)
            snappy = await self._command(run, snappy_step(case), case)
            self._check_patches(run, op, case, self._domain_patches(config) | {
                config.geometry.patch_name}, snappy)
            self._check_zones(run, op, case, snappy, cell_zone=True, face_zone=True)
            return self._mesh_files(run, MERGED)
        if op is Op.ASSEMBLE:
            merged, rotor = run.case(MERGED), run.case(ROTOR)
            target = merged / POLY_MESH
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(run.case(OUTER) / POLY_MESH, target,
                            ignore=shutil.ignore_patterns(*_EXCLUDED))
            # mergeMeshes runs from the master case (V0 R9).
            await self._command(run, merge_meshes_step(merged, rotor, "05_mergeMeshes.log"),
                                merged)
            create = await self._command(run, create_patch_step(merged, "06_createPatch.log"),
                                         merged)
            faces = _patch_faces(merged)
            empty = [name for name in AMI_PATCHES if faces.get(name, 0) <= 0]
            if empty:
                raise _Stop("The AMI interface has no faces.", _issue(
                    IssueSeverity.ERROR, IssueCategory.MESHING, IssueStage.PATCH_CREATION,
                    "AMI_PATCH_EMPTY",
                    f"createPatch produced no faces in {', '.join(empty)}.",
                    "Check that the outer and rotor meshes have their cylinder patches "
                    f"({INTERFACE_OUTER}, {INTERFACE_ROTOR}); createPatch exits 0 even when "
                    "a source patch is missing.", log=Path(create.log_path),
                    case=MERGED, empty=empty, patches=faces))
            self._check_patches(run, op, merged, self._domain_patches(config) | {
                config.geometry.patch_name}, create)
            self._check_zones(run, op, merged, create, cell_zone=True, face_zone=False)
            return self._mesh_files(run, MERGED)
        if op is Op.CHECK_MESH:
            case = run.case(case_layout(config).final)
            result = await self._command(run, check_mesh_step(case), case)
            return [Path(result.log_path)]
        raise AssertionError(f"not an OpenFOAM operation: {op}")

    @staticmethod
    def _feature_step(case: Path) -> MeshingStep:
        # surfaceFeatureExtract -case <case>: the openfoam.com form (V0, V2).
        return feature_extraction_step(("surfaceFeatureExtract", "-case", str(case)))

    @staticmethod
    def _domain_patches(config: VawtProjectConfig) -> set[str]:
        return set(config.domain.patches.names()) if config.domain is not None else set()

    def _check_patches(self, run: _Run, op: VawtOperation, case: Path, expected: set[str],
                       evidence: CommandResult) -> None:
        """Each expected patch exists with faces (V0 R3: a mesh point in the wrong
        region exits 0 and meshes that region instead)."""
        faces = _patch_faces(case)
        missing = sorted(name for name in expected if faces.get(name, 0) <= 0)
        if missing:
            raise _Stop(f"The {case.name} mesh is not the intended region.", _issue(
                IssueSeverity.ERROR, IssueCategory.MESHING, evidence_stage(evidence, op),
                "MESHED_WRONG_REGION",
                f"The {case.name} mesh has no faces on {', '.join(missing)}.",
                "Check the mesh point (locationInMesh) of this sub-case: it must lie in the "
                "fluid region meant to be meshed, not inside the rotor or the wrong side "
                "of the cylinder.", log=Path(evidence.log_path),
                explanation="snappyHexMesh keeps the region that contains the mesh point "
                "and exits successfully even when that is the wrong region.",
                case=case.name, missing=missing, patches=faces))

    def _check_zones(self, run: _Run, op: VawtOperation, case: Path, evidence: CommandResult,
                     *, cell_zone: bool, face_zone: bool) -> None:
        missing = []
        if cell_zone and CELL_ZONE_NAME not in _zone_names(case, "cellZones"):
            missing.append(f"cell zone {CELL_ZONE_NAME}")
        if face_zone and ZONE_SURFACE not in _zone_names(case, "faceZones"):
            missing.append(f"face zone {ZONE_SURFACE}")
        if missing:
            raise _Stop(f"The {case.name} mesh has no rotating zone.", _issue(
                IssueSeverity.ERROR, IssueCategory.MESHING, evidence_stage(evidence, op),
                "ROTATING_ZONE_MISSING",
                f"The {case.name} mesh is missing the {' and '.join(missing)}.",
                "Inspect the command log; the zone definition may not have matched.",
                log=Path(evidence.log_path), case=case.name, missing=missing))

    # --- mesh validation --------------------------------------------------------------

    @staticmethod
    def _supersede_report(run: _Run) -> None:
        current = run.root / MESH_REPORT
        if current.is_file():
            current.replace(run.root / SUPERSEDED_MESH_REPORT)

    def _validate_mesh(self, run: _Run, final: str) -> tuple[Path, bool, str]:
        log = run.root / "logs" / final / "04_checkMesh.log"
        metrics = CheckMeshParser().parse_file(log)
        report = MeshQualityValidator().assess(metrics, run.config.quality)
        expected = expected_regions(run.config)
        found = _REGIONS.search(log.read_text(encoding="utf-8", errors="replace")
                                if log.is_file() else "")
        if found is None:
            extra = _issue(
                IssueSeverity.WARNING, IssueCategory.MESHING, IssueStage.MESH_VALIDATION,
                "REGION_COUNT_UNKNOWN", "checkMesh did not report the number of regions.",
                "Inspect the raw checkMesh log.", log=log, expected=expected)
            report = replace(report, issues=(*report.issues, extra))
        elif int(found.group(1)) != expected:
            mode = run.config.rotating_zone.interface
            extra = _issue(
                IssueSeverity.ERROR, IssueCategory.MESHING, IssueStage.MESH_VALIDATION,
                "REGION_COUNT_UNEXPECTED",
                f"The mesh has {found.group(1)} disconnected region(s); a "
                f"{'two-mesh AMI' if expected == 2 else mode.value} mesh has {expected}.",
                "Inspect the raw checkMesh log: part of the domain is cut off, or the "
                "meshes were not assembled as intended.", log=log,
                found=int(found.group(1)), expected=expected,
                ami=mode is InterfaceType.AMI)
            report = replace(report, validity=ValidityStatus.INVALID, status="failed",
                             issues=(*report.issues, extra))
        path = run.root / MESH_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = report.as_dict()
        payload["run_id"] = run.run_id
        payload["config_sha256"] = sha256_json(run.config.model_dump(mode="json"))
        payload["regions"] = {"expected": expected,
                              "found": int(found.group(1)) if found else None}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        run.issues.extend(report.issues)
        return path, report.status != "failed", report.status

    # --- run record -------------------------------------------------------------------

    def _record(self, run: _Run, result: VawtRunResult | None, clock: float,
                error: BaseException | None) -> Path:
        executables = sorted({e for names in _EXECUTABLES.values() for e in names})
        return write_run_record(run.root, {
            "run_id": run.run_id,
            "kind": "vawt_mesh",
            "app_version": APP_VERSION,
            "config_sha256": sha256_json(run.config.model_dump(mode="json")),
            "project_name": run.config.project_name,
            "environment": tool_identity(run.config.openfoam_profile, executables, self._env()),
            "started_at": run.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_seconds": time.monotonic() - clock,
            "state": result.state.value if result else RunState.FAILED.value,
            "succeeded": bool(result and result.succeeded),
            "message": result.message if result else f"Internal error: {error!r}",
            "executed": [op.value for op in run.executed],
            "reused": [op.value for op in run.reused],
            "issues": [i.as_dict() for i in (result.issues if result else run.issues)],
            "commands": [
                {"argv": list(c.argv), "status": c.status.value, "return_code": c.return_code,
                 "duration_seconds": c.duration_seconds, "log_path": c.log_path,
                 "stderr_log_path": c.stderr_log_path, "command_run_id": c.run_id}
                for c in run.commands
            ],
        })


def evidence_stage(evidence: CommandResult, op: VawtOperation) -> IssueStage:
    """The stage of the command whose output was checked."""
    name = evidence.argv[0] if evidence.argv else ""
    return {
        "snappyHexMesh": IssueStage.SNAPPY_HEX_MESH, "topoSet": IssueStage.ZONE_CREATION,
        "createPatch": IssueStage.PATCH_CREATION, "mergeMeshes": IssueStage.MERGE_MESHES,
    }.get(name, IssueStage.OPENFOAM_EXECUTION)
