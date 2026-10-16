"""Layout of the machines UI (G7; docs/rotating_machinery.md sections 13 and 27):
a machine choice first, then the machine's own sections (from its profile),
header, action bar, section content, 3D preview pane, status strip.

Only the active section's render() runs. Status marks are the service's
states, drawn as symbols here. The status strip, the preview pane and live
run views are fragments; they refresh on a timer only while a run is active.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.preview import preview_pane
from app.machines_ui.sections import renderer
from app.machines_ui.sections.common import live
from machines.profiles import GROUP_ORDER, MachineProfile, SectionGroup
from machines.service import MachineService, list_machine_projects, machines_projects_root
from vawt.service import RunStatusView, RunView, SectionState
from vawt.workspace import create_project

EDITABLE = (SectionGroup.SETUP, SectionGroup.PHYSICS)
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
STAGE_MARKS = {"SUCCESS": "●", "SKIPPED": "○", "FAILED": "✕", "CANCELLED": "■"}


def elapsed(started_at: str | None) -> str:
    if not started_at:
        return "—"
    try:
        seconds = int((datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds())
    except ValueError:
        return "—"
    return f"{seconds // 60}:{seconds % 60:02d}"


# --- before a project is open ------------------------------------------------------------

def machine_choice(key: str) -> MachineProfile | None:
    """The machine chooser: one option per profile, with its description."""
    options = {p.id: p for p in state.registry().all()}
    choice = st.radio("Machine", list(options), key=key,
                      format_func=lambda i: f"{options[i].label} — {options[i].description}",
                      index=None)
    return options.get(choice) if choice else None


def landing() -> None:
    st.title("Rotating machines: mesh and case setup")
    st.caption("Choose the machine, then name the project. Each machine has its own "
               "sections and terms; Custom keeps the general workflow.")
    root = machines_projects_root()
    chosen = machine_choice("machines_landing_machine")
    name = st.text_input("Project name", key="machines_new_name")
    if st.button("Create project", key="machines_create", type="primary",
                 disabled=not (chosen and name)):
        try:
            path = create_project(name, root)
        except ValueError as exc:
            st.error(str(exc))
        else:
            assert chosen is not None
            state.open_project(path, chosen.id)
            st.rerun()
    st.caption(f"Projects are created in `{root}` (set MACHINES_PROJECTS_DIR to change it). "
               "Open an existing one in the sidebar.")


def project_picker() -> None:
    st.subheader("Project")
    root = machines_projects_root()
    known = list_machine_projects(root)
    if known:
        choice = st.selectbox("Open a project", [p.name for p in known], index=None,
                              placeholder=f"in {root}", key="machines_pick")
        if choice and st.button("Open", key="machines_open_known"):
            state.open_project(root / choice)
            st.rerun()
    with st.expander("Open a folder"):
        folder = st.text_input("Project folder (inside WSL)", key="machines_folder")
        if st.button("Open folder", key="machines_open_folder", disabled=not folder):
            state.open_project(Path(folder))
            st.rerun()
    if state.has_project() and st.button("Close the project", key="machines_close"):
        state.close_project()
        st.rerun()


def choose_machine() -> None:
    """A project folder without a saved configuration or a machine yet. A saved
    project whose machine has no profile (VAWT_POLE until G8) is not retyped."""
    st.title("Choose the machine")
    saved = state.saved_raw()
    if saved is not None:
        st.error(f"This project is saved as a {saved.get('machine')} machine, which this "
                 "app has no profile for yet; it is left as it is.")
        return
    st.caption(f"`{state.service().root}` has no machine yet.")
    chosen = machine_choice("machines_folder_machine")
    if st.button("Use this machine", key="machines_use_machine", type="primary",
                 disabled=chosen is None):
        assert chosen is not None
        state.choose_profile(chosen.id)
        st.rerun()


# --- the page pieces ---------------------------------------------------------------------

def header(service: MachineService, profile: MachineProfile, run_view: RunStatusView) -> None:
    draft = state.draft()
    environment = service.environment()
    name, saved, openfoam, running = st.columns([3, 2, 2, 2])
    name.markdown(f"### {draft.get('project_name') or 'Unnamed project'}")
    name.caption(f"{profile.label} · `{service.root}`")
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


def navigation(profile: MachineProfile, sections: dict[str, Any], run_view: RunStatusView,
               service: MachineService) -> None:
    stages: dict[str, Any] | None = None
    mesh_report: dict[str, Any] | None = None
    for group in GROUP_ORDER:
        specs = [s for s in profile.sections if s.group is group]
        if not specs:
            continue
        st.caption(group.value)
        for spec in specs:
            key = spec.key
            if group in EDITABLE and key in sections:
                mark = SECTION_MARKS[sections[key].state]
            elif key == "run":
                mark = RUN_MARKS[run_view.state]
            elif key == "mesh":
                if mesh_report is None:
                    mesh_report = service.reports()["mesh"] or {}
                validity = (mesh_report.get("assessment") or {}).get("mesh_validity")
                mark = VALIDITY_MARKS.get(str(validity), "○")
            elif key == "case":
                if stages is None:
                    stages = service.last_stages()
                mark = STAGE_MARKS.get(str((stages["case_setup"] or {}).get("status")), "○")
            else:
                mark = " "
            st.button(f"{mark}  {spec.title}", key=f"nav-{key}", width="stretch",
                      type="primary" if key == state.section() else "secondary",
                      on_click=state.set_section, args=(key,))


def action_bar(service: MachineService) -> None:
    left, revert, save = st.columns([4, 1, 1])
    left.caption("Edit a section and press Apply; Save writes a new revision. "
                 "Runs use the saved configuration.")
    if revert.button("Revert", key="machines_revert", disabled=not state.unsaved(),
                     width="stretch"):
        state.revert()
        st.rerun()
    if save.button("Save", key="machines_save", type="primary", disabled=not state.unsaved(),
                   width="stretch"):
        result = service.save(state.draft())
        if result.revision is not None:
            state.mark_saved(result.revision)
        else:
            st.session_state["machines_save_failed"] = True
        state.add_messages(result.issues)
        st.rerun()


def status_strip(service: MachineService, initial: RunStatusView) -> None:
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
            if view.can_cancel and action.button("Cancel", key="machines_strip_cancel"):
                service.cancel_run()
    strip()


# --- the page ------------------------------------------------------------------------------

def main() -> None:
    with st.sidebar:
        project_picker()
    if not state.has_project():
        landing()
        return
    service = state.service()
    profile = service.profile()
    if profile is None:
        choose_machine()
        return
    analysis = state.analysis(profile)
    run_view = service.run_status()
    header(service, profile, run_view)
    with st.sidebar:
        st.divider()
        navigation(profile, analysis.sections, run_view, service)

    messages = state.take_messages()
    if messages:
        render_issues(messages)
    for note in state.take_notes():
        st.info(note)
    if st.session_state.pop("machines_save_failed", False):
        st.error("The configuration was not saved: it is incomplete or malformed. "
                 "See the marked sections.")

    content, preview = st.columns([3, 2], gap="medium")
    active = state.section() if state.section() in profile.section_keys() else "project"
    spec = profile.section(active)
    with content:
        if spec is not None and (spec.group in EDITABLE or active == "mesh"):
            action_bar(service)  # Mesh edits the acceptance limits
        renderer(active)(state.Context(service, profile, state.draft(), analysis, run_view,
                                       service.ui_settings()))
    with preview:
        preview_pane()
    status_strip(service, run_view)
