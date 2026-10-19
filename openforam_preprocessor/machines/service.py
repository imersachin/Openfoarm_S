"""MachineService: the only interface the machines UI calls (G7;
docs/rotating_machinery.md sections 13 and 27).

Built like VawtService (spec section 12 of the VAWT workflow): plain data and
structured issues in, plain data out, no Streamlit types. Runs use the saved
configuration only and go to the background worker with the same run lock,
status file and re-attach as VAWT (RunControl). A run meshes and, when the
project has physics, sets up the case; it never solves.

Per machine, a profile (machines.profiles) says which sections exist and
which configuration fields each owns; section status follows it.
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import os
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from case_setup.physics import (
    FlowRateInlet,
    RpmSpeed,
    TipSpeedRatio,
    VelocityInlet,
    rpm_to_rad_s,
)
from case_setup.setup import inlet_area
from case_setup.validation import CaseSetupThresholds, check_physics
from case_setup.wall_cells import WallEstimate, first_layer_for, y_plus_of
from core.artifacts.hashing import sha256_json
from core.config.models import MeshQualityLimits, OpenFOAMProfile
from core.config.validation import issues_from_validation_error
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from core.workflow.run_lock import RunLock
from core.workflow.run_records import list_run_records
from machines.config import (
    CylinderZone,
    ImportedDomain,
    MachineProjectConfig,
    MachineType,
    Motion,
    PatchType,
    SourceKind,
    StlSource,
)
from machines.joints import propose_joints
from machines.mesh_folder import MeshFolderReport, check_mesh_folder
from machines.msh_import.cases import IMPORTED_CASE, SCRATCH_CASE, converter
from machines.msh_import.checks import patch_areas
from machines.msh_import.checks import zone_radius as mesh_zone_radius
from machines.msh_import.inspection import MeshInspection, PatchGeometry, patch_geometry
from machines.msh_import.inspection import inspect as inspect_mesh_file_view
from machines.msh_import.pipeline import (
    IMPORT_REPORT,
    SCRATCH_RECORD,
    ScratchResult,
    convert_scratch,
    import_operations,
    preflight_cells,
    scratch_box,
)
from machines.msh_import.revolution import EXECUTABLES as REVOLUTION_EXECUTABLES
from machines.msh_import.revolution import RevolutionResult, check_revolution, mesh_hash
from machines.msh_import.validation import read_mesh_file
from machines.operations import MACHINE_EXECUTABLES, operations_for
from machines.pipeline import (
    CASE_SETUP_REPORT,
    MESH_REPORT,
    PREFLIGHT_REPORT,
    VERIFIED_VERSIONS,
    MachinePipeline,
    vawt_workflow_issues,
)
from machines.preflight import MachineCellEstimate, assess
from machines.presets import PRESET_NOTE
from machines.presets.francis import francis_draft, slots_base, specific_speed
from machines.presets.hawt import hawt_draft, hawt_rotor
from machines.presets.vawt import vawt_draft
from machines.previews import (
    PreviewLimits,
    cut_planes,
    flow_arrows,
    mesh_preview,
    mesh_signature,
    patch_kinds,
    rotation_arrows,
    setup_preview,
    setup_signature,
    stl_paths,
    zone_frames_of,
)
from machines.profiles import MachineProfile, PresetKind, ProfileRegistry, default_registry
from machines.project_store import (
    CONFIG_PATH,
    MachineProjectStore,
    MachineUiSettings,
    WallReference,
    read_last_meshed,
    read_ui_settings,
    write_ui_settings,
)
from machines.validation import CheckedConfig, MachineThresholds, check_all, parse_config
from mesh.estimator import CellEstimate, ResourceAssessment, ResourceEstimator, SystemResources
from openfoam.environment import tool_identity, validate_environment
from vawt.config import Axis, RotorGeometryConfig
from vawt.presets import PresetKind as VawtPresetKind
from vawt.rotor_metrics import RotorMetrics, compute_rotor_metrics
from vawt.runtime import REGISTRY, RunRegistry
from vawt.service import (
    GEOMETRY_CACHE,
    EnvironmentInfo,
    LoadResult,
    PreviewData,
    PreviewKey,
    PreviewView,
    RunControl,
    RunView,
    SaveResult,
    SectionState,
    SectionStatus,
    StartResult,
    _GeometryCache,
)
from vawt.validation import UNSET_ERROR_TYPES
from vawt.workspace import Mount, is_wsl, location_issues

INPUTS_DIR = "inputs"
MESH_INPUTS = "mesh"  # inputs/mesh/: uploaded .msh files (M1-MSH)
MAX_MESH_UPLOAD_BYTES = 200 * 2**20  # Streamlit's default upload limit
PROJECTS_DIR_ENV = "MACHINES_PROJECTS_DIR"
DEFAULT_PROJECTS_DIR = "~/machine_projects"
CASE_DIR = "case"
CHECK_CACHE_SIZE = 4
# Values never assumed: a section with one of these is incomplete, not wrong.
UNSET_CODES = frozenset({
    "MACHINE_NOT_CHOSEN", "MOTION_NOT_CHOSEN", "ZONE_AXIS_NOT_CHOSEN", "PATCH_TYPE_NOT_CHOSEN",
    "UNITS_NOT_CHOSEN", "ROTATION_NOT_SET", "ROTATION_DIRECTION_NOT_SET", "INLET_VALUE_NOT_SET",
    "OUTLET_VALUE_NOT_SET", "TURBULENCE_LENGTH_SCALE_NOT_SET", "FREE_STREAM_SPEED_NOT_SET"})
PHYSICS_SECTIONS = frozenset({"fluid", "boundary", "rotation", "operating_point", "time"})
FLUENT_DROPPED = ("Fluent .msh export is turned off: this workflow does not write it (the "
                  "VAWT app does; export comes with V7).")
WAKE_DROPPED = ("The VAWT preset's wake refinement box is left out: this workflow meshes "
                "without one (the VAWT app keeps it). The wake is resolved by the domain "
                "and zone cells only.")


def _issue(severity: IssueSeverity, code: str, message: str, action: str, *,
           category: IssueCategory = IssueCategory.CONFIGURATION,
           stage: IssueStage = IssueStage.CONFIGURATION, **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=stage, code=code,
                 message=message, suggested_action=action, details=details)


def _unique(issues: Sequence[Issue]) -> tuple[Issue, ...]:
    seen: set[tuple[str, str]] = set()
    kept = []
    for issue in issues:
        if (issue.code, issue.message) not in seen:
            seen.add((issue.code, issue.message))
            kept.append(issue)
    return tuple(kept)


# --- results of the calls -----------------------------------------------------------

@dataclass(frozen=True)
class MachineValidation:
    config: MachineProjectConfig | None
    issues: tuple[Issue, ...]

    @property
    def can_run(self) -> bool:
        return self.config is not None and not has_stopping_issue(self.issues)


@dataclass(frozen=True)
class MachineAnalysis:
    outcome: MachineValidation
    sections: dict[str, SectionStatus]


@dataclass(frozen=True)
class RotorInfo:
    """A rotor STL after units and transform, for the preset boxes."""

    metrics: RotorMetrics  # extents, axis suggestion (VAWT)
    diameter: float | None  # swept diameter about the confirmed axis (HAWT measure)
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class DraftResult:
    draft: dict[str, Any] | None
    issues: tuple[Issue, ...] = ()
    notes: tuple[str, ...] = ()
    # Francis: joints proposed (first, second, details) and suggestions.
    joints: tuple[tuple[str, str, dict[str, float]], ...] = ()
    outlet_diameters: dict[str, float] = field(default_factory=dict)
    suggested_types: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MachinePlanView:
    operations: tuple[str, ...]
    from_scratch: bool  # no mesh yet: everything runs
    case_setup: bool  # the run also sets up the case (the project has physics)
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class MachinePreflightView:
    estimate: MachineCellEstimate | None
    assessment: ResourceAssessment | None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class SpeedSuggestion:
    label: str
    speed: float  # m/s


@dataclass(frozen=True)
class WallEstimates:
    forward: WallEstimate | None  # y+ of the first layer given
    reverse: WallEstimate | None  # first layer for the target y+
    notes: tuple[str, ...] = ()


# --- the check cache ---------------------------------------------------------------

@dataclass
class _CheckCache:
    """check_all by configuration (without physics) and STL file identity."""

    size: int = CHECK_CACHE_SIZE
    entries: OrderedDict[str, CheckedConfig] = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    runs: int = 0  # check_all calls so far (tests check reuse)

    def get(self, config: MachineProjectConfig,
            thresholds: MachineThresholds) -> CheckedConfig:
        stats = []
        mesh = [config.imported_mesh.source_path] if config.imported_mesh else []
        for path in (*stl_paths(config), *mesh):
            try:
                info = path.stat()
                stats.append([str(path), info.st_size, info.st_mtime_ns])
            except OSError:
                stats.append([str(path), None, None])
        data = config.model_dump(mode="json")
        data.pop("physics", None)  # physics is checked apart (check_physics)
        key = sha256_json([data, stats, thresholds.__dict__])
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key]
        checked = check_all(config, thresholds)
        with self.lock:
            self.runs += 1
            self.entries[key] = checked
            while len(self.entries) > self.size:
                self.entries.popitem(last=False)
        return checked


# --- the service -----------------------------------------------------------------------

class MachineService(RunControl):
    def __init__(
        self,
        root: Path,
        *,
        pipeline: MachinePipeline | None = None,
        registry: RunRegistry | None = None,
        env: Mapping[str, str] | None = None,
        mounts: list[Mount] | None = None,
        system_probe: Callable[[Path], SystemResources] | None = None,
        estimator: ResourceEstimator | None = None,
        thresholds: MachineThresholds | None = None,
        case_thresholds: CaseSetupThresholds | None = None,
        profiles: ProfileRegistry | None = None,
        preview_limits: PreviewLimits | None = None,
        geometry_cache: _GeometryCache | None = None,
        check_cache: _CheckCache | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.env = env  # None: the process environment
        self.pipeline = pipeline or MachinePipeline(environment=env)
        self.registry = registry or REGISTRY
        self.mounts = mounts
        self.system_probe = system_probe or SystemResources.detect
        self.estimator = estimator or ResourceEstimator()
        self.thresholds = thresholds or MachineThresholds()
        self.case_thresholds = case_thresholds or CaseSetupThresholds()
        self.profiles = profiles or default_registry()
        self.preview_limits = preview_limits or PreviewLimits()
        self.geometry = geometry_cache or GEOMETRY_CACHE
        self.checks = check_cache or _CHECKS
        self.store = MachineProjectStore(self.root)

    def _env(self) -> Mapping[str, str]:
        return self.env if self.env is not None else os.environ

    # --- environment and location ---------------------------------------------------

    def environment(self) -> EnvironmentInfo:
        env = self._env()
        root = machines_projects_root(env)
        found = validate_environment(OpenFOAMProfile.OPENCFD, MACHINE_EXECUTABLES, env,
                                     verified_versions=VERIFIED_VERSIONS)
        return EnvironmentInfo(
            wsl=is_wsl(env), distro=env.get("WSL_DISTRO_NAME") or None,
            openfoam_version=env.get("WM_PROJECT_VERSION") or None, projects_root=root,
            issues=(*found, *location_issues(root, self.mounts, env)))

    def location_issues(self) -> tuple[Issue, ...]:
        return location_issues(self.root, self.mounts, self._env())

    # --- profiles and UI settings --------------------------------------------------------

    def ui_settings(self) -> MachineUiSettings:
        return read_ui_settings(self.root)

    def save_ui_settings(self, settings: MachineUiSettings) -> None:
        write_ui_settings(self.root, settings)

    def profile(self) -> MachineProfile | None:
        """The project's profile: the one chosen, else the first for the saved
        configuration's machine type, else None (choose one)."""
        chosen = self.ui_settings().profile
        if chosen is not None and self.profiles.get(chosen) is not None:
            return self.profiles.get(chosen)
        raw = self._saved_raw()
        machine = raw.get("machine") if isinstance(raw, Mapping) else None
        try:
            return self.profiles.for_machine(MachineType(str(machine))) if machine else None
        except ValueError:
            return None

    def choose_profile(self, profile_id: str) -> MachineProfile:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Unknown machine profile '{profile_id}'.")
        self.save_ui_settings(self.ui_settings().model_copy(update={"profile": profile_id}))
        return profile

    @staticmethod
    def new_draft(profile: MachineProfile, project_name: str) -> dict[str, Any]:
        """The draft of a new project: its name and machine type; a preset or
        the sections fill in the rest."""
        return {"project_name": project_name, "machine": profile.machine.value,
                "bodies": [], "rotating_zones": [], "patches": [], "joints": []}

    # --- configuration ----------------------------------------------------------------

    def _saved_raw(self) -> dict[str, Any] | None:
        try:
            stored = self.store.load()
        except ValueError:
            return None  # load() reports it
        return stored.raw if stored is not None else None

    def load(self) -> LoadResult:
        location = self.location_issues()
        try:
            stored = self.store.load()
        except ValueError as exc:
            return LoadResult(None, None, (*location, _issue(
                IssueSeverity.ERROR, "PROJECT_FILE_UNREADABLE", str(exc),
                "Restore the file from .preprocessor/machines/history/ or save again.",
                category=IssueCategory.INPUT)))
        if stored is None:
            return LoadResult(None, None, location)
        _, issues = parse_config(stored.raw)
        return LoadResult(stored.raw, stored.revision, (*location, *issues))

    def saved_config(self) -> MachineProjectConfig | None:
        raw = self._saved_raw()
        return parse_config(raw)[0] if raw is not None else None

    def validate(self, raw: Any) -> MachineValidation:
        """Every check a run's validation gate makes, and the physics findings
        (which stop case setup only). STL files are read once per change.

        While only the physics section is malformed (being filled in), the
        rest is still checked, so the setup sections keep their findings; the
        result has no configuration then (the draft cannot be saved)."""
        config, issues = parse_config(raw)
        if config is None:
            rest = without_physics(raw, issues)
            if rest is None:
                return MachineValidation(None, issues)
            checked = self.checks.get(rest, self.thresholds)
            return MachineValidation(None, _unique((
                *issues, *vawt_workflow_issues(rest), *checked.issues)))
        checked = self.checks.get(config, self.thresholds)
        physics = check_physics(config, checked.surfaces, self.case_thresholds,
                                mesh_areas=None if config.imported_mesh is None
                                else self.imported_patch_areas(config))
        return MachineValidation(config, _unique((
            *vawt_workflow_issues(config), *checked.issues, *physics)))

    def save(self, raw: Any) -> SaveResult:
        """A new revision if the configuration is well-formed. Engineering
        findings are returned, not enforced: the run's gate stops on them."""
        config, issues = parse_config(raw)
        if config is None:
            return SaveResult(None, issues)
        stored = self.store.save(config)
        return SaveResult(stored.revision, (*issues, *self.location_issues()))

    def store_source_file(self, filename: str, data: bytes, folder: str | None = None) -> Path:
        """Copy an uploaded STL into inputs/ (inputs/<folder>/ for a named slot, so
        two slots' files never overwrite each other). ValueError if not .stl."""
        name = Path(filename).name
        if Path(name).suffix.lower() != ".stl" or not Path(name).stem:
            raise ValueError("Only .stl files can be added as geometry.")
        directory = self.root / INPUTS_DIR
        if folder:
            if Path(folder).name != folder or folder in (".", ".."):
                raise ValueError(f"'{folder}' is not a folder name.")
            directory = directory / folder
        target = (directory / name).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    # --- imported meshes (M1-MSH) ---------------------------------------------------------

    def store_mesh_file(self, filename: str, data: bytes) -> Path:
        """Copy an uploaded .msh into inputs/mesh/. Its format is read from the
        content later, never from the name. ValueError above the upload limit
        (a larger file is chosen by its path instead, and read where it is)."""
        name = Path(filename).name
        if not name or name in (".", ".."):
            raise ValueError("The file has no name.")
        if len(data) > MAX_MESH_UPLOAD_BYTES:
            raise ValueError(f"The file is larger than {MAX_MESH_UPLOAD_BYTES // 2**20} MB; "
                             "give its path on this machine instead.")
        target = (self.root / INPUTS_DIR / MESH_INPUTS / name).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def inspect_mesh(self, path: Path) -> MeshInspection:
        """Format, names, zones, box and suggestions of a .msh file. The box is the
        file's own when it can be read cheaply, else the box of a scratch
        conversion of this same file, if one was made (convert_for_box)."""
        info, digest = read_mesh_file(Path(path))
        scratch = None
        if info.readable and info.box is None and digest is not None:
            scratch = scratch_box(self.root, info, digest,
                                  self._tool_identity((converter(info),)))
        return inspect_mesh_file_view(Path(path), scratch)

    def _profile(self) -> OpenFOAMProfile:
        saved = self.saved_config()
        return saved.openfoam_profile if saved is not None else OpenFOAMProfile.OPENCFD

    def _tool_identity(self, names: Sequence[str]) -> str:
        """As MachinePipeline._tool for the saved project's OpenFOAM profile."""
        return sha256_json(tool_identity(self._profile(), names, self._env()))

    def convert_for_box(self, path: Path) -> ScratchResult:
        """Convert the file once at scale 1 into cases/imported_scratch/ to show its
        box "from the converted mesh" (owner answer 4). Under the run lock; never a
        verified mesh: an import moves it into place only after checking it."""
        info, digest = read_mesh_file(Path(path))
        if not info.readable or digest is None:
            return ScratchResult(None, converter(info), None, info.issues)
        lock = RunLock(self.root)
        busy = lock.acquire()
        if busy is not None:
            return ScratchResult(None, converter(info), None, (busy,))
        try:
            names = (converter(info),)
            found = validate_environment(self._profile(), names, self._env(),
                                         verified_versions=VERIFIED_VERSIONS)
            if has_stopping_issue(found):
                return ScratchResult(None, converter(info), None, tuple(found))
            tool = self._tool_identity(names)
            return asyncio.run(convert_scratch(self.root, info, digest,
                                               runner=self.pipeline.runner, env=self.env,
                                               tool=tool))
        finally:
            lock.release()

    def check_ami_revolution(self) -> RevolutionResult:
        """The saved imported mesh's AMI weights over one revolution, the zone
        turned without solving the flow (optional; machines.msh_import.revolution).
        Under the run lock, on a copy of the assembled mesh."""
        config = self.saved_config()
        digest = mesh_hash(self.root)
        if config is None or config.imported_mesh is None:
            return RevolutionResult((), 0, 0, digest, (_issue(
                IssueSeverity.BLOCKING, "AMI_REVOLUTION_UNAVAILABLE",
                "The saved project has no imported mesh.",
                "Save an imported-mesh project and run the import first."),))
        lock = RunLock(self.root)
        busy = lock.acquire()
        if busy is not None:
            return RevolutionResult((), 0, 0, digest, (busy,))
        try:
            found = validate_environment(config.openfoam_profile, REVOLUTION_EXECUTABLES,
                                         self._env(), verified_versions=VERIFIED_VERSIONS)
            if has_stopping_issue(found):
                return RevolutionResult((), 0, 0, digest, tuple(found))
            return asyncio.run(check_revolution(
                self.root, config, runner=self.pipeline.runner, env=self.env,
                ami_range=self.thresholds.imported_ami_weights_range))
        finally:
            lock.release()

    def mesh_patch_geometry(self, names: Sequence[str],
                            sha256: str) -> dict[str, PatchGeometry]:
        """Area and centroid of patches of the converted mesh of the file with this
        content hash (the imported one, else the scratch conversion): evidence for
        a proposed AMI pair. Empty when neither is of this file."""
        records = ((IMPORTED_CASE, self.root / IMPORT_REPORT),
                   (SCRATCH_CASE, self.root / "cases" / SCRATCH_CASE / SCRATCH_RECORD))
        for case, record in records:
            folder = self.root / "cases" / case
            try:
                found = json.loads(record.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if (isinstance(found, dict) and found.get("sha256") == sha256
                    and (folder / "constant" / "polyMesh" / "boundary").is_file()):
                return patch_geometry(folder, tuple(names))
        return {}

    def imported_patch_areas(self, config: MachineProjectConfig) -> dict[str, float] | None:
        """Each configured patch's area in m^2, measured from the mesh: the
        imported mesh of this same file, else its scratch conversion, scaled to the
        configured units. None when neither is of this file (before conversion)."""
        mesh = config.imported_mesh
        assert mesh is not None
        records = ((IMPORTED_CASE, self.root / IMPORT_REPORT, "scale_to_metres"),
                   (SCRATCH_CASE, self.root / "cases" / SCRATCH_CASE / SCRATCH_RECORD, "scale"))
        refs = {p.name: p.source.ref for p in config.patches
                if p.source.kind is SourceKind.MESH_PATCH}
        for case, record, key in records:
            folder = self.root / "cases" / case
            try:
                found = json.loads(record.read_text("utf-8"))
                done = float(found[key])
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if found.get("sha256") != mesh.sha256 or done <= 0 or not (
                    folder / "constant" / "polyMesh" / "boundary").is_file():
                continue
            factor = (mesh.source_units.to_metres / done) ** 2
            areas = _patch_areas(folder, tuple(sorted(set(refs.values()))))
            return {name: areas[ref] * factor for name, ref in refs.items() if ref in areas}
        return None

    # --- sections ------------------------------------------------------------------------

    def analyse(self, raw: Mapping[str, Any] | None, profile: MachineProfile) -> MachineAnalysis:
        """Validation and per-section status of a draft (default: the saved one)."""
        if raw is None:
            raw = self._saved_raw()
        if raw is None:
            return MachineAnalysis(MachineValidation(None, ()), {
                s.key: SectionStatus(SectionState.EMPTY) for s in profile.sections})
        outcome = self.validate(dict(raw))
        return MachineAnalysis(outcome, self._sections(raw, outcome, profile))

    @staticmethod
    def section_of_issue(issue: Issue, profile: MachineProfile) -> str | None:
        """The section a finding belongs to: by its field, else by what it names."""
        details = issue.details
        candidates = [details.get("field"), details.get("owner")]
        if details.get("body"):
            candidates.append("bodies")
        if details.get("zone"):
            candidates.append("rotating_zones")
        if details.get("patch") or details.get("region"):
            candidates.append("patches")
        for path in candidates:
            if isinstance(path, str) and path:
                section = profile.section_of_field(path)
                if section is not None:
                    return section
        return None

    def _sections(self, raw: Mapping[str, Any], outcome: MachineValidation,
                  profile: MachineProfile) -> dict[str, SectionStatus]:
        found: dict[str, list[Issue]] = {s.key: [] for s in profile.sections}
        for issue in outcome.issues:
            section = self.section_of_issue(issue, profile)
            if section in found:
                found[section].append(issue)
        stale = self._stale_sections(raw, profile)
        physics = raw.get("physics") if isinstance(raw.get("physics"), Mapping) else None
        empty = {
            "project": not raw.get("project_name"),
            "bodies": not raw.get("bodies"),
            "parts": not raw.get("domain"),
            "domain": not raw.get("domain"),
            "zones": not raw.get("rotating_zones"),
            "patches": not raw.get("patches"),
            **dict.fromkeys(PHYSICS_SECTIONS, physics is None),
        }
        if physics is not None:
            empty["boundary"] = not (physics.get("inlets") or physics.get("outlets"))
            empty["rotation"] = not physics.get("rotation")
            empty["operating_point"] = not (physics.get("rotation") or physics.get("inlets"))
        result = {}
        for spec in profile.sections:
            issues = tuple(found.get(spec.key, ()))
            if empty.get(spec.key, False) and not issues:
                state = SectionState.EMPTY
            elif any(i.code in UNSET_CODES or i.details.get("error_type") in UNSET_ERROR_TYPES
                     for i in issues):
                state = SectionState.INCOMPLETE
            elif has_stopping_issue(issues):
                state = SectionState.ERROR
            elif stale.get(spec.key):
                state = SectionState.STALE
            else:
                state = SectionState.READY
            result[spec.key] = SectionStatus(state, issues, tuple(stale.get(spec.key, ())))
        return result

    def _stale_sections(self, raw: Mapping[str, Any],
                        profile: MachineProfile) -> dict[str, list[str]]:
        """Changed settings since the last run that produced meshes, by section."""
        last = read_last_meshed(self.root)
        if last is None:
            return {}
        # Both as the model writes them: a default left out of the draft is no change.
        old, _ = parse_config(last.raw)
        new, issues = parse_config(dict(raw))
        if new is None:
            new = without_physics(raw, issues)
        before = old.model_dump(mode="json") if old is not None else last.raw
        after = new.model_dump(mode="json") if new is not None else dict(raw)
        if new is not None and new.physics is None and isinstance(raw.get("physics"), Mapping):
            after["physics"] = raw["physics"]  # being filled in: compare it as it is
        changed: dict[str, list[str]] = {}
        for path in _changed_paths(before, after):
            section = profile.section_of_field(path)
            if section is not None:
                changed.setdefault(section, []).append(path)
        return changed

    # --- presets ----------------------------------------------------------------------

    def rotor_info(self, source: Mapping[str, Any], axis: Axis | None = None,
                   ) -> tuple[RotorInfo | None, tuple[Issue, ...]]:
        """A rotor STL after units and transform: extents, the axis suggestion and,
        for a confirmed axis, the swept diameter about it."""
        try:
            stl = StlSource.model_validate(source)
        except ValidationError as exc:
            return None, tuple(issues_from_validation_error(exc))
        mesh, issues = self.geometry.get(_rotor_geometry(stl))
        if mesh is None:
            return None, issues
        metrics = compute_rotor_metrics(mesh.vertices, axis)
        diameter = hawt_rotor(mesh, axis).diameter if axis is not None else None
        return RotorInfo(metrics, diameter, issues), issues

    def draft_from_preset(self, profile: MachineProfile,
                          inputs: Mapping[str, Any]) -> DraftResult:
        """A draft from the profile's preset; nothing is saved.

        VAWT: project_name, source (StlSource data), axis, flow_axis.
        HAWT: project_name, source, axis.
        FRANCIS: project_name, axis, origin {x, y, z}, diameter (runner outlet D,
        confirmed), slots {slot key: StlSource data}."""
        try:
            if profile.preset is PresetKind.HAWT:
                return self._hawt(inputs)
            if profile.preset is PresetKind.VAWT:
                return self._vawt(inputs)
            if profile.preset is PresetKind.FRANCIS:
                return self._francis(profile, inputs)
        except ValidationError as exc:
            return DraftResult(None, tuple(issues_from_validation_error(exc)))
        except (ValueError, KeyError) as exc:
            return DraftResult(None, (_issue(
                IssueSeverity.ERROR, "PRESET_NOT_APPLICABLE", str(exc),
                "Check the inputs of the preset; see the message."),))
        return DraftResult(None, (_issue(
            IssueSeverity.INFO, "NO_PRESET", f"The {profile.label} profile has no preset.",
            "Fill in the sections one by one."),))

    def _rotor(self, inputs: Mapping[str, Any]) -> tuple[Any, Axis, StlSource]:
        stl = StlSource.model_validate(inputs["source"])
        axis = Axis(str(inputs["axis"]))
        mesh, issues = self.geometry.get(_rotor_geometry(stl))
        if mesh is None:
            raise ValueError("; ".join(i.message for i in issues) or "The rotor STL "
                             "cannot be read.")
        return mesh, axis, stl

    def _hawt(self, inputs: Mapping[str, Any]) -> DraftResult:
        mesh, axis, stl = self._rotor(inputs)
        config = hawt_draft({"project_name": inputs["project_name"],
                             "source": stl.model_dump(mode="json")}, hawt_rotor(mesh, axis))
        return DraftResult(config.model_dump(mode="json"), notes=(PRESET_NOTE,))

    def _vawt(self, inputs: Mapping[str, Any]) -> DraftResult:
        mesh, axis, stl = self._rotor(inputs)
        flow = Axis(str(inputs["flow_axis"]))
        metrics = compute_rotor_metrics(mesh.vertices, axis)
        geometry = _rotor_geometry(stl).model_dump(mode="json")
        config = vawt_draft({"project_name": inputs["project_name"], "geometry": geometry},
                            metrics, flow, VawtPresetKind.SIMPLE)
        notes = [PRESET_NOTE]
        data = config.model_dump(mode="json")
        if data.get("wake") is not None:
            data["wake"] = None
            notes.append(WAKE_DROPPED)
        if data["export"].get("fluent_msh"):
            data["export"]["fluent_msh"] = False
            notes.append(FLUENT_DROPPED)
        return DraftResult(data, notes=tuple(notes))

    def _francis(self, profile: MachineProfile, inputs: Mapping[str, Any]) -> DraftResult:
        slots = dict(inputs.get("slots") or {})
        missing = [s.label for s in profile.slots if s.required and not slots.get(s.key)]
        if missing:
            return DraftResult(None, (_issue(
                IssueSeverity.BLOCKING, "FRANCIS_SLOT_MISSING",
                f"These parts have no file yet: {', '.join(missing)}.",
                "Upload one STL for each required part.", slots=missing),))
        parts = [(s.key, slots[s.key]) for s in profile.slots
                 if s.role == "part" and slots.get(s.key)]
        runner = next(s for s in profile.slots if s.role == "runner")
        base = slots_base(str(inputs["project_name"]), str(inputs["axis"]),
                          dict(inputs["origin"]), parts, (runner.key, slots[runner.key]))
        drafted = francis_draft(base, float(inputs["diameter"]))
        return DraftResult(
            drafted.data, notes=(PRESET_NOTE,),
            joints=tuple((j.first, j.second, j.measure.as_details()) for j in drafted.joints),
            outlet_diameters=dict(drafted.outlet_diameters),
            suggested_types={k: v.value for k, v in drafted.suggested_types.items()})

    def propose_joints(self, raw: Mapping[str, Any]) -> tuple[tuple[str, str, dict[str, float]],
                                                             ...]:
        """Coincident regions of different imported surfaces, proposed as joints
        for the user to confirm (machines.joints), with their measurements."""
        config, _ = parse_config(dict(raw))
        if config is None:
            return ()
        checked = self.checks.get(config, self.thresholds)
        cells = {}
        for surface in checked.surfaces:
            section, index = surface.owner.rsplit(".", 1)
            if section == "domain.parts" and isinstance(config.domain, ImportedDomain):
                cells[surface.owner] = config.domain.parts[int(index)].cell_size
            else:
                cells[surface.owner] = config.rotating_zones[int(index)].cell_size
        return tuple((j.first, j.second, j.measure.as_details())
                     for j in propose_joints(list(checked.surfaces), cells))

    def surface_regions(self, raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
        """Region names of each imported surface of a draft, by owner."""
        config, _ = parse_config(dict(raw))
        if config is None:
            return {}
        checked = self.checks.get(config, self.thresholds)
        return {s.owner: tuple(r.name for r in s.regions) for s in checked.surfaces}

    # --- plan and preflight -------------------------------------------------------------

    def plan(self, raw: Mapping[str, Any] | None = None) -> MachinePlanView:
        """The operations a run would execute, from the configuration; the run
        itself reuses whatever still verifies."""
        config, issues = (parse_config(dict(raw)) if raw is not None
                          else (self.saved_config(), ()))
        if config is None:
            return MachinePlanView((), False, False, issues or (_issue(
                IssueSeverity.INFO, "NO_SAVED_CONFIGURATION",
                "There is no saved configuration to plan.", "Save the configuration."),))
        checked = self.checks.get(config, self.thresholds)
        if config.imported_mesh is not None:
            return MachinePlanView(import_operations(config.physics is not None),
                                   read_last_meshed(self.root) is None,
                                   config.physics is not None, issues)
        try:
            cases = self.pipeline.generator.render(config, checked.surfaces, checked.meshes,
                                                   typed=False)
        except ValueError as exc:
            return MachinePlanView((), False, False, (_issue(
                IssueSeverity.BLOCKING, "CASE_GENERATION_UNSUPPORTED", str(exc),
                "Change the configuration; see the message."),))
        has_physics = config.physics is not None
        return MachinePlanView(operations_for(cases, case_setup=has_physics),
                               read_last_meshed(self.root) is None, has_physics, issues)

    def preflight(self, raw: Mapping[str, Any] | None = None) -> MachinePreflightView:
        """The resource estimate a run's preflight makes (arithmetic, heuristic)."""
        config, issues = (parse_config(dict(raw)) if raw is not None
                          else (self.saved_config(), ()))
        if config is None:
            return MachinePreflightView(None, None, issues)
        if config.imported_mesh is not None:
            cells, basis = preflight_cells(read_mesh_file(config.imported_mesh.source_path)[0])
            if cells is None:
                return MachinePreflightView(None, None, (*issues, _issue(
                    IssueSeverity.INFO, "RESOURCE_NOT_ASSESSED", f"Not assessed: {basis}.",
                    "Watch the conversion.", category=IssueCategory.RESOURCE_RISK,
                    stage=IssueStage.RESOURCE_PREFLIGHT)))
            assessment = self.estimator.assess_cells(
                CellEstimate(background=cells, surface=0, feature=0, layers=0),
                self.system_probe(self.root), config.max_global_cells, peak_cells=cells)
            return MachinePreflightView(MachineCellEstimate(background={"imported": cells}),
                                        assessment, (*issues, *assessment.issues))
        checked = self.checks.get(config, self.thresholds)
        estimate, assessment = assess(config, checked.meshes, checked.surfaces,
                                      self.system_probe(self.root), self.estimator)
        return MachinePreflightView(estimate, assessment, (*issues, *assessment.issues))

    # --- runs ---------------------------------------------------------------------------

    def start_run(self, *, allow_high_resource_risk: bool = False,
                  force: bool = False) -> StartResult:
        """A background run of the saved configuration (mesh, then case setup when
        the project has physics); returns at once. If a run is active, its ID
        comes back instead (a refreshed page or second tab re-attaches)."""
        location = self.location_issues()
        loaded = self.load()
        config = parse_config(loaded.raw)[0] if loaded.raw is not None else None
        if config is None:
            return StartResult(False, None, (*loaded.issues, _issue(
                IssueSeverity.BLOCKING, "NO_SAVED_CONFIGURATION",
                "There is no valid saved configuration to run.",
                "Save the configuration first; runs use the saved configuration only.")))
        status = self.run_status()
        if status.state is RunView.RUNNING:
            return StartResult(False, status.run_id, (_issue(
                IssueSeverity.INFO, "RUN_ALREADY_ACTIVE",
                "A run of this project is already active; showing it.",
                "Follow its progress, or cancel it before starting another.",
                category=IssueCategory.EXECUTION, stage=IssueStage.OPENFOAM_EXECUTION),))
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
                "Follow its progress, or cancel it before starting another.",
                category=IssueCategory.EXECUTION, stage=IssueStage.OPENFOAM_EXECUTION),))
        return StartResult(True, run_id, location)

    # --- outputs ------------------------------------------------------------------------

    def reports(self) -> dict[str, dict[str, Any] | None]:
        """The preflight, mesh and case-setup reports of the last run (each may
        be absent). Mesh and case setup are separate results."""
        result: dict[str, dict[str, Any] | None] = {}
        for name, relative in (("preflight", PREFLIGHT_REPORT), ("mesh", MESH_REPORT),
                               ("case_setup", CASE_SETUP_REPORT)):
            try:
                data = json.loads((self.root / relative).read_text(encoding="utf-8"))
                result[name] = data if isinstance(data, dict) else None
            except (OSError, ValueError):
                result[name] = None
        return result

    def runs(self) -> list[dict[str, Any]]:
        return [r for r in list_run_records(self.root) if r.get("kind") == "machine_mesh"]

    def last_stages(self) -> dict[str, dict[str, Any] | None]:
        """The mesh and case-setup stage results of the most recent run record:
        {status, reason, message} each, or None."""
        runs = self.runs()
        if not runs:
            return {"mesh": None, "case_setup": None}
        latest = max(runs, key=lambda r: str(r.get("finished_at") or r.get("started_at") or ""))
        return {"mesh": latest.get("mesh"), "case_setup": latest.get("case_setup")}

    def generated_files(self, max_bytes: int = 256 * 1024) -> dict[str, str]:
        """The meshing dictionaries of every sub-case, {relative path: text}."""
        return self._texts(sorted((self.root / "cases").glob("*/system/*")), max_bytes)

    def case_files(self, max_bytes: int = 256 * 1024) -> dict[str, str]:
        """The set-up case's files (0/, constant/ without the mesh, system/)."""
        case = self.root / CASE_DIR
        paths = [p for pattern in ("0/*", "constant/*", "system/*")
                 for p in sorted(case.glob(pattern))]
        return self._texts(paths, max_bytes)

    def _texts(self, paths: Sequence[Path], max_bytes: int) -> dict[str, str]:
        files = {}
        for path in paths:
            if path.is_file():
                with path.open("rb") as stream:
                    files[path.relative_to(self.root).as_posix()] = stream.read(
                        max_bytes).decode("utf-8", errors="replace")
        return files

    def check_mesh_folder(self, folder: Path,
                          limits: MeshQualityLimits | None = None) -> MeshFolderReport:
        """The mesh-only checks on any mesh folder (one this app did not build,
        too); the saved acceptance limits unless given. Nothing is run."""
        if limits is None:
            saved = self.saved_config()
            limits = saved.quality if saved is not None else MeshQualityLimits()
        return check_mesh_folder(Path(folder), limits=limits)

    # --- wall cells and operating point (estimates) ------------------------------------------

    def speed_suggestions(self, raw: Mapping[str, Any]) -> tuple[SpeedSuggestion, ...]:
        """Reference speeds for the y+ estimate, from the draft's physics; each
        labelled with what it is. Never applied by themselves.

        Inflow: a velocity inlet's speed, or a flow-rate inlet's Q / inlet area.
        Rotating walls: for a tip-speed ratio, the relative tip speed; for an
        rpm, omega x the zone's radius (a cylinder's, or an imported zone's
        measured from its surface), an upper bound of the tip speed, combined
        with the inflow when there is one."""
        config, _ = parse_config(dict(raw))
        if config is None or config.physics is None:
            return ()
        physics = config.physics
        suggestions = []
        inflow: list[float] = []
        surfaces = None
        for name, inlet in sorted(physics.inlets.items()):
            if isinstance(inlet, VelocityInlet):
                speed = math.hypot(inlet.velocity.x, inlet.velocity.y, inlet.velocity.z)
                suggestions.append(SpeedSuggestion(
                    f"inlet {name}: inlet speed (stationary walls)", speed))
            else:
                if surfaces is None:
                    surfaces = self.checks.get(config, self.thresholds).surfaces
                area = inlet_area(config, surfaces, name, None if config.imported_mesh is None
                                  else self.imported_patch_areas(config))
                if not area:
                    continue
                speed = inlet.volumetric_flow_rate / area
                suggestions.append(SpeedSuggestion(
                    f"inlet {name}: Q / inlet area (stationary walls)", speed))
            inflow.append(speed)
        u = max(inflow) if inflow else None
        for zone in config.rotating_zones:
            rotation = physics.rotation.get(zone.name)
            if rotation is None:
                continue
            given = rotation.speed
            if isinstance(given, TipSpeedRatio):
                free = given.free_stream_speed or u
                if free is None:
                    continue
                tip = given.tip_speed_ratio * free
                suggestions.append(SpeedSuggestion(
                    f"{zone.name}: relative tip speed, sqrt((lambda U)^2 + U^2)",
                    math.hypot(tip, free)))
            elif isinstance(given, RpmSpeed):
                radius = (zone.shape.diameter / 2.0 if isinstance(zone.shape, CylinderZone)
                          else self.zone_radius(raw, zone.name))
                if not radius:
                    continue
                tip = abs(rpm_to_rad_s(given.rpm)) * radius
                if u is None:
                    suggestions.append(SpeedSuggestion(
                        f"{zone.name}: omega x zone radius, an upper bound of the tip speed",
                        tip))
                else:
                    suggestions.append(SpeedSuggestion(
                        f"{zone.name}: relative speed at the zone radius, "
                        "sqrt((omega R)^2 + U^2), an upper bound", math.hypot(tip, u)))
        return tuple(s for s in suggestions if s.speed > 0)

    @staticmethod
    def wall_estimate(raw: Mapping[str, Any], reference: WallReference,
                      first_layer: float | None = None) -> WallEstimates:
        """The y+ of a first-layer thickness, and the thickness for the target
        y+, for the draft's fluid and the reference speed and length (estimates,
        case_setup.wall_cells.SOURCE)."""
        config, _ = parse_config(dict(raw))
        physics = config.physics if config is not None else None
        if physics is None:
            return WallEstimates(None, None, ("Set the fluid (Physics) first: the estimate "
                                              "needs its kinematic viscosity.",))
        if reference.speed_m_s is None or reference.length_m is None:
            return WallEstimates(None, None, ("Give a reference speed and a reference length "
                                              "(a chord, say).",))
        nu = physics.fluid.kinematic_viscosity
        forward = (y_plus_of(first_layer, reference.speed_m_s, reference.length_m, nu)
                   if first_layer else None)
        reverse = (first_layer_for(reference.target_y_plus, reference.speed_m_s,
                                   reference.length_m, nu)
                   if reference.target_y_plus else None)
        return WallEstimates(forward, reverse, ())

    @staticmethod
    def operating_reference(raw: Mapping[str, Any],
                            settings: MachineUiSettings) -> dict[str, float]:
        """Derived reference values of an operating point: omega, and the specific
        speed when a rated net head is given (reference only, never an input)."""
        config, _ = parse_config(dict(raw))
        if config is None or config.physics is None:
            return {}
        physics = config.physics
        values: dict[str, float] = {}
        rpm = next((r.speed.rpm for r in physics.rotation.values()
                    if isinstance(r.speed, RpmSpeed)), None)
        if rpm:
            values["omega_rad_s"] = rpm_to_rad_s(rpm)
        discharge = sum(i.volumetric_flow_rate for i in physics.inlets.values()
                        if isinstance(i, FlowRateInlet))
        head = settings.rated_net_head_m
        if rpm and discharge > 0 and head:
            values["specific_speed_nq"] = specific_speed(rpm, discharge, head)
        return values

    # --- 3D preview (V6) ---------------------------------------------------------------

    def _preview_source(self, view: PreviewView, raw: Mapping[str, Any] | None,
                        ) -> tuple[MachineProjectConfig | None, Path | None,
                                   tuple[Issue, ...], tuple[str, ...]]:
        saved = self._saved_raw()
        if view is PreviewView.SETUP:
            source = raw if raw is not None else saved
            config, display_notes = displayable(source) if source is not None else (None, ())
            if config is None:
                return None, None, (_preview_issue(
                    "VIEW_SETUP_INCOMPLETE",
                    "The setup is shown once the configuration can be read.",
                    "Complete the sections marked as incomplete."),), ()
            return config, None, (), display_notes
        last = read_last_meshed(self.root)
        meshed = parse_config(last.raw)[0] if last is not None else None
        if meshed is None:
            return None, None, (_preview_issue(
                "VIEW_NO_MESH", "No mesh has been built for this project yet.",
                "Run the mesh; it is shown here when it finishes."),), ()
        poly = self.root / "cases" / "merged" / "constant" / "polyMesh"
        current = parse_config(saved)[0] if saved is not None else None
        notes: tuple[str, ...] = ()
        if current is not None and current != meshed:
            notes = ("This mesh was built from an earlier configuration than the one "
                     "saved; run the mesh to update it.",)
        return meshed, poly, (), notes

    def preview_key(self, view: PreviewView, raw: Mapping[str, Any] | None = None,
                    ) -> PreviewKey:
        """A cheap identity of what the 3D pane would show (configuration and the
        files' stat, no content). The physics is part of it: the arrows follow."""
        config, poly, issues, notes = self._preview_source(view, raw)
        if config is None:
            return PreviewKey(view, None, issues, notes)
        signature = [setup_signature(config, self.preview_limits)]
        if poly is not None:
            signature.append(mesh_signature(poly, patch_kinds(config), (), self.preview_limits))
        return PreviewKey(view, sha256_json([signature, config.model_dump(mode="json"),
                                             list(notes)]), issues, notes)

    def preview(self, view: PreviewView, raw: Mapping[str, Any] | None = None) -> PreviewData:
        """The display data of the setup or the last mesh, with the rotation and
        flow arrows from the configuration's physics."""
        config, poly, issues, notes = self._preview_source(view, raw)
        if config is None:
            return PreviewData(None, (), notes, issues, built=False)
        if poly is not None and not (poly / "points").is_file():
            return PreviewData(None, (), notes, (*issues, _preview_issue(
                "VIEW_NO_MESH", "The mesh files of the last run are missing.",
                "Run the mesh again.", IssueSeverity.WARNING)), built=False)
        setup = setup_preview(self.root, config, self.preview_limits)
        stored = setup
        if poly is not None:
            stored = mesh_preview(self.root, poly, patch_kinds(config),
                                  cut_planes(config, setup.scene), self.preview_limits)
        scene = stored.scene
        flows, flow_notes = flow_arrows(config, setup.scene)
        arrows = (*rotation_arrows(config, setup.scene), *flows)
        found = (*scene.issues, *(setup.scene.issues if poly is not None else ()))
        return PreviewData(scene, arrows, (*notes, *scene.notes, *flow_notes),
                           (*issues, *found), built=stored.built)

    def zone_radius(self, raw: Mapping[str, Any], zone: str) -> float | None:
        """A zone's radius: the cylinder's, or an imported zone's from its stored
        setup scene (None until the setup has been shown)."""
        config, _ = parse_config(dict(raw))
        if config is None:
            return None
        if config.imported_mesh is not None:
            return self._imported_zone_radius(config, zone)
        stored = setup_preview(self.root, config, self.preview_limits)
        frame = next((f for f in zone_frames_of(stored.scene, config) if f.name == zone), None)
        return frame.radius if frame is not None and frame.radius > 0 else None


    def _imported_zone_radius(self, config: MachineProjectConfig, zone: str) -> float | None:
        """An imported mesh's zone radius, computed from the zone in the imported
        mesh of this same file (None before it is imported, or for another file)."""
        mesh = config.imported_mesh
        assert mesh is not None
        own = next((z for z in mesh.zones if z.name == zone), None)
        try:
            report = json.loads((self.root / IMPORT_REPORT).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if own is None or not isinstance(report, dict) or report.get("sha256") != mesh.sha256:
            return None
        return mesh_zone_radius(self.root / "cases" / IMPORTED_CASE, own)


def _patch_areas(folder: Path, names: tuple[str, ...]) -> dict[str, float]:
    """patch_areas, read once per version of the mesh's faces and points."""
    poly = folder / "constant" / "polyMesh"
    try:
        stamp = tuple((s.st_size, s.st_mtime_ns) for s in (
            (poly / n).stat() for n in ("boundary", "faces", "points")))
    except OSError:
        return {}
    return dict(_areas_of(str(folder), names, stamp))


@functools.lru_cache(maxsize=16)
def _areas_of(folder: str, names: tuple[str, ...],
              stamp: tuple[tuple[int, int], ...]) -> tuple[tuple[str, float], ...]:
    return tuple(patch_areas(Path(folder), names).items())


_CHECKS = _CheckCache()


def machines_projects_root(env: Mapping[str, str] | None = None) -> Path:
    """Where machine projects live: $MACHINES_PROJECTS_DIR, else ~/machine_projects."""
    env = os.environ if env is None else env
    return Path(env.get(PROJECTS_DIR_ENV) or DEFAULT_PROJECTS_DIR).expanduser().resolve()


def list_machine_projects(root: Path) -> list[Path]:
    """Project folders under root with a saved machine configuration, by name."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / CONFIG_PATH).is_file())


def _rotor_geometry(stl: StlSource) -> RotorGeometryConfig:
    return RotorGeometryConfig(
        source_path=stl.source_path, source_units=stl.source_units, scale=stl.scale,
        rotation_deg=stl.rotation_deg, translation=stl.translation, patch_name="rotor")


def _preview_issue(code: str, message: str, action: str,
                   severity: IssueSeverity = IssueSeverity.INFO) -> Issue:
    return Issue(category=IssueCategory.INPUT, severity=severity,
                 stage=IssueStage.VISUALIZATION, code=code, message=message,
                 suggested_action=action)


PHYSICS_LEFT_OUT = ("The physics settings are incomplete, so the arrows leave them out "
                    "(the rotation shows as not set).")
UNTYPED_LEFT_OUT = ("Patches without a type are drawn as regions without a patch until "
                    "their type is chosen.")


def without_physics(raw: Any, issues: Sequence[Issue]) -> MachineProjectConfig | None:
    """The configuration without its physics, when every parse problem is in the
    physics section (being filled in); else None."""
    if not isinstance(raw, Mapping) or "physics" not in raw or not issues:
        return None
    if not all(str(i.details.get("field", "")).startswith("physics") for i in issues):
        return None
    return parse_config({k: v for k, v in raw.items() if k != "physics"})[0]


def displayable(raw: Mapping[str, Any]) -> tuple[MachineProjectConfig | None, tuple[str, ...]]:
    """A configuration the 3D pane can draw, and what it left out: the draft; else
    without its incomplete physics; else also without the patches that have no
    type yet (a drafted Francis passage before its types are chosen). Display
    only: validation never uses this."""
    config, issues = parse_config(dict(raw))
    if config is not None:
        return config, ()
    rest = without_physics(raw, issues)
    if rest is not None:
        return rest, (PHYSICS_LEFT_OUT,)
    data = {k: v for k, v in dict(raw).items() if k != "physics"}
    patches = [p for p in data.get("patches") or [] if isinstance(p, Mapping)]
    typed = [p for p in patches if p.get("type")]
    if len(typed) == len(patches):
        return None, ()
    config, _ = parse_config({**data, "patches": typed})
    notes = (UNTYPED_LEFT_OUT,) + ((PHYSICS_LEFT_OUT,) if "physics" in raw else ())
    return (config, notes) if config is not None else (None, ())


def _changed_paths(old: Any, new: Any, prefix: str = "") -> list[str]:
    """Dotted paths whose value differs between two JSON-like values."""
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        paths = []
        for key in sorted({*old, *new}):
            sub = f"{prefix}.{key}" if prefix else str(key)
            paths += _changed_paths(old.get(key), new.get(key), sub)
        return paths
    if (isinstance(old, list) and isinstance(new, list) and len(old) == len(new)):
        paths = []
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            paths += _changed_paths(a, b, f"{prefix}.{i}" if prefix else str(i))
        return paths
    return [] if old == new else [prefix]


def body_motion_label(motion: Motion) -> str:
    return {Motion.STATIONARY: "stationary", Motion.ROTATING: "rotating",
            Motion.SPLIT: "split (the part inside its zone rotates)"}[motion]


PATCH_TYPE_LABELS: dict[str, str] = {
    PatchType.INLET.value: "Inlet", PatchType.OUTLET.value: "Outlet",
    PatchType.WALL.value: "Wall", PatchType.ROTATING_WALL.value: "Rotating wall",
    PatchType.SLIP.value: "Symmetry or slip"}
