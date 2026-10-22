"""Shared pieces of the machines section pages (presentation only)."""

from __future__ import annotations

import html
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from vawt.service import SectionState

REFRESH_S = 1.5  # status and log refresh while a run is active (VAWT spec 14.2: 1-2 s)

STATE_TEXT = {
    SectionState.EMPTY: "Not filled in yet.",
    SectionState.INCOMPLETE: "Some required values are missing.",
    SectionState.READY: "Ready.",
    SectionState.STALE: "Changed since the last run.",
    SectionState.ERROR: "Has problems that stop a run.",
}
PHYSICS_EMPTY = "Not set: the project is meshed only; set the physics to also set up the case."
# Next to every Courant number shown (owner, after M2a).
COURANT_NOTE = ("A max Courant number below 50 keeps a run stable, not accurate: rotor-stator "
                "studies usually need a max Courant number around 1-5.")
SHORT_NAME = 16  # longer zone names are shown short, the full name on hover


def short_name(name: str) -> str:
    """A long cell-zone name as its last '_' part (fluid_GV_flux_1_GV1_skin ->
    skin), else cut; short names as they are."""
    if len(name) <= SHORT_NAME:
        return name
    tail = name.rsplit("_", 1)[-1]
    return tail if tail and tail != name else name[:SHORT_NAME - 1] + "\u2026"


def zone_html(name: str) -> str:
    """A zone name for st.markdown(unsafe_allow_html=True): the short label, the
    full name on hover (logs and reports keep the full name)."""
    short = short_name(name)
    if short == name:
        return html.escape(name)
    return f'<abbr title="{html.escape(name, quote=True)}">{html.escape(short)}</abbr>'


def title(ctx: state.Context, key: str) -> str:
    spec = ctx.profile.section(key)
    return spec.title if spec is not None else key


def section_header(ctx: state.Context, key: str) -> None:
    spec = ctx.profile.section(key)
    st.subheader(title(ctx, key))
    if spec is not None and spec.help:
        st.caption(spec.help)
    status = ctx.analysis.sections.get(key)
    if status is None:
        return
    text = STATE_TEXT[status.state]
    if status.state is SectionState.EMPTY and spec is not None and spec.group.value == "Physics":
        text = PHYSICS_EMPTY
    if status.state is SectionState.STALE and status.stale_because:
        shown = status.stale_because[:6]
        text += " Changed settings: " + ", ".join(f"`{p}`" for p in shown)
        if len(status.stale_because) > len(shown):
            text += f" and {len(status.stale_because) - len(shown)} more"
    st.caption(f"Status: **{status.state.value}** · {text}")


def section_issues(ctx: state.Context, key: str) -> None:
    status = ctx.analysis.sections.get(key)
    if status is not None and status.issues:
        render_issues(status.issues)


@contextmanager
def apply_form(key: str, on_apply: Callable[[], None] | None = None) -> Iterator[None]:
    """A form whose fields write to the draft only when Apply is pressed;
    Apply then runs on_apply (an edit that only Apply may make) and reruns the
    page so marks and issues follow."""
    with st.form(f"machines_form_{key}", border=False):
        yield
        applied = st.form_submit_button("Apply", type="primary")
    if applied:
        if on_apply is not None:
            on_apply()
        st.rerun()


def confirm(key: str, prompt: str, action_label: str, on_confirm: Callable[[], object],
            *, button_label: str) -> None:
    """A two-step confirmation: the first button asks, the second acts."""
    flag = f"machines_confirm_{key}"
    if not st.session_state.get(flag):
        if st.button(button_label, key=f"{flag}_ask"):
            st.session_state[flag] = True
            st.rerun()
        return
    st.warning(prompt)
    yes, no = st.columns(2)
    if yes.button(action_label, key=f"{flag}_yes", type="primary"):
        st.session_state[flag] = False
        on_confirm()
        st.rerun()
    if no.button("Keep it", key=f"{flag}_no"):
        st.session_state[flag] = False
        st.rerun()


def live(active: bool) -> Callable[[Callable[[], None]], Callable[[], None]]:
    """A fragment that refreshes every REFRESH_S while a run is active; when the
    run ends it reruns the whole page once and stops refreshing."""
    def decorate(body: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            if active and state.service().run_status().state not in state.ACTIVE_STATES:
                st.rerun()
            body()
        return st.fragment(run_every=REFRESH_S if active else None)(wrapped)
    return decorate


def store_upload(ctx: state.Context, upload: Any, uploaded_key: str,
                 folder: str | None = None) -> str | None:
    """Copy a newly uploaded STL into the project once; its stored path, or None
    if nothing new was uploaded (or it was refused, shown as an error)."""
    if upload is None or st.session_state.get(uploaded_key) == upload.file_id:
        return None
    try:
        stored = ctx.service.store_source_file(upload.name, upload.getvalue(), folder)
    except ValueError as exc:
        st.error(str(exc))
        return None
    st.session_state[uploaded_key] = upload.file_id
    return str(stored)


def rotating_zones(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """The named rotating zones (name, axis): the STL zones, or an imported
    mesh's cell zones (M1c)."""
    mesh = draft.get("imported_mesh")
    zones = (mesh.get("zones") if isinstance(mesh, dict) else draft.get("rotating_zones"))
    return [z for z in zones or [] if isinstance(z, dict) and z.get("name")]


def list_at(draft: dict[str, Any], key: str) -> list[Any]:
    """The list under a top-level key of the draft, created when missing."""
    value = draft.get(key)
    if not isinstance(value, list):
        value = []
        draft[key] = value
    return value
