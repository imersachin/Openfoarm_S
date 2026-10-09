"""Mesh results: validity and quality shown separately (simulation suitability
and CFD accuracy are never assessed), metrics against limits, acceptance limits.
The 3D views of the mesh arrive in V6."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.vawt_ui import state
from app.vawt_ui.sections.common import apply_form
from app.vawt_ui.sections.refinement import number_fields
from vawt.service import SectionState

ASSESSMENTS = (("mesh_validity", "Mesh validity"), ("mesh_quality", "Mesh quality"),
               ("simulation_suitability", "Simulation suitability"),
               ("cfd_accuracy", "CFD accuracy"))


def stale_settings(ctx: state.Context) -> list[str]:
    return [p for s in ctx.analysis.sections.values() if s.state is SectionState.STALE
            for p in s.stale_because]


def report_view(report: dict[str, Any], ctx: state.Context) -> None:
    stale = stale_settings(ctx)
    if stale:
        st.warning("Stale: these settings changed since this mesh was made: "
                   + ", ".join(f"`{p}`" for p in stale))
    assessment = report.get("assessment") or {}
    columns = st.columns(4)
    for column, (key, label) in zip(columns, ASSESSMENTS, strict=True):
        column.metric(label, str(assessment.get(key, "—")))
    if assessment.get("note"):
        st.caption(str(assessment["note"]))
    regions = report.get("regions") or {}
    if regions:
        st.caption(f"Regions: {regions.get('found')} found, {regions.get('expected')} expected")
    st.markdown("**Measured values and acceptance limits**")
    metrics = report.get("metrics") or {}
    rows = [{"check": name.replace("_", " "), "limit": f"{limit:g}",
             "measured": "—" if metrics.get(name) is None else f"{metrics[name]:g}"}
            for name, limit in sorted((report.get("limits") or {}).items())]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    for issue in report.get("issues") or []:
        st.markdown(f"- **{issue.get('severity')}** {issue.get('message')}")
    with st.expander("All checkMesh values"):
        st.json(metrics)


def render(ctx: state.Context) -> None:
    st.subheader("Mesh")
    report = ctx.service.reports()["mesh"]
    if report is None:
        st.info("No mesh report yet. Run the meshing in Run.")
    else:
        report_view(report, ctx)
    st.markdown("**Acceptance limits** (changing them re-runs validation only)")
    with apply_form("quality"):
        number_fields(ctx.draft, "quality")
    st.caption("3D views of the mesh by patch and slices arrive in V6.")
