"""Per-session state of the machines UI, and access to MachineService.

Presentation only: every engineering answer comes from the service. This
module keeps the draft and the project's machine profile, remembers the last
analysis of the draft, and decides nothing else.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from app.components.widgets import GENERATION
from machines.profiles import MachineProfile, ProfileRegistry, default_registry
from machines.project_store import LAST_MESHED_PATH, MachineUiSettings
from machines.service import MachineAnalysis, MachineService
from vawt.service import RunStatusView, RunView

# The service for a project folder. Tests replace it to inject a fake OpenFOAM.
SERVICE_FACTORY: Callable[[Path], MachineService] = MachineService
# The machine profiles offered (machines/profile_data/). Tests replace it to load
# profiles from their own data files; the service of a project uses the same.
REGISTRY_FACTORY: Callable[[], ProfileRegistry] = default_registry


def registry() -> ProfileRegistry:
    return REGISTRY_FACTORY()

ROOT = "machines_project_root"
DRAFT = "machines_draft"
SAVED = "machines_saved_raw"
REVISION = "machines_revision"
SECTION = "machines_section"
ANALYSIS = "machines_analysis"  # (key, MachineAnalysis) of the current draft
MESSAGES = "machines_messages"  # issues to show once after an action
NOTES = "machines_notes"  # notes to show once (preset notes)
ACTIVE_STATES = (RunView.RUNNING, RunView.RUNNING_ELSEWHERE)


@st.cache_resource(show_spinner=False)
def _service(root: str, factory_id: int) -> MachineService:
    return SERVICE_FACTORY(Path(root))


def service() -> MachineService:
    """One service per project folder for the whole app."""
    return _service(st.session_state[ROOT], id(SERVICE_FACTORY))


def has_project() -> bool:
    return bool(st.session_state.get(ROOT))


def bump_generation() -> None:
    """Recreate every form widget from the draft (after Revert, a preset, a switch)."""
    st.session_state[GENERATION] = st.session_state.get(GENERATION, 0) + 1


STASHES = ("machines_rotor_inputs", "machines_slot_inputs", "machines_francis_extras",
           "machines_patch_suggestions")


def _clear_stashes() -> None:
    """Inputs kept for a project's presets; never carried to another project."""
    for key in STASHES:
        st.session_state.pop(key, None)
    for key in [k for k in st.session_state if str(k).startswith("machines_proposed_")]:
        st.session_state.pop(key, None)


def open_project(root: Path, profile_id: str | None = None) -> None:
    """Open a project folder; with profile_id, a new project of that machine."""
    _clear_stashes()
    st.session_state[ROOT] = str(root.expanduser().resolve())
    svc = service()
    if profile_id is not None:
        svc.choose_profile(profile_id)
    loaded = svc.load()
    profile = svc.profile()
    st.session_state[SAVED] = copy.deepcopy(loaded.raw)
    if loaded.raw is not None:
        draft = copy.deepcopy(loaded.raw)
    elif profile is not None:
        draft = MachineService.new_draft(profile, root.name)
    else:
        draft = {"project_name": root.name}
    st.session_state[DRAFT] = draft
    st.session_state[REVISION] = loaded.revision
    st.session_state[SECTION] = "project"
    st.session_state.pop(ANALYSIS, None)
    st.session_state[MESSAGES] = list(loaded.issues)
    bump_generation()


def close_project() -> None:
    _clear_stashes()
    for key in (ROOT, DRAFT, SAVED, REVISION, SECTION, ANALYSIS):
        st.session_state.pop(key, None)


def profile() -> MachineProfile | None:
    return service().profile()


def choose_profile(profile_id: str) -> None:
    """Set the machine profile of the open project (new projects only)."""
    chosen = service().choose_profile(profile_id)
    draft()["machine"] = chosen.machine.value
    st.session_state[SECTION] = "project"
    st.session_state.pop(ANALYSIS, None)
    bump_generation()


def draft() -> dict[str, Any]:
    value: dict[str, Any] = st.session_state[DRAFT]
    return value


def replace_draft(new: dict[str, Any]) -> None:
    st.session_state[DRAFT] = new
    bump_generation()


def saved_raw() -> dict[str, Any] | None:
    value: dict[str, Any] | None = st.session_state.get(SAVED)
    return value


def revision() -> int | None:
    value: int | None = st.session_state.get(REVISION)
    return value


def unsaved() -> bool:
    return draft() != saved_raw()


def revert() -> None:
    saved = saved_raw()
    current = profile()
    if saved is not None:
        replace_draft(copy.deepcopy(saved))
    elif current is not None:
        replace_draft(MachineService.new_draft(current, Path(st.session_state[ROOT]).name))


def mark_saved(revision_number: int) -> None:
    st.session_state[SAVED] = copy.deepcopy(draft())
    st.session_state[REVISION] = revision_number


def section() -> str:
    value: str = st.session_state.get(SECTION, "project")
    return value


def set_section(name: str) -> None:
    st.session_state[SECTION] = name


def analysis(current: MachineProfile) -> MachineAnalysis:
    """The service's analysis of the draft, recomputed only when the draft, the
    saved revision, the last mesh or the profile changed (so switching section
    reads no geometry or mesh file)."""
    root = Path(st.session_state[ROOT])
    try:
        meshed = (root / LAST_MESHED_PATH).stat().st_mtime_ns
    except OSError:
        meshed = 0
    key = (json.dumps(draft(), sort_keys=True, default=str), revision(), meshed, current.id)
    cached = st.session_state.get(ANALYSIS)
    if cached is not None and cached[0] == key:
        result: MachineAnalysis = cached[1]
        return result
    result = service().analyse(draft(), current)
    st.session_state[ANALYSIS] = (key, result)
    return result


def add_messages(issues: Any) -> None:
    st.session_state.setdefault(MESSAGES, []).extend(issues)


def take_messages() -> list[Any]:
    messages: list[Any] = st.session_state.pop(MESSAGES, [])
    return messages


def add_notes(notes: Any) -> None:
    st.session_state.setdefault(NOTES, []).extend(notes)


def take_notes() -> list[str]:
    notes: list[str] = st.session_state.pop(NOTES, [])
    return notes


@dataclass(frozen=True)
class Context:
    """What a section needs to render."""

    service: MachineService
    profile: MachineProfile
    draft: dict[str, Any]
    analysis: MachineAnalysis
    run: RunStatusView
    settings: MachineUiSettings

    @property
    def active(self) -> bool:
        return self.run.state in ACTIVE_STATES

    def term(self, key: str, default: str) -> str:
        return self.profile.term(key, default)
