"""Shared pieces of the section pages (presentation only)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import streamlit as st

from app.components.widgets import render_issues
from app.vawt import state
from vawt.service import SectionState

REFRESH_S = 1.5  # status and log refresh while a run is active (spec 14.2: 1-2 s)

STATE_TEXT = {
    SectionState.EMPTY: "Not filled in yet.",
    SectionState.INCOMPLETE: "Some required values are missing.",
    SectionState.READY: "Ready.",
    SectionState.STALE: "Changed since the last mesh.",
    SectionState.ERROR: "Has problems that stop a run.",
}


def section_header(ctx: state.Context, key: str, title: str) -> None:
    st.subheader(title)
    status = ctx.analysis.sections.get(key)
    if status is None:
        return
    text = STATE_TEXT[status.state]
    if status.state is SectionState.STALE and status.stale_because:
        text += " Changed settings: " + ", ".join(f"`{p}`" for p in status.stale_because)
    st.caption(f"Status: **{status.state.value}** · {text}")


def section_issues(ctx: state.Context, key: str) -> None:
    status = ctx.analysis.sections.get(key)
    if status is not None and status.issues:
        render_issues(status.issues)


@contextmanager
def apply_form(key: str) -> Iterator[None]:
    """A form whose fields write to the draft only when Apply is pressed
    (spec 14.1 #2); Apply then reruns the page so marks and issues follow."""
    with st.form(f"vawt_form_{key}", border=False):
        yield
        applied = st.form_submit_button("Apply", type="primary")
    if applied:
        st.rerun()


def confirm(key: str, prompt: str, action_label: str, on_confirm: Callable[[], object],
            *, button_label: str) -> None:
    """A two-step confirmation: the first button asks, the second acts."""
    flag = f"vawt_confirm_{key}"
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
    """A fragment that refreshes every REFRESH_S while a run is active (spec
    14.1 #3). When the run ends it reruns the whole page once (marks, results)
    and stops refreshing."""
    def decorate(body: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            if active and state.service().run_status().state not in state.ACTIVE_STATES:
                st.rerun()
            body()
        return st.fragment(run_every=REFRESH_S if active else None)(wrapped)
    return decorate
