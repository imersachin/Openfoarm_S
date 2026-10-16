"""Review: every issue, what a run will do, estimated cells and resources."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from machines.operations import (
    ASSEMBLE,
    CASE_SETUP,
    CHECK_MESH,
    GENERATE_CASES,
    MESH_PREFIX,
    VALIDATE,
    VALIDATE_MESH,
)
from mesh.estimator import ResourceStatus

# The acceptance is kept apart from its checkbox (a widget's state is dropped as
# soon as another section is shown), for the saved revision it was given for.
ALLOW_HIGH_RISK = "machines_allow_high_risk"
ACCEPT_BOX = "machines_allow_high_risk_box"


def high_risk_accepted() -> bool:
    """The high resource risk was accepted for the saved revision a run uses."""
    stored = st.session_state.get(ALLOW_HIGH_RISK)
    return isinstance(stored, dict) and stored.get("revision") == state.revision()


def _remember_acceptance() -> None:
    accepted = bool(st.session_state.get(ACCEPT_BOX))
    st.session_state[ALLOW_HIGH_RISK] = {"revision": state.revision()} if accepted else None
OPERATION_LABELS = {
    VALIDATE: "Validate the configuration",
    GENERATE_CASES: "Write the OpenFOAM dictionaries",
    ASSEMBLE: "Assemble (mergeMeshes, createPatch, patch types)",
    CHECK_MESH: "checkMesh and AMI weights",
    VALIDATE_MESH: "Mesh validation",
    CASE_SETUP: "Case setup (fields and dictionaries; not solved)",
}


def operation_label(op: str) -> str:
    if op.startswith(MESH_PREFIX):
        return f"Mesh {op.removeprefix(MESH_PREFIX)} (blockMesh, snappyHexMesh…)"
    return OPERATION_LABELS.get(op, op)


def render(ctx: state.Context) -> None:
    st.subheader("Review")
    if state.unsaved():
        st.warning("You have unsaved changes. The plan and resource estimate below are "
                   "for the saved configuration, which is what a run uses.")
    st.markdown("**Issues in the current draft**")
    issues = ctx.analysis.outcome.issues
    if issues:
        render_issues(issues)
    else:
        st.success("No issues.")

    st.markdown("**What a run will do**")
    plan = ctx.service.plan()
    if not plan.operations:
        render_issues(plan.issues)
    else:
        if plan.from_scratch:
            st.caption("No mesh yet: everything runs.")
        for op in plan.operations:
            st.markdown(f"- {operation_label(op)}")
        st.caption("Operations whose files still verify are reused when the run starts. "
                   + ("The run also sets up the case." if plan.case_setup else
                      "No physics: the run meshes only."))

    st.markdown("**Estimated cells and resources** (a heuristic, not exact)")
    preflight = ctx.service.preflight()
    if preflight.estimate is None or preflight.assessment is None:
        render_issues(preflight.issues)
        return
    estimate, assessment = preflight.estimate, preflight.assessment
    st.dataframe([{"mesh": name, "estimated cells": f"{cells:,}"}
                  for name, cells in estimate.per_case.items()], hide_index=True,
                 width="stretch")
    st.metric("Total estimated cells", f"{estimate.total:,}")
    st.markdown(f"Resource status: **{assessment.status.value}**")
    render_issues(assessment.issues)
    if assessment.status is ResourceStatus.HIGH_RESOURCE_RISK:
        st.checkbox("I accept the high resource risk for the next run (this saved revision)",
                    value=high_risk_accepted(), key=ACCEPT_BOX, on_change=_remember_acceptance)
    elif assessment.status is ResourceStatus.BLOCKED:
        st.error("Meshing is blocked: reduce the mesh settings. This cannot be overridden.")
