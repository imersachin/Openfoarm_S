"""Review: every issue, what a run will do and why, estimated cells, resources."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues
from app.vawt_ui import state
from mesh.estimator import ResourceStatus
from vawt.operations import VawtOperation

ALLOW_HIGH_RISK = "vawt_allow_high_risk"
OPERATION_LABELS = {
    VawtOperation.VALIDATE: "Validate the configuration",
    VawtOperation.IMPORT_GEOMETRY: "Import and transform the rotor",
    VawtOperation.GENERATE_CASES: "Write the OpenFOAM dictionaries",
    VawtOperation.OUTER_MESH: "Outer mesh (blockMesh, snappyHexMesh)",
    VawtOperation.ROTOR_FEATURES: "Rotor feature edges (surfaceFeatureExtract)",
    VawtOperation.ROTOR_MESH: "Rotor mesh (blockMesh, snappyHexMesh, topoSet)",
    VawtOperation.SINGLE_MESH: "Single mesh (blockMesh, snappyHexMesh)",
    VawtOperation.ASSEMBLE: "Assemble (mergeMeshes, createPatch)",
    VawtOperation.CHECK_MESH: "checkMesh",
    VawtOperation.VALIDATE_MESH: "Mesh validation",
}


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
            why = ", ".join(f"`{p}`" for p in plan.reasons.get(op, ()) if p != "<initial>")
            st.markdown(f"- {OPERATION_LABELS[op]}" + (f" — because of {why}" if why else ""))
        st.caption("Operations whose files still verify are reused when the run starts.")

    st.markdown("**Estimated cells and resources** (a heuristic, not exact)")
    preflight = ctx.service.preflight()
    if preflight.estimate is None or preflight.assessment is None:
        render_issues(preflight.issues)
        return
    estimate, assessment = preflight.estimate, preflight.assessment
    cells = st.columns(3)
    cells[0].metric("Outer mesh", f"{estimate.outer:,}")
    cells[1].metric("Rotor mesh", f"{estimate.rotor:,}")
    cells[2].metric("Total", f"{estimate.total:,}")
    st.markdown(f"Resource status: **{assessment.status.value}**")
    render_issues(assessment.issues)
    if assessment.status is ResourceStatus.HIGH_RESOURCE_RISK:
        st.checkbox("I accept the high resource risk for the next run", key=ALLOW_HIGH_RISK)
    elif assessment.status is ResourceStatus.BLOCKED:
        st.error("Meshing is blocked: reduce the mesh settings. This cannot be overridden.")
