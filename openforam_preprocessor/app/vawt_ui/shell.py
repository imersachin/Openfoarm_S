"""Layout of the VAWT UI (spec section 13.1): header, section navigation with
status marks, action bar, section content, 3D preview pane, status strip.

Only the active section's render() runs (spec 14.1 #1). Status marks are the
service's states, drawn as symbols here. The status strip, the preview pane
and live run views are fragments; they refresh on a timer only while a run is
active (spec 14.1 #3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.vawt_ui import state
from app.vawt_ui.sections import (
    domain,
    export,
    geometry,
    history,
    layers,
    logs,
    mesh,
    project,
    refinement,
    review,
    rotating_zone,
    run,
)
from app.vawt_ui.sections.common import live
from vawt.service import RunStatusView, RunView, SectionState, VawtService
from vawt.workspace import create_project, list_projects, projects_root

# (group, key, label, module); the module's render(ctx) draws the section.
SECTIONS: tuple[tuple[str, str, str, ModuleType], ...] = (
    ("Setup", "project", "Project", project),
    ("Setup", "geometry", "Geometry", geometry),
    ("Setup", "rotating_zone", "Rotating zone", rotating_zone),
    ("Setup", "domain", "Domain", domain),
    ("Setup", "refinement", "Refinement", refinement),
    ("Setup", "layers", "Boundary layers", layers),
    ("Run", "review", "Review", review),
    ("Run", "run", "Run", run),
    ("Results", "mesh", "Mesh", mesh),
    ("Results", "export", "Export", export),
    ("Tools", "history", "History", history),
    ("Tools", "logs", "Logs", logs),
)
SETUP = frozenset(key for group, key, _, _ in SECTIONS if group == "Setup")

SECTION_MARKS = {
    SectionState.EMPTY: "○", SectionState.INCOMPLETE: "◐", SectionState.READY: "●",
    SectionState.STALE: "↻", SectionState.ERROR: "✕",
}
RUN_MARKS = {
    RunView.RUNNING: "▶", RunView.RUNNING_ELSEWHERE: "▶", RunView.INTERRUPTED: "!",
    RunView.LOCK_UNREADABLE: "!", RunView.SUCCEEDED: "●", RunView.FAILED: "✕",
    RunView.CANCELLED: "■", RunView.NONE: "○",
}
VALIDITY_MARKS = {"VALID": "●", "INVALID": "✕"}


def elapsed(started_at: str | None) -> str:
    if not started_at:
        return "—"
    try:
        seconds = int((datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds())
    except ValueError:
        return "—"
    return f"{seconds // 60}:{seconds % 60:02d}"


# --- pieces ------------------------------------------------------------------------------

def project_picker() -> None:
    st.subheader("Project")
    root = projects_root()
    known = list_projects(root)
    if known:
        choice = st.selectbox("Open a project", [p.name for p in known], index=None,
                              placeholder=f"in {root}", key="vawt_pick")
        if choice and st.button("Open", key="vawt_open_known"):
            state.open_project(root / choice)
            st.rerun()
    with st.expander("New project"):
        name = st.text_input("Name", key="vawt_new_name")
        if st.button("Create", key="vawt_create", disabled=not name):
            try:
                state.open_project(create_project(name, root))
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()
    with st.expander("Open a folder"):
        folder = st.text_input("Project folder (inside WSL)", key="vawt_folder")
        if st.button("Open folder", key="vawt_open_folder", disabled=not folder):
            state.open_project(Path(folder))
            st.rerun()


def header(service: VawtService, run_view: RunStatusView) -> None:
    draft = state.draft()
    environment = service.environment()
    name, saved, openfoam, running = st.columns([3, 2, 2, 2])
    name.markdown(f"### {draft.get('project_name') or 'Unnamed project'}")
    name.caption(f"`{service.root}`")
    if state.unsaved():
        saved.warning("Unsaved changes")
    else:
        saved.success(f"Saved · revision {state.revision()}" if state.revision()
                      else "Nothing saved yet")
    version = environment.openfoam_version
    if version is None:
        openfoam.error("OpenFOAM not sourced")
    elif any(i.code == "OPENFOAM_VERSION_UNVERIFIED" for i in environment.issues):
        openfoam.warning(f"OpenFOAM {version} (unverified)")
    else:
        openfoam.success(f"OpenFOAM {version}")
    running.info(f"Run: {RUN_MARKS[run_view.state]} {run_view.state.value}")
    location = [i for i in (*environment.issues, *service.location_issues())
                if i.code == "PROJECT_ON_WINDOWS_DRIVE"]
    if location:
        render_issues(location[:1])


def navigation(sections: dict[str, Any], run_view: RunStatusView,
               service: VawtService) -> None:
    mesh_report = None
    group = None
    for group_name, key, label, _ in SECTIONS:
        if group_name != group:
            st.caption(group_name)
            group = group_name
        if key in sections:
            mark = SECTION_MARKS[sections[key].state]
        elif key == "run":
            mark = RUN_MARKS[run_view.state]
        elif key == "mesh":
            if mesh_report is None:
                mesh_report = service.reports()["mesh"] or {}
            validity = (mesh_report.get("assessment") or {}).get("mesh_validity")
            mark = VALIDITY_MARKS.get(str(validity), "○")
        else:
            mark = " "
        st.button(f"{mark}  {label}", key=f"nav-{key}", use_container_width=True,
                  type="primary" if key == state.section() else "secondary",
                  on_click=state.set_section, args=(key,))


def action_bar(service: VawtService) -> None:
    left, revert, save = st.columns([4, 1, 1])
    left.caption("Edit a section and press Apply; Save writes a new revision. "
                 "Runs use the saved configuration.")
    if revert.button("Revert", key="vawt_revert", disabled=not state.unsaved(),
                     use_container_width=True):
        state.revert()
        st.rerun()
    if save.button("Save", key="vawt_save", type="primary", disabled=not state.unsaved(),
                   use_container_width=True):
        result = service.save(state.draft())
        if result.revision is not None:
            state.mark_saved(result.revision)
        else:
            st.session_state["vawt_save_failed"] = True
        state.add_messages(result.issues)
        st.rerun()


@st.fragment
def preview_pane() -> None:
    with st.container(border=True):
        st.markdown("**3D preview**")
        st.caption("Rotor, rotating zone, domain and the finished mesh are shown here "
                   "from V6 on (display artifacts, patch view, slices).")


def status_strip(service: VawtService, initial: RunStatusView) -> None:
    @live(initial.state in state.ACTIVE_STATES)
    def strip() -> None:
        view = service.run_status()
        with st.container(border=True):
            stage, step, clock, issues, action = st.columns([3, 1, 1, 1, 1])
            stage.markdown(f"**{RUN_MARKS[view.state]} {view.state.value}**"
                           + (f" · {view.stage}" if view.stage else ""))
            step.caption(f"step {view.step} of {view.total_steps}" if view.total_steps
                         else "step —")
            clock.caption(f"elapsed {elapsed(view.started_at)}"
                          if view.state in state.ACTIVE_STATES else "")
            issues.caption(f"{len(view.issues)} issue(s)")
            if view.can_cancel and action.button("Cancel", key="vawt_strip_cancel"):
                service.cancel_run()
    strip()


# --- the page --------------------------------------------------------------------

def main() -> None:
    with st.sidebar:
        project_picker()
    if not state.has_project():
        st.title("VAWT mesh generator")
        st.info("Open or create a project in the sidebar. Projects live in "
                f"`{projects_root()}`.")
        return

    service = state.service()
    analysis = state.analysis()
    run_view = service.run_status()
    header(service, run_view)
    with st.sidebar:
        st.divider()
        navigation(analysis.sections, run_view, service)

    messages = state.take_messages()
    if messages:
        render_issues(messages)
    if st.session_state.pop("vawt_save_failed", False):
        st.error("The configuration was not saved: it is incomplete or malformed. "
                 "See the marked sections.")

    content, preview = st.columns([3, 2], gap="medium")
    active = state.section()
    with content:
        if active in SETUP:
            action_bar(service)
        module = next(m for _, key, _, m in SECTIONS if key == active)
        module.render(state.Context(service, state.draft(), analysis, run_view))
    with preview:
        preview_pane()
    status_strip(service, run_view)
