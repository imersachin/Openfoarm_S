"""VawtService: the only interface the VAWT UI calls (spec section 12).

Plain data and structured issues in, plain data out; no Streamlit types, so the
UI can be replaced. Runs use the saved configuration only and go to a
background worker (vawt/runtime.py). Progress is read from
.preprocessor/status.json together with the run lock, so a refreshed page or a
second tab re-attaches to a run instead of starting another, and a run whose
process died is reported as INTERRUPTED.

Not here yet: preview (V6) and export_files (V7).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import psutil
import trimesh
from pydantic import ValidationError

from core.config.models import OpenFOAMProfile
from core.config.validation import issues_from_validation_error
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from core.services import ProjectService
from core.workflow.run_lock import CREATE_TIME_TOLERANCE_S, RunLock, process_create_time
from core.workflow.run_records import list_run_records
from mesh.estimator import ResourceAssessment, ResourceEstimator, SystemResources
from openfoam.environment import validate_environment
from vawt.case_generator import VawtCaseGenerator, zone_cell_size
from vawt.config import Axis, RotorGeometryConfig, VawtProjectConfig
from vawt.operations import CACHED, LAYOUT, PLANNER, VawtOperation, plan_changes
from vawt.pipeline import (
    GEOMETRY_REPORT,
    MESH_REPORT,
    PREFLIGHT_REPORT,
    VAWT_EXECUTABLES,
    VERIFIED_VERSIONS,
    VawtPipeline,
)
from vawt.preflight import VawtCellEstimate, assess
from vawt.presets import PresetKind
from vawt.presets import draft_from_preset as preset_draft
from vawt.project_store import VawtProjectStore, read_last_meshed
from vawt.rotor_metrics import RotorMetrics, compute_rotor_metrics
from vawt.runtime import REGISTRY, RunRegistry
from vawt.status import RunState, read_status
from vawt.validation import (
    UNSET_ERROR_TYPES,
    ValidationThresholds,
    check_config,
    load_rotor_geometry,
    parse_config,
)
from vawt.workspace import Mount, is_wsl, location_issues, projects_root

LOG_TAIL_BYTES = 64 * 1024
# A run writes its lock file within microseconds of creating it; an unreadable
# lock older than this was left by a process that died in between.
UNREADABLE_LOCK_AGE_S = 5.0
ORPHAN_TERMINATE_WAIT_S = 10.0

# --- sections ----------------------------------------------------------------------

SETUP_SECTIONS = ("project", "geometry", "rotating_zone", "domain", "refinement", "layers")
SECTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "project": ("schema_version", "project_name", "openfoam_profile"),
    "geometry": ("geometry",),
    "rotating_zone": ("rotor", "rotating_zone"),
    "domain": ("domain",),
    "refinement": ("refinement", "snappy_quality", "max_global_cells"),
    "layers": ("layers",),
}
# Configuration fields shown with the results, not in a setup section.
RESULT_FIELDS = ("quality", "export")

# Issues without a field, by code. Unlisted codes appear only in Review.
CODE_SECTIONS: Mapping[str, str] = {
    **dict.fromkeys((
        "GEOMETRY_SOURCE_MISSING", "GEOMETRY_UNREADABLE", "GEOMETRY_UNSUPPORTED_OBJECT",
        "DEGENERATE_EXTENT", "DEGENERATE_FACES", "DUPLICATE_FACES", "INCONSISTENT_WINDING",
        "INWARD_NORMALS", "MULTIPLE_COMPONENTS", "NON_FINITE_COORDINATES", "NOT_WATERTIGHT",
        "NO_FACES", "SUSPICIOUS_DIMENSIONS", "BODY_INSIDE_OUT", "UNITS_NOT_CHOSEN"),
        "geometry"),
    **dict.fromkeys((
        "ROTOR_AXIS_NOT_CONFIRMED", "ROTOR_OUTSIDE_ZONE", "ZONE_CLEARANCE_SMALL",
        "TOO_FEW_ZONE_CELLS", "INNER_POINT_IN_ROTOR", "INNER_POINT_OUTSIDE_ZONE",
        "INNER_POINT_UNCHECKED", "AMI_REQUIRES_DOMAIN", "ZONE_CELL_SIZE_ADJUSTED"),
        "rotating_zone"),
    **dict.fromkeys(("ZONE_OUTSIDE_DOMAIN", "OUTER_POINT_INVALID"), "domain"),
    "WAKE_OUTSIDE_DOMAIN": "refinement",
    **dict.fromkeys(("ABSOLUTE_LAYER_TOO_THICK", "LAYERS_TOO_THIN_FOR_CELLS",
                     "LAYER_MIN_THICKNESS_TOO_LARGE"), "layers"),
    **dict.fromkeys(("SCHEMA_VERSION_INVALID", "SCHEMA_VERSION_NEWER",
                     "VAWT_PROFILE_UNSUPPORTED"), "project"),
}
_UNSET_CODES = frozenset({"UNITS_NOT_CHOSEN", "ROTOR_AXIS_NOT_CONFIRMED"})


def section_of_field(path: str) -> str | None:
    for section, prefixes in SECTION_FIELDS.items():
        if any(path == p or path.startswith(f"{p}.") for p in prefixes):
            return section
    return None


def section_of_issue(issue: Issue) -> str | None:
    found = issue.details.get("field")
    if isinstance(found, str) and found:
        section = section_of_field(found)
        if section is not None:
            return section
    return CODE_SECTIONS.get(issue.code)


class SectionState(StrEnum):
    EMPTY = "EMPTY"
    INCOMPLETE = "INCOMPLETE"
    READY = "READY"
    STALE = "STALE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class SectionStatus:
    state: SectionState
    issues: tuple[Issue, ...] = ()
    stale_because: tuple[str, ...] = ()  # changed settings that make meshes stale


# --- results of the calls -----------------------------------------------------------

@dataclass(frozen=True)
class LoadResult:
    raw: dict[str, Any] | None
    revision: int | None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class SaveResult:
    revision: int | None
    issues: tuple[Issue, ...] = ()

    @property
    def saved(self) -> bool:
        return self.revision is not None


@dataclass(frozen=True)
class ValidationOutcome:
    config: VawtProjectConfig | None
    metrics: RotorMetrics | None
    issues: tuple[Issue, ...]
    # Rotating-zone cell size the mesh will actually get (m). In a single mesh
    # it is the domain cell size / 2^n, which can differ from the one entered
    # (INFO ZONE_CELL_SIZE_ADJUSTED names both).
    zone_cell_size: float | None = None

    @property
    def can_run(self) -> bool:
        return self.config is not None and not has_stopping_issue(self.issues)


@dataclass(frozen=True)
class PlanView:
    operations: tuple[VawtOperation, ...]
    reasons: dict[VawtOperation, tuple[str, ...]]
    from_scratch: bool  # no mesh produced yet: everything runs
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class PreflightView:
    estimate: VawtCellEstimate | None
    assessment: ResourceAssessment | None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class EnvironmentInfo:
    wsl: bool
    distro: str | None
    openfoam_version: str | None
    projects_root: Path
    issues: tuple[Issue, ...] = ()


class RunView(StrEnum):
    NONE = "NONE"  # no run yet
    RUNNING = "RUNNING"  # this app process runs it; it can be cancelled from here
    RUNNING_ELSEWHERE = "RUNNING_ELSEWHERE"  # another live process holds the run lock
    INTERRUPTED = "INTERRUPTED"  # the status says running, but no live run holds the lock
    LOCK_UNREADABLE = "LOCK_UNREADABLE"  # a run lock with no readable holder, left behind
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class RunStatusView:
    state: RunView
    run_id: str | None = None
    stage: str | None = None
    step: int = 0
    total_steps: int = 0
    started_at: str | None = None
    updated_at: str | None = None
    message: str = ""
    can_cancel: bool = False
    log: str | None = None  # active log, relative to the project
    orphan_pid: int | None = None  # an OpenFOAM process left by an interrupted run
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class StartResult:
    started: bool
    run_id: str | None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class LogTail:
    path: str | None  # relative to the project
    text: str
    truncated: bool  # earlier output exists that was not read
    size: int  # bytes in the file
    bytes_read: int = 0


def _unique(issues: tuple[Issue, ...]) -> tuple[Issue, ...]:
    seen: set[tuple[str, str]] = set()
    kept = []
    for issue in issues:
        if (issue.code, issue.message) not in seen:
            seen.add((issue.code, issue.message))
            kept.append(issue)
    return tuple(kept)


def _issue(severity: IssueSeverity, code: str, message: str, action: str, *,
           category: IssueCategory = IssueCategory.EXECUTION,
           stage: IssueStage = IssueStage.OPENFOAM_EXECUTION, explanation: str = "",
           **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=stage, code=code,
                 message=message, explanation=explanation, suggested_action=action,
                 details=details)


# --- geometry cache (spec 14.1 #4) ------------------------------------------------

@dataclass
class _GeometryCache:
    """Parsed, transformed rotor by file identity plus transform; a few entries."""

    size: int = 4
    entries: OrderedDict[str, tuple[trimesh.Trimesh | None, tuple[Issue, ...]]] = field(
        default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    loads: int = 0  # STL parses so far (tests check reuse)

    def get(self, geometry: RotorGeometryConfig) -> tuple[trimesh.Trimesh | None,
                                                          tuple[Issue, ...]]:
        try:
            stat = Path(geometry.source_path).stat()
            identity = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            identity = None  # missing: load_rotor_geometry reports it; not cached
        key = json.dumps([str(geometry.source_path), identity,
                          geometry.model_dump(mode="json")], sort_keys=True)
        with self.lock:
            if identity is not None and key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key]
        loaded = load_rotor_geometry(geometry)
        with self.lock:
            self.loads += 1
            if identity is not None:
                self.entries[key] = loaded
                while len(self.entries) > self.size:
                    self.entries.popitem(last=False)
        return loaded


GEOMETRY_CACHE = _GeometryCache()


# --- the service ---------------------------------------------------------------------

class VawtService:
    def __init__(
        self,
        root: Path,
        *,
        pipeline: VawtPipeline | None = None,
        registry: RunRegistry | None = None,
        env: Mapping[str, str] | None = None,
        mounts: list[Mount] | None = None,
        system_probe: Callable[[Path], SystemResources] | None = None,
        estimator: ResourceEstimator | None = None,
        thresholds: ValidationThresholds | None = None,
        geometry_cache: _GeometryCache | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.env = env  # None: the process environment
        self.pipeline = pipeline or VawtPipeline(environment=env)
        self.registry = registry or REGISTRY
        self.mounts = mounts  # None: read /proc/mounts
        self.system_probe = system_probe or SystemResources.detect
        self.estimator = estimator or ResourceEstimator()
        self.thresholds = thresholds or ValidationThresholds()
        self.geometry = geometry_cache or GEOMETRY_CACHE
        self.store = VawtProjectStore(self.root)

    def _env(self) -> Mapping[str, str]:
        return self.env if self.env is not None else os.environ

    # --- environment and location ---------------------------------------------------

    def environment(self) -> EnvironmentInfo:
        """Where the app runs and whether OpenFOAM is usable from it."""
        env = self._env()
        root = projects_root(env)
        found = validate_environment(OpenFOAMProfile.OPENCFD, VAWT_EXECUTABLES, env,
                                     verified_versions=VERIFIED_VERSIONS)
        return EnvironmentInfo(
            wsl=is_wsl(env), distro=env.get("WSL_DISTRO_NAME") or None,
            openfoam_version=env.get("WM_PROJECT_VERSION") or None, projects_root=root,
            issues=(*found, *location_issues(root, self.mounts, env)))

    def location_issues(self) -> tuple[Issue, ...]:
        return location_issues(self.root, self.mounts, self._env())

    # --- configuration ----------------------------------------------------------------

    def load(self) -> LoadResult:
        location = self.location_issues()
        try:
            stored = self.store.load()
        except ValueError as exc:
            return LoadResult(None, None, (*location, _issue(
                IssueSeverity.ERROR, "PROJECT_FILE_UNREADABLE", str(exc),
                "Restore the file from .preprocessor/vawt/history/ or save again.",
                category=IssueCategory.INPUT, stage=IssueStage.CONFIGURATION)))
        if stored is None:
            return LoadResult(None, None, location)
        _, issues = parse_config(stored.raw)
        return LoadResult(stored.raw, stored.revision, (*location, *issues))

    def saved_config(self) -> VawtProjectConfig | None:
        loaded = self.load()
        return parse_config(loaded.raw)[0] if loaded.raw is not None else None

    def validate(self, raw: Any) -> ValidationOutcome:
        """Every check of spec section 8 (the STL is parsed once per file/transform)."""
        config, issues = parse_config(raw)
        if config is None:
            return ValidationOutcome(None, None, issues)
        # What case generation will report (zone cell size used, profile), before
        # any run; check_config repeats some of it, so duplicates are dropped.
        generation = VawtCaseGenerator().issues(config)
        size = zone_cell_size(config)
        mesh, geometry_issues = self.geometry.get(config.geometry)
        if mesh is None:
            return ValidationOutcome(config, None, (*geometry_issues, *generation), size)
        metrics = compute_rotor_metrics(mesh.vertices, config.rotor.axis)
        checks = check_config(config, mesh, metrics, self.thresholds)
        return ValidationOutcome(config, metrics,
                                 _unique((*geometry_issues, *checks, *generation)), size)

    def save(self, raw: Any) -> SaveResult:
        """Write a new revision if the configuration is well-formed. Engineering
        findings are returned, not enforced: the run's validation gate stops on
        ERROR or BLOCKING."""
        config, issues = parse_config(raw)
        if config is None:
            return SaveResult(None, issues)
        stored = self.store.save(config)
        return SaveResult(stored.revision, (*issues, *self.location_issues()))

    def store_source_file(self, filename: str, data: bytes) -> Path:
        """Copy an uploaded STL into the project's inputs/ (ValueError if not .stl)."""
        return ProjectService(self.root).store_source_file(filename, data)

    def rotor_metrics(self, geometry: Mapping[str, Any], axis: Axis | None = None,
                      ) -> tuple[RotorMetrics | None, tuple[Issue, ...]]:
        try:
            parsed = RotorGeometryConfig.model_validate(geometry)
        except ValidationError as exc:
            return None, tuple(issues_from_validation_error(exc))
        mesh, issues = self.geometry.get(parsed)
        if mesh is None:
            return None, issues
        return compute_rotor_metrics(mesh.vertices, axis), issues

    def draft_from_preset(self, base: Mapping[str, Any], axis: Axis, flow_axis: Axis,
                          kind: PresetKind = PresetKind.SIMPLE, *,
                          include_domain: bool = True,
                          ) -> tuple[dict[str, Any] | None, tuple[Issue, ...]]:
        """A draft from presets for the confirmed axes; nothing is saved."""
        metrics, issues = self.rotor_metrics(base.get("geometry", {}), axis)
        if metrics is None:
            return None, issues
        try:
            return preset_draft(dict(base), metrics, flow_axis, kind,
                                include_domain=include_domain), issues
        except ValueError as exc:
            return None, (*issues, _issue(
                IssueSeverity.ERROR, "PRESET_NOT_APPLICABLE", str(exc),
                "Confirm the rotor axis and choose a different flow axis.",
                category=IssueCategory.CONFIGURATION, stage=IssueStage.CONFIGURATION))

    # --- sections, plan, preflight ---------------------------------------------------

    def _last_meshed_config(self) -> VawtProjectConfig | None:
        last = read_last_meshed(self.root)
        return parse_config(last.raw)[0] if last is not None else None

    def _stale_paths(self, config: VawtProjectConfig) -> set[str]:
        """Changed settings that make a mesh stale (not just its validation)."""
        old = self._last_meshed_config()
        if old is None:
            return set()
        plan = plan_changes(old, config)
        paths: set[str] = set()
        for op in plan.operations:
            if op in CACHED:
                paths |= plan.reasons.get(op, frozenset())
        return paths - {LAYOUT}

    def section_status(self, raw: Mapping[str, Any] | None = None,
                       ) -> dict[str, SectionStatus]:
        """Per setup section, for a draft (default: the saved configuration)."""
        if raw is None:
            raw = self.load().raw
        if raw is None:
            return {s: SectionStatus(SectionState.EMPTY) for s in SETUP_SECTIONS}
        outcome = self.validate(dict(raw))
        found: dict[str, list[Issue]] = {s: [] for s in SETUP_SECTIONS}
        for issue in outcome.issues:
            section = section_of_issue(issue)
            if section in found:
                found[section].append(issue)
        stale: dict[str, list[str]] = {s: [] for s in SETUP_SECTIONS}
        if outcome.config is not None:
            for path in sorted(self._stale_paths(outcome.config)):
                section = section_of_field(path)
                if section in stale:
                    stale[section].append(path)
        geometry = raw.get("geometry")
        empty = {
            "project": not raw.get("project_name"),
            "geometry": not (isinstance(geometry, Mapping) and geometry.get("source_path")),
            "rotating_zone": raw.get("rotating_zone") is None,
        }
        result = {}
        for section in SETUP_SECTIONS:
            issues = tuple(found[section])
            if empty.get(section, False) and not issues:
                state = SectionState.EMPTY
            elif any(i.code in _UNSET_CODES
                     or i.details.get("error_type") in UNSET_ERROR_TYPES for i in issues):
                state = SectionState.INCOMPLETE
            elif has_stopping_issue(issues):
                state = SectionState.ERROR
            elif stale[section]:
                state = SectionState.STALE
            else:
                state = SectionState.READY
            result[section] = SectionStatus(state, issues, tuple(stale[section]))
        return result

    def plan(self, raw: Mapping[str, Any] | None = None) -> PlanView:
        """Which operations a run would execute and why, from the configuration
        alone (the run itself re-verifies cached files)."""
        config, issues = (parse_config(dict(raw)) if raw is not None
                          else (self.saved_config(), ()))
        if config is None:
            return PlanView((), {}, False, issues or (_issue(
                IssueSeverity.INFO, "NO_SAVED_CONFIGURATION",
                "There is no saved configuration to plan.", "Save the configuration.",
                stage=IssueStage.CONFIGURATION),))
        old = self._last_meshed_config()
        plan = PLANNER.plan_full(config) if old is None else plan_changes(old, config)
        return PlanView(plan.operations,
                        {op: tuple(sorted(plan.reasons.get(op, ()))) for op in plan.operations},
                        old is None, issues)

    def preflight(self, raw: Mapping[str, Any] | None = None) -> PreflightView:
        """Arithmetic estimate on a draft (the STL comes from the geometry cache)."""
        config, issues = (parse_config(dict(raw)) if raw is not None
                          else (self.saved_config(), ()))
        if config is None:
            return PreflightView(None, None, issues)
        mesh, geometry_issues = self.geometry.get(config.geometry)
        if mesh is None:
            return PreflightView(None, None, (*issues, *geometry_issues))
        estimate, assessment = assess(config, float(mesh.area), self.system_probe(self.root),
                                      self.estimator)
        return PreflightView(estimate, assessment, (*issues, *assessment.issues))

    # --- runs ---------------------------------------------------------------------

    def start_run(self, *, allow_high_resource_risk: bool = False,
                  force: bool = False) -> StartResult:
        """Start a background run of the saved configuration and return at once.

        If a run of this project is active, nothing starts: its run ID comes
        back instead (a refreshed page or second tab re-attaches)."""
        location = self.location_issues()
        loaded = self.load()
        config = parse_config(loaded.raw)[0] if loaded.raw is not None else None
        if config is None:
            return StartResult(False, None, (*loaded.issues, _issue(
                IssueSeverity.BLOCKING, "NO_SAVED_CONFIGURATION",
                "There is no valid saved configuration to run.",
                "Save the configuration first; runs use the saved configuration only.",
                stage=IssueStage.CONFIGURATION)))
        status = self.run_status()
        if status.state is RunView.RUNNING:
            return StartResult(False, status.run_id, (_issue(
                IssueSeverity.INFO, "RUN_ALREADY_ACTIVE",
                "A run of this project is already active; showing it.",
                "Follow its progress, or cancel it before starting another."),))
        if (status.state in (RunView.RUNNING_ELSEWHERE, RunView.LOCK_UNREADABLE)
                or status.orphan_pid is not None):
            return StartResult(False, status.run_id, status.issues)
        extra = ({"previous_run_interrupted": status.run_id}
                 if status.state is RunView.INTERRUPTED else {})
        run_id = uuid.uuid4().hex

        def work(cancel_event: Any) -> Any:
            return self.pipeline.run(
                self.root, config, force=force,
                allow_high_resource_risk=allow_high_resource_risk,
                cancel_event=cancel_event, run_id=run_id, record_extra=extra)

        active, started = self.registry.start(self.root, run_id, work)
        if not started:
            return StartResult(False, active.run_id, (_issue(
                IssueSeverity.INFO, "RUN_ALREADY_ACTIVE",
                "A run of this project is already active; showing it.",
                "Follow its progress, or cancel it before starting another."),))
        return StartResult(True, run_id, location)

    def cancel_run(self) -> bool:
        """Cancel this process's active run of the project. False if there is none
        (a run held by another process cannot be cancelled from here)."""
        return self.registry.cancel(self.root)

    def run_status(self) -> RunStatusView:
        status = read_status(self.root)
        active = self.registry.latest(self.root)
        if active is not None and not active.done:
            own = status if status is not None and status.get("run_id") == active.run_id else None
            return self._view(RunView.RUNNING, own, run_id=active.run_id, can_cancel=True)
        lock = RunLock(self.root)
        if self._unreadable_lock(lock):
            return self._view(RunView.LOCK_UNREADABLE, status, issues=(_issue(
                IssueSeverity.BLOCKING, "RUN_LOCK_UNREADABLE",
                f"The run lock {lock.path} names no run; it was left by a process that "
                "stopped while creating it.",
                "Remove the lock (remove_unreadable_lock, after confirming that no run "
                "of this project is active anywhere), then start the run again.",
                explanation="No run can start while the lock exists, and the lock does "
                "not say which process holds it.", lock=str(lock.path)),))
        if lock.is_held_by_live_run():
            running = status if status is not None and status.get("state") == "RUNNING" else None
            return self._view(RunView.RUNNING_ELSEWHERE, running, issues=(_issue(
                IssueSeverity.BLOCKING, "RUN_IN_PROGRESS",
                "Another process is running this project; it cannot be cancelled from here.",
                "Wait for it to finish, or stop it where it was started.",
                holder=lock.holder()),))
        if status is None:
            return RunStatusView(RunView.NONE)
        if status.get("state") == RunState.RUNNING.value:
            return self._interrupted(status)
        state = RunView(status.get("state", RunView.FAILED.value))
        issues: tuple[Issue, ...] = ()
        if active is not None and active.run_id == status.get("run_id") and active.result:
            issues = active.result.issues
        return self._view(state, status, issues=issues)

    def _view(self, state: RunView, status: Mapping[str, Any] | None, *,
              run_id: str | None = None, can_cancel: bool = False, orphan_pid: int | None = None,
              issues: tuple[Issue, ...] = ()) -> RunStatusView:
        status = status or {}
        command = status.get("command") or {}
        return RunStatusView(
            state=state, run_id=run_id or status.get("run_id"), stage=status.get("stage"),
            step=int(status.get("step", 0)), total_steps=int(status.get("total_steps", 0)),
            started_at=status.get("started_at"), updated_at=status.get("updated_at"),
            message=str(status.get("message", "")), can_cancel=can_cancel,
            log=command.get("log") if isinstance(command, Mapping) else None,
            orphan_pid=orphan_pid, issues=issues)

    def _interrupted(self, status: Mapping[str, Any]) -> RunStatusView:
        """The status says running but no live run holds the lock: the process
        that ran it ended without finishing (crash, kill, WSL shutdown)."""
        issues = [_issue(
            IssueSeverity.WARNING, "RUN_INTERRUPTED",
            f"The run {str(status.get('run_id', ''))[:8]} stopped without finishing, during "
            f"{status.get('stage')}.",
            "Start the run again: the interrupted operation re-runs; earlier results are "
            "reused only if their files still verify.",
            explanation="The process running it ended (crash, kill or WSL shutdown).",
            run_id=status.get("run_id"), run_stage=status.get("stage"))]
        orphan = orphaned_command(status)
        if orphan is not None:
            command = status.get("command") or {}
            issues.append(_issue(
                IssueSeverity.BLOCKING, "ORPHANED_OPENFOAM_PROCESS",
                f"{command.get('name')} (PID {orphan}) from the interrupted run is still "
                "running and writing into the project.",
                "Terminate it (terminate_orphan) or wait for it to end before starting "
                "another run.", pid=orphan, command=command))
        return self._view(RunView.INTERRUPTED, status, orphan_pid=orphan, issues=tuple(issues))

    @staticmethod
    def _unreadable_lock(lock: RunLock) -> bool:
        try:
            age = time.time() - lock.path.stat().st_mtime
        except OSError:
            return False
        return lock.holder() is None and age > UNREADABLE_LOCK_AGE_S

    def remove_unreadable_lock(self) -> bool:
        """Remove a run lock that names no run (only after the user confirmed).
        False, and nothing removed, if the lock is readable, recent or absent."""
        lock = RunLock(self.root)
        if not self._unreadable_lock(lock):
            return False
        lock.path.unlink(missing_ok=True)
        return True

    def terminate_orphan(self) -> bool:
        """Terminate the OpenFOAM process left by an interrupted run (only after
        the user confirmed). False if there is none."""
        pid = self.run_status().orphan_pid
        if pid is None:
            return False
        try:
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(ORPHAN_TERMINATE_WAIT_S)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(ORPHAN_TERMINATE_WAIT_S)
        except psutil.NoSuchProcess:
            pass
        return True

    # --- outputs ----------------------------------------------------------------------

    def reports(self) -> dict[str, dict[str, Any] | None]:
        result: dict[str, dict[str, Any] | None] = {}
        for name, relative in (("geometry", GEOMETRY_REPORT), ("preflight", PREFLIGHT_REPORT),
                               ("mesh", MESH_REPORT)):
            try:
                data = json.loads((self.root / relative).read_text(encoding="utf-8"))
                result[name] = data if isinstance(data, dict) else None
            except (OSError, ValueError):
                result[name] = None
        return result

    def runs(self) -> list[dict[str, Any]]:
        return [r for r in list_run_records(self.root) if r.get("kind") == "vawt_mesh"]

    def log_tail(self, max_bytes: int = LOG_TAIL_BYTES) -> LogTail:
        """The end of the active log (or the most recent one), read by seek:
        at most max_bytes, whatever the log's size."""
        status = read_status(self.root) or {}
        command = status.get("command") or {}
        relative = command.get("log") if isinstance(command, Mapping) else None
        path = self.root / relative if isinstance(relative, str) else None
        if path is None or not path.is_file():
            logs = [p for p in (self.root / "logs").glob("*/*.log")
                    if not p.name.endswith(".stderr.log")]
            if not logs:
                return LogTail(None, "", False, 0)
            path = max(logs, key=lambda p: p.stat().st_mtime_ns)
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            stream.seek(start)
            data = stream.read(max_bytes)
        if start > 0:  # drop the partial first line
            newline = data.find(b"\n")
            data = data[newline + 1:] if newline >= 0 else data
        return LogTail(path.relative_to(self.root).as_posix(),
                       data.decode("utf-8", errors="replace"), start > 0, size,
                       bytes_read=min(size, max_bytes))


def orphaned_command(status: Mapping[str, Any]) -> int | None:
    """The PID of the status file's command if that very process still runs."""
    command = status.get("command")
    if not isinstance(command, Mapping):
        return None
    pid, recorded = command.get("pid"), command.get("pid_create_time")
    if not isinstance(pid, int) or not isinstance(recorded, int | float):
        return None
    current = process_create_time(pid)
    if current is None or abs(current - recorded) > CREATE_TIME_TOLERANCE_S:
        return None
    return pid
