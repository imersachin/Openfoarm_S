"""Run: start, cancel, stages, live log; interrupted runs, leftover OpenFOAM
processes and unreadable run locks, each with a confirmation. A run meshes
and, with physics, sets up the case; it never solves."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.sections.common import confirm, live
from app.machines_ui.sections.review import high_risk_accepted, operation_label
from mesh.estimator import ResourceStatus
from vawt.service import RunView


def start_blockers(ctx: state.Context) -> list[str]:
    """Why Start is disabled (empty: it is enabled)."""
    if state.saved_raw() is None:
        return ["Save the configuration first; runs use the saved configuration."]
    reasons = []
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
        elif status is ResourceStatus.HIGH_RESOURCE_RISK and not high_risk_accepted():
            reasons.append("High resource risk: accept it in Review first.")
    return reasons


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
    if state.saved_raw() is None:
        return
    plan = ctx.service.plan()
    view = ctx.service.run_status()
    done = view.state is RunView.SUCCEEDED
    current = view.stage
    passed = True
    for op in plan.operations:
        if done:
            mark = "●"
        elif op == current:
            passed = False
            mark = "▶" if view.state in state.ACTIVE_STATES else "✕" \
                if view.state in (RunView.FAILED, RunView.INTERRUPTED) else "■"
        else:
            mark = "●" if passed and current in plan.operations else "○"
        st.markdown(f"{mark} {operation_label(op)}")


def log_view(ctx: state.Context) -> None:
    tail = ctx.service.log_tail()
    if tail.path is None:
        st.caption("No log yet.")
        return
    st.caption(f"`{tail.path}` · last {tail.bytes_read:,} of {tail.size:,} bytes")
    st.code(tail.text or "(empty)", language=None)


def render(ctx: state.Context) -> None:
    st.subheader("Run")
    st.caption("A run meshes and, when physics is set, prepares the case. It never runs "
               "the solver.")
    if state.unsaved() and state.saved_raw() is not None:
        st.warning(f"Unsaved changes: a run uses saved revision {state.revision()}.")
    recovery(ctx)
    blockers = start_blockers(ctx)
    start, cancel = st.columns(2)
    if start.button("Start run", key="machines_start", type="primary", disabled=bool(blockers)):
        result = ctx.service.start_run(
            allow_high_resource_risk=high_risk_accepted())
        state.add_messages(result.issues)
        st.rerun()
    if cancel.button("Cancel run", key="machines_cancel", disabled=not ctx.run.can_cancel):
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
