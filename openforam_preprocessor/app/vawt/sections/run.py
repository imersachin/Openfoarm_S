"""Run: start, cancel, stages, live log; interrupted runs, leftover OpenFOAM
processes and unreadable run locks, each with a confirmation."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues
from app.vawt import state
from app.vawt.sections.common import confirm, live
from app.vawt.sections.review import ALLOW_HIGH_RISK, OPERATION_LABELS
from mesh.estimator import ResourceStatus
from vawt.operations import operations_for
from vawt.service import RunView
from vawt.validation import parse_config


def start_blockers(ctx: state.Context) -> list[str]:
    """Why Start is disabled (empty: it is enabled)."""
    saved = state.saved_raw()
    if saved is None:
        return ["Save the configuration first; runs use the saved configuration."]
    reasons = []
    not_offered = ctx.service.layout_not_offered(saved)
    if not_offered:
        reasons.append(not_offered)
    if ctx.active:
        reasons.append("A run of this project is already active.")
    if ctx.run.state is RunView.LOCK_UNREADABLE:
        reasons.append("The run lock is unreadable; remove it first.")
    if ctx.run.orphan_pid is not None:
        reasons.append("An OpenFOAM process from the interrupted run is still running.")
    if not reasons:
        preflight = ctx.service.preflight()
        status = preflight.assessment.status if preflight.assessment else None
        if status is ResourceStatus.BLOCKED:
            reasons.append("Resource preflight BLOCKED meshing (see Review).")
        elif status is ResourceStatus.HIGH_RESOURCE_RISK and not st.session_state.get(
                ALLOW_HIGH_RISK):
            reasons.append("High resource risk: accept it in Review first.")
    return reasons


def layout_banner(ctx: state.Context) -> None:
    reason = ctx.service.layout_not_offered(state.saved_raw()) or \
        ctx.service.layout_not_offered(ctx.draft)
    if not reason:
        return
    st.warning(reason + " The setting is kept as it is; Start stays disabled.")
    if st.button("Switch to AMI", key="vawt_switch_ami"):
        switched, issues = ctx.service.switch_to_ami(ctx.draft)
        if switched is not None:
            state.replace_draft(switched)
            state.add_messages(issues)
            st.session_state["vawt_switched"] = True
        else:
            state.add_messages(issues)
        st.rerun()
    if st.session_state.pop("vawt_switched", False):
        st.info("Switched the draft to AMI. Review the domain, then Save.")


def recovery(ctx: state.Context) -> None:
    run = ctx.run
    if run.state in (RunView.INTERRUPTED, RunView.LOCK_UNREADABLE, RunView.RUNNING_ELSEWHERE):
        render_issues(run.issues)
    if run.orphan_pid is not None:
        command = next((i.details.get("command") or {} for i in run.issues
                        if i.code == "ORPHANED_OPENFOAM_PROCESS"), {})
        name = command.get("name", "OpenFOAM")
        confirm("orphan", f"Stop {name} (PID {run.orphan_pid})? Its partial output is "
                "discarded; the step re-runs next time.", "Stop process",
                ctx.service.terminate_orphan, button_label="Stop process…")
    if run.state is RunView.LOCK_UNREADABLE:
        confirm("lock", "Remove the run lock? Do this only if no run of this project is "
                "active anywhere.", "Remove lock", ctx.service.remove_unreadable_lock,
                button_label="Remove lock…")


def stages(ctx: state.Context) -> None:
    config = parse_config(state.saved_raw())[0] if state.saved_raw() else None
    if config is None:
        return
    view = ctx.service.run_status()
    for index, op in enumerate(operations_for(config), start=1):
        if view.state is RunView.SUCCEEDED or index < view.step:
            mark = "●"
        elif index == view.step:
            mark = "▶" if view.state in state.ACTIVE_STATES else "✕" \
                if view.state in (RunView.FAILED, RunView.INTERRUPTED) else "■"
        else:
            mark = "○"
        st.markdown(f"{mark} {OPERATION_LABELS[op]}")


def log_view(ctx: state.Context) -> None:
    tail = ctx.service.log_tail()
    if tail.path is None:
        st.caption("No log yet.")
        return
    st.caption(f"`{tail.path}` · last {tail.bytes_read:,} of {tail.size:,} bytes")
    st.code(tail.text or "(empty)", language=None)


def render(ctx: state.Context) -> None:
    st.subheader("Run")
    layout_banner(ctx)
    if state.unsaved() and state.saved_raw() is not None:
        st.warning(f"Unsaved changes: a run uses saved revision {state.revision()}.")
    recovery(ctx)

    blockers = start_blockers(ctx)
    start, cancel = st.columns(2)
    if start.button("Start run", key="vawt_start", type="primary", disabled=bool(blockers)):
        result = ctx.service.start_run(
            allow_high_resource_risk=bool(st.session_state.get(ALLOW_HIGH_RISK)))
        state.add_messages(result.issues)
        st.rerun()
    if cancel.button("Cancel run", key="vawt_cancel", disabled=not ctx.run.can_cancel):
        ctx.service.cancel_run()
        st.rerun()
    for reason in blockers:
        st.caption(f"Start is disabled: {reason}")

    @live(ctx.active)
    def progress() -> None:
        stages(ctx)
        log_view(ctx)

    progress()
    if ctx.run.state in (RunView.SUCCEEDED, RunView.FAILED, RunView.CANCELLED):
        render_issues(ctx.run.issues)
