"""Per-session state of the VAWT UI, and access to VawtService.

Presentation only: every engineering answer (validation, section status,
plan, preflight, runs) comes from the service. This module keeps the draft,
remembers the last analysis of it, and decides nothing else.
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
from vawt.config import (
    LayersConfig,
    RefinementConfig,
    StrictMeshQualityLimits,
    StrictSnappyQualityControls,
    VawtProjectConfig,
)
from vawt.project_store import LAST_MESHED_PATH
from vawt.service import Analysis, RunStatusView, RunView, VawtService

# The service for a project folder. Tests replace it to inject a fake OpenFOAM.
SERVICE_FACTORY: Callable[[Path], VawtService] = VawtService

ROOT = "vawt_project_root"
DRAFT = "vawt_draft"
SAVED = "vawt_saved_raw"
REVISION = "vawt_revision"
SECTION = "vawt_section"
ANALYSIS = "vawt_analysis"  # (key, Analysis) of the current draft
MESSAGES = "vawt_messages"  # issues to show once after an action
ACTIVE_STATES = (RunView.RUNNING, RunView.RUNNING_ELSEWHERE)


@st.cache_resource(show_spinner=False)
def _service(root: str, factory_id: int) -> VawtService:
    return SERVICE_FACTORY(Path(root))


def service() -> VawtService:
    """One service per project folder for the whole app (spec 14.1 #4)."""
    return _service(st.session_state[ROOT], id(SERVICE_FACTORY))


def has_project() -> bool:
    return bool(st.session_state.get(ROOT))


def bump_generation() -> None:
    """Recreate every form widget from the draft (after Revert, preset, switch)."""
    st.session_state[GENERATION] = st.session_state.get(GENERATION, 0) + 1


def new_draft(name: str) -> dict[str, Any]:
    """A draft for a project with no saved configuration. The geometry defaults
    are filled in so that showing a field never changes the draft."""
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    return {"project_name": name,
            "geometry": {"scale": 1.0, "rotation_deg": dict(zero),
                         "translation": dict(zero), "patch_name": "rotor"},
            "refinement": RefinementConfig().model_dump(mode="json"),
            "layers": LayersConfig().model_dump(mode="json"),
            "snappy_quality": StrictSnappyQualityControls().model_dump(mode="json"),
            "quality": StrictMeshQualityLimits().model_dump(mode="json"),
            "max_global_cells": VawtProjectConfig.model_fields["max_global_cells"].default}


def open_project(root: Path) -> None:
    st.session_state[ROOT] = str(root.expanduser().resolve())
    loaded = service().load()
    st.session_state[SAVED] = copy.deepcopy(loaded.raw)
    st.session_state[DRAFT] = (copy.deepcopy(loaded.raw) if loaded.raw is not None
                               else new_draft(root.name))
    st.session_state[REVISION] = loaded.revision
    st.session_state[SECTION] = "project"
    st.session_state.pop(ANALYSIS, None)
    st.session_state[MESSAGES] = list(loaded.issues)
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
    replace_draft(copy.deepcopy(saved) if saved is not None
                  else new_draft(Path(st.session_state[ROOT]).name))


def mark_saved(revision_number: int) -> None:
    st.session_state[SAVED] = copy.deepcopy(draft())
    st.session_state[REVISION] = revision_number


def section() -> str:
    value: str = st.session_state.get(SECTION, "project")
    return value


def set_section(name: str) -> None:
    st.session_state[SECTION] = name


def analysis() -> Analysis:
    """The service's analysis of the draft, recomputed only when the draft, the
    saved revision or the last mesh changed (so switching sections reads no
    geometry or mesh file)."""
    root = Path(st.session_state[ROOT])
    try:
        meshed = (root / LAST_MESHED_PATH).stat().st_mtime_ns
    except OSError:
        meshed = 0
    key = (json.dumps(draft(), sort_keys=True, default=str), revision(), meshed)
    cached = st.session_state.get(ANALYSIS)
    if cached is not None and cached[0] == key:
        result: Analysis = cached[1]
        return result
    result = service().analyse(draft())
    st.session_state[ANALYSIS] = (key, result)
    return result


def add_messages(issues: Any) -> None:
    st.session_state.setdefault(MESSAGES, []).extend(issues)


def take_messages() -> list[Any]:
    messages: list[Any] = st.session_state.pop(MESSAGES, [])
    return messages


@dataclass(frozen=True)
class Context:
    """What a section needs to render."""

    service: VawtService
    draft: dict[str, Any]
    analysis: Analysis
    run: RunStatusView

    @property
    def active(self) -> bool:
        return self.run.state in ACTIVE_STATES
