"""Machine meshing pipeline (docs/rotating_machinery.md section 11; G4, H2).

One run under the project run lock, built from the VAWT V3 pieces (run lock,
status file, run records, artifact manifest, environment check, OpenFOAM
runner, resource estimator):

    validate (gate) -> generate cases -> environment check, resource preflight
    -> one mesh per domain and zone case -> assemble -> checkMesh, AMI weights
    -> mesh validation and result checks

Each OpenFOAM operation is reused only when its recorded inputs and outputs
verify. A mesh whose result check fails is not recorded. Commands run from
their case directory; logs go to logs/<case>/. Meshes are built untyped and
the assembly sets the patch types (H3).
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
from core.artifacts.hashing import files_under, hash_files, sha256_json
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from core.version import APP_VERSION
from core.workflow.run_lock import RunLock, process_create_time
from core.workflow.run_records import write_run_record
from machines.assembly import MERGED_CASE, MachineCaseGenerator, MachineCases, Step, meshing_steps
from machines.case_generator import CASES_DIR, DomainCase
from machines.config import InterfaceType, MachineProjectConfig, MachineType
from machines.mesh_checks import (
    cell_zone_names,
    check_assembly,
    check_domain_case,
    parse_ami_weights,
)
from machines.operations import (
    ASSEMBLE,
    CHECK_MESH,
    GENERATE_CASES,
    MACHINE_EXECUTABLES,
    MESH_PREFIX,
    VALIDATE,
    VALIDATE_MESH,
    case_of,
    executables,
    operations_for,
)
from machines.preflight import assess
from machines.project_store import write_last_meshed
from machines.validation import MachineThresholds, check_all
from mesh.estimator import ResourceEstimator, ResourceStatus, SystemResources
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityValidator, ValidityStatus
from openfoam.commands import MeshingStep
from openfoam.environment import tool_identity, validate_environment
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus
from vawt.status import RunState, write_status

PREFLIGHT_REPORT = "reports/resource_preflight.json"
MESH_REPORT = "reports/mesh_quality_report.json"
SUPERSEDED_MESH_REPORT = "reports/mesh_quality_report.superseded.json"
POLY_MESH = "constant/polyMesh"
_EXCLUDED = frozenset({"sets"})  # checkMesh diagnostics, not part of the mesh
CHECK_MESH_LOG = "checkMesh.log"
AMI_WEIGHTS_LOG = "postProcess.log"
# OpenFOAM versions the method was proven on (G0, G3).
VERIFIED_VERSIONS = ("v2512",)
_STAGES = {
    "blockMesh": IssueStage.BLOCK_MESH, "surfaceFeatureExtract": IssueStage.FEATURE_EXTRACTION,
    "snappyHexMesh": IssueStage.SNAPPY_HEX_MESH, "topoSet": IssueStage.ZONE_CREATION,
    "mergeMeshes": IssueStage.MERGE_MESHES, "createPatch": IssueStage.PATCH_CREATION,
    "foamDictionary": IssueStage.PATCH_CREATION, "checkMesh": IssueStage.CHECK_MESH,
    "postProcess": IssueStage.MESH_VALIDATION,
}


@dataclass(frozen=True)
class MachineRunResult:
    succeeded: bool
    message: str
    run_id: str
    state: RunState
    issues: tuple[Issue, ...] = ()
    executed: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    mesh_report_path: Path | None = None
    run_record_path: Path | None = None


class _Stop(Exception):
    """Ends the run early with a result (failure or cancellation)."""

    def __init__(self, message: str, *issues: Issue,
                 state: RunState = RunState.FAILED) -> None:
        super().__init__(message)
        self.message, self.issues, self.state = message, issues, state


def _issue(severity: IssueSeverity, category: IssueCategory, stage: IssueStage, code: str,
           message: str, action: str, **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=stage, code=code,
                 message=message, suggested_action=action, details=dict(details))


@dataclass
class _Run:
    root: Path
    config: MachineProjectConfig
    run_id: str
    started_at: str
    store: ArtifactStore
    cancel_event: asyncio.Event | None
    operations: tuple[str, ...] = (VALIDATE, GENERATE_CASES)
    executed: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    commands: list[CommandResult] = field(default_factory=list)
    record_extra: Mapping[str, Any] = field(default_factory=dict)
    stage: str = "starting"
    step: int = 0

    def case(self, name: str) -> Path:
        return self.root / CASES_DIR / name

    def status(self, operation: str, state: RunState = RunState.RUNNING, message: str = "",
               command: Mapping[str, Any] | None = None) -> None:
        self.stage = operation
        if operation in self.operations:
            self.step = self.operations.index(operation) + 1
        write_status(self.root, run_id=self.run_id, state=state, stage=operation,
                     step=self.step, total=len(self.operations), started_at=self.started_at,
                     message=message, command=command)


def vawt_workflow_issues(config: MachineProjectConfig) -> list[Issue]:
    """Settings only the VAWT workflow meshes (D6)."""
    if config.machine is not MachineType.VAWT:
        return []
    found = []
    if config.domain is None:
        found.append("no domain (rotating zone only)")
    if any(z.interface is InterfaceType.CELL_ZONE for z in config.rotating_zones):
        found.append("a CELL_ZONE interface")
    if config.wake is not None:
        found.append("a wake box")
    if config.export.fluent_msh:
        found.append("Fluent export")
    if not found:
        return []
    return [_issue(IssueSeverity.BLOCKING, IssueCategory.CONFIGURATION,
                   IssueStage.CONFIGURATION, "USE_VAWT_WORKFLOW",
                   f"This VAWT project uses {', '.join(found)}, which only the VAWT "
                   "workflow meshes.", "Mesh it with the VAWT workflow.", settings=found)]


class MachinePipeline:
    def __init__(
        self,
        runner: OpenFOAMRunner | None = None,
        environment: Mapping[str, str] | None = None,
        estimator: ResourceEstimator | None = None,
        system_probe: Callable[[Path], SystemResources] | None = None,
        thresholds: MachineThresholds | None = None,
    ) -> None:
        self.runner = runner or OpenFOAMRunner()
        self.environment = environment  # None: the process environment at run time
        self.estimator = estimator or ResourceEstimator()
        self.system_probe = system_probe or SystemResources.detect
        self.thresholds = thresholds or MachineThresholds()
        self.generator = MachineCaseGenerator()

    def _env(self) -> Mapping[str, str]:
        return self.environment if self.environment is not None else os.environ

    def _tool(self, config: MachineProjectConfig, names: Sequence[str]) -> str:
        return sha256_json(tool_identity(config.openfoam_profile, names, self._env()))

    # --- public entry points -------------------------------------------------------------

    def run_sync(self, root: Path, config: MachineProjectConfig,
                 **kwargs: Any) -> MachineRunResult:
        return asyncio.run(self.run(root, config, **kwargs))

    async def run(
        self,
        root: Path,
        config: MachineProjectConfig,
        *,
        force: bool = False,
        allow_high_resource_risk: bool = False,
        cancel_event: asyncio.Event | None = None,
        run_id: str | None = None,
        record_extra: Mapping[str, Any] | None = None,
    ) -> MachineRunResult:
        """Mesh the configuration, re-running only what is not verifiably current.

        As VawtPipeline.run: force ignores the cache; HIGH RESOURCE RISK stops
        unless allowed; BLOCKED always stops; cancel_event stops the running
        command and starts no further one.
        """
        lock = RunLock(root)
        busy = lock.acquire()
        run_id = run_id or uuid.uuid4().hex
        if busy is not None:
            return MachineRunResult(False, "Another run is in progress for this project.",
                                    run_id, RunState.FAILED, issues=(busy,))
        clock = time.monotonic()
        run = _Run(root, config, run_id, datetime.now(UTC).isoformat(), ArtifactStore(root),
                   cancel_event, record_extra=dict(record_extra or {}))
        result: MachineRunResult | None = None
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

    # --- the run ---------------------------------------------------------------------------

    def _result(self, run: _Run, succeeded: bool, message: str, state: RunState,
                extra: Sequence[Issue] = (), report: Path | None = None) -> MachineRunResult:
        return MachineRunResult(succeeded, message, run.run_id, state, (*run.issues, *extra),
                                tuple(run.executed), tuple(run.reused), report)

    async def _run(self, run: _Run, force: bool, allow_risk: bool) -> MachineRunResult:
        config = run.config

        # 1. Validation gate: ERROR or BLOCKING stops before any OpenFOAM command.
        run.status(VALIDATE)
        run.executed.append(VALIDATE)
        run.issues.extend(vawt_workflow_issues(config))
        checked = check_all(config, self.thresholds)
        run.issues.extend(checked.issues)
        if has_stopping_issue(run.issues):
            raise _Stop("Validation found problems that stop the run; no OpenFOAM command "
                        "was started.")

        # 2. Dictionaries and surfaces (cheap; write-if-changed). Untyped meshes (H3).
        run.status(GENERATE_CASES)
        try:
            cases = self.generator.render(config, checked.surfaces, checked.meshes,
                                          typed=False)
        except ValueError as exc:
            raise _Stop("Case generation failed; no OpenFOAM command was started.", _issue(
                IssueSeverity.BLOCKING, IssueCategory.CONFIGURATION, IssueStage.CASE_GENERATION,
                "CASE_GENERATION_UNSUPPORTED", str(exc),
                "Change the configuration; see the message.")) from exc
        self.generator.write(run.root, cases)
        run.executed.append(GENERATE_CASES)
        run.operations = operations_for(cases)
        run.status(GENERATE_CASES)
        openfoam_ops = [op for op in run.operations if executables(op)]

        # 3. Which operations may run (cached ones verify first; downstream follows).
        may_run: dict[str, bool] = {}
        any_mesh = False
        for op in openfoam_ops:
            stale = force or not run.store.check(op, self._inputs(run, cases, op)).reusable
            if op.startswith(MESH_PREFIX):
                any_mesh |= stale
                may_run[op] = stale
            elif op == ASSEMBLE:
                may_run[op] = stale or any_mesh
            else:
                may_run[op] = stale or may_run.get(ASSEMBLE, False)

        # 4. Environment check for every executable that may run.
        names = sorted({e for op, runs in may_run.items() if runs for e in executables(op)})
        if names:
            found = validate_environment(config.openfoam_profile, names, self._env(),
                                         verified_versions=VERIFIED_VERSIONS)
            if has_stopping_issue(found):
                raise _Stop("The OpenFOAM environment is not usable; nothing was run.", *found)
            run.issues.extend(found)

        # 5. Resource preflight before expensive meshing.
        if any_mesh:
            self._preflight(run, checked.meshes, checked.surfaces, allow_risk)

        # 6. OpenFOAM operations, in order. Set the old mesh report aside first.
        if any(may_run.values()):
            current = run.root / MESH_REPORT
            if current.is_file():
                current.replace(run.root / SUPERSEDED_MESH_REPORT)
        # Inputs are computed when each operation is reached: the assembly and
        # check inputs hash the meshes they consume, so they follow any re-run.
        for op in openfoam_ops:
            run.status(op)
            inputs = self._inputs(run, cases, op)
            if not force and run.store.check(op, inputs).reusable:
                run.reused.append(op)
                continue
            run.store.invalidate(op)  # a failed or cancelled run never leaves a record
            run.executed.append(op)
            outputs = await self._execute(run, cases, op)
            run.store.record(op, inputs, outputs)
        # 7. Mesh validation and result checks (cheap).
        run.status(VALIDATE_MESH)
        run.executed.append(VALIDATE_MESH)
        report_path, succeeded, status = self._validate_mesh(run, cases)
        write_last_meshed(run.root, run.run_id, config, status)
        return self._result(run, succeeded, f"Mesh validation completed with status: {status}.",
                            RunState.SUCCEEDED if succeeded else RunState.FAILED,
                            report=report_path)

    # --- cache inputs -------------------------------------------------------------------------

    def _mesh_files(self, run: _Run, case: str) -> list[Path]:
        return files_under(run.case(case) / POLY_MESH, exclude_dirs=_EXCLUDED)

    def _case_inputs(self, run: _Run, case: DomainCase) -> list[Path]:
        root = run.case(case.name)
        return sorted([*(root / r for r in case.dictionaries), *(root / r for r in case.surfaces)])

    def _inputs(self, run: _Run, cases: MachineCases, op: str) -> dict[str, str]:
        config = run.config
        files: list[Path] = []
        extra: dict[str, str] = {}
        if op.startswith(MESH_PREFIX):
            case = next(c for c in (*cases.domain, *cases.zones) if c.name == case_of(op))
            files = self._case_inputs(run, case)
        elif op == ASSEMBLE:
            for case in (*cases.domain, *cases.zones):
                files += self._mesh_files(run, case.name)
            merged = run.case(MERGED_CASE)
            files += [merged / "system/createPatchDict", merged / "system/controlDict"]
            extra["retype"] = sha256_json(cases.retype)
            extra["order"] = sha256_json([c.name for c in (*cases.domain, *cases.zones)])
        elif op == CHECK_MESH:
            merged = run.case(MERGED_CASE)
            files = [*self._mesh_files(run, MERGED_CASE), merged / "system/meshQualityDict",
                     merged / "system/amiWeightsDict"]
        return {**hash_files(run.root, files), **extra,
                "tool": self._tool(config, executables(op))}

    # --- preflight ------------------------------------------------------------------------------

    def _preflight(self, run: _Run, meshes: Mapping[str, Any], surfaces: Sequence[Any],
                   allow_risk: bool) -> None:
        estimate, assessment = assess(run.config, meshes, surfaces, self.system_probe(run.root),
                                      self.estimator)
        path = run.root / PREFLIGHT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            **assessment.as_dict(),
            "estimated_cells_by_case": estimate.per_case, "estimated_cells": estimate.total,
            "estimate": asdict(estimate),
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
                       "allow_high_resource_risk to proceed anyway."))
        run.issues.extend(assessment.issues)

    # --- OpenFOAM operations -------------------------------------------------------------------

    async def _command(self, run: _Run, step: Step, index: int) -> CommandResult:
        if run.cancel_event is not None and run.cancel_event.is_set():
            raise _Stop("Run cancelled.", _issue(
                IssueSeverity.ERROR, IssueCategory.TIMEOUT_CANCELLATION,
                _STAGES.get(step.argv[0], IssueStage.OPENFOAM_EXECUTION), "RUN_CANCELLED",
                "The run was cancelled before this step started.",
                "Start the run again when ready; completed steps are reused."),
                state=RunState.CANCELLED)
        case = run.root / step.case
        name = step.argv[0]
        log_name = (CHECK_MESH_LOG if name == "checkMesh" else
                    AMI_WEIGHTS_LOG if name == "postProcess" else
                    f"{index:02d}_{step.name.removeprefix(case.name + '_')}.log")
        logs = run.root / "logs" / case.name
        command = {"name": name, "sub_case": case.name,
                   "log": (logs / log_name).relative_to(run.root).as_posix()}
        run.status(run.stage, command=command)

        def started(pid: int) -> None:
            run.status(run.stage, command={**command, "pid": pid,
                                           "pid_create_time": process_create_time(pid)})

        result = await self.runner.run_step(
            MeshingStep(step.argv, log_name, _STAGES.get(name, IssueStage.OPENFOAM_EXECUTION)),
            case_root=case, env=self.environment, cancel_event=run.cancel_event, logs_dir=logs,
            on_start=started)
        run.commands.append(result)
        if result.status is RunStatus.CANCELLED:
            raise _Stop("Run cancelled.", *result.issues, state=RunState.CANCELLED)
        if not result.succeeded:
            raise _Stop(f"{name} failed in {case.name}. Review logs/{case.name}/.",
                        *result.issues)
        return result

    async def _execute(self, run: _Run, cases: MachineCases, op: str) -> list[Path]:
        steps = meshing_steps(cases)
        if op.startswith(MESH_PREFIX):
            case = next(c for c in (*cases.domain, *cases.zones) if c.name == case_of(op))
            own = [s for s in steps if s.case == case.root]
            for index, step in enumerate(own, start=1):
                await self._command(run, step, index)
            self._check_case(run, case)
            return self._mesh_files(run, case.name)
        if op == ASSEMBLE:
            merged = run.case(MERGED_CASE)
            own = [s for s in steps if s.case == cases.merged.root
                   and s.argv[:1] not in (("checkMesh",), ("postProcess",))]
            for index, step in enumerate(own, start=1):
                if step.kind == "copy_mesh":
                    target = merged / POLY_MESH
                    shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(run.root / step.source / POLY_MESH, target,
                                    ignore=shutil.ignore_patterns(*_EXCLUDED))
                    continue
                await self._command(run, step, index)
            stopping = [i for i in check_domain_case(merged, cases.merged) if i.is_stopping]
            if stopping:
                raise _Stop("The assembled mesh is not the intended one.", *stopping)
            missing = [c.name.removeprefix("zone_") for c in cases.zones
                       if c.name.removeprefix("zone_") not in cell_zone_names(merged)]
            if missing:
                raise _Stop("The assembled mesh has no cell zone for every rotating zone.",
                            _issue(IssueSeverity.ERROR, IssueCategory.MESHING,
                                   IssueStage.MERGE_MESHES, "CELL_ZONE_MISSING",
                                   f"The merged mesh has no cell zone {', '.join(missing)}.",
                                   "Inspect the topoSet and mergeMeshes logs.",
                                   missing=missing))
            return self._mesh_files(run, MERGED_CASE)
        if op == CHECK_MESH:
            own = [s for s in steps if s.argv[:1] in (("checkMesh",), ("postProcess",))]
            logs = []
            for index, step in enumerate(own, start=1):
                logs.append(Path((await self._command(run, step, index)).log_path))
            return logs
        raise AssertionError(f"not an OpenFOAM operation: {op}")

    def _check_case(self, run: _Run, case: DomainCase) -> None:
        """Each expected patch has faces, the background is gone, and a zone mesh
        has its cell zone (a mesh point in the wrong region exits 0, V0 R3)."""
        directory = run.case(case.name)
        found = [i for i in check_domain_case(directory, case) if i.is_stopping]
        if "system/topoSetDict" in case.dictionaries:
            zone = case.name.removeprefix("zone_")
            if zone not in cell_zone_names(directory):
                found.append(_issue(
                    IssueSeverity.ERROR, IssueCategory.MESHING, IssueStage.ZONE_CREATION,
                    "CELL_ZONE_MISSING", f"The {case.name} mesh has no cell zone {zone}.",
                    "Inspect the topoSet log.", case=case.name))
        if found:
            raise _Stop(f"The {case.name} mesh is not the intended one; it was not kept "
                        "for reuse.", *found)

    # --- mesh validation ------------------------------------------------------------------------

    def _validate_mesh(self, run: _Run, cases: MachineCases) -> tuple[Path, bool, str]:
        logs = run.root / "logs" / MERGED_CASE
        check_text = _read(logs / CHECK_MESH_LOG)
        ami_text = _read(logs / AMI_WEIGHTS_LOG)
        metrics = CheckMeshParser().parse_text(check_text, logs / CHECK_MESH_LOG)
        report = MeshQualityValidator().assess(metrics, run.config.quality)
        extra = check_assembly(cases, run.case(MERGED_CASE), check_text, ami_text or None,
                               ami_range=self.thresholds.ami_weights_range)
        if any(i.is_stopping for i in extra):
            report = replace(report, validity=ValidityStatus.INVALID, status="failed")
        report = replace(report, issues=(*report.issues, *extra))
        regions = re.search(r"Number of regions:\s*(\d+)", check_text)
        path = run.root / MESH_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = report.as_dict()
        payload["run_id"] = run.run_id
        payload["config_sha256"] = sha256_json(run.config.model_dump(mode="json"))
        payload["regions"] = {"expected": len(cases.domain) + len(cases.zones),
                              "found": int(regions[1]) if regions else None}
        payload["ami_weights"] = [asdict(w) for w in parse_ami_weights(ami_text)]
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        run.issues.extend(report.issues)
        return path, report.status != "failed", report.status

    # --- run record ---------------------------------------------------------------------------

    def _record(self, run: _Run, result: MachineRunResult | None, clock: float,
                error: BaseException | None) -> Path:
        return write_run_record(run.root, {
            **run.record_extra,
            "run_id": run.run_id,
            "kind": "machine_mesh",
            "app_version": APP_VERSION,
            "config_sha256": sha256_json(run.config.model_dump(mode="json")),
            "project_name": run.config.project_name,
            "machine": run.config.machine.value,
            "environment": tool_identity(run.config.openfoam_profile, MACHINE_EXECUTABLES,
                                         self._env()),
            "started_at": run.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_seconds": time.monotonic() - clock,
            "state": result.state.value if result else RunState.FAILED.value,
            "succeeded": bool(result and result.succeeded),
            "message": result.message if result else f"Internal error: {error!r}",
            "executed": list(run.executed),
            "reused": list(run.reused),
            "issues": [i.as_dict() for i in (result.issues if result else run.issues)],
            "commands": [
                {"argv": list(c.argv), "status": c.status.value, "return_code": c.return_code,
                 "duration_seconds": c.duration_seconds, "log_path": c.log_path,
                 "stderr_log_path": c.stderr_log_path, "command_run_id": c.run_id}
                for c in run.commands],
        })


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

