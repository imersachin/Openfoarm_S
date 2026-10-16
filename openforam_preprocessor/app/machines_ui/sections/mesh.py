"""Mesh results, apart from case setup: the mesh stage of the last run,
validity and quality shown separately (simulation suitability and CFD
accuracy are never assessed), measured values against the acceptance limits,
and a check of any mesh folder (one this app did not build, too)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form
from app.machines_ui.sections.fields import bool_field, float_field, int_field
from vawt.config import StrictMeshQualityLimits
from vawt.service import SectionState

ASSESSMENTS = (("mesh_validity", "Mesh validity"), ("mesh_quality", "Mesh quality"),
               ("simulation_suitability", "Simulation suitability"),
               ("cfd_accuracy", "CFD accuracy"))
STAGE_MARKS = {"SUCCESS": "●", "SKIPPED": "○", "FAILED": "✕", "CANCELLED": "■"}


def limits_fields(ctx: state.Context) -> None:
    """Every acceptance limit, shown at its model default when the draft has none."""
    for name, default in StrictMeshQualityLimits().model_dump(mode="json").items():
        path, label = f"quality.{name}", name.replace("_", " ")
        if isinstance(default, bool):
            bool_field(ctx.draft, path, label, default=default)
        elif isinstance(default, int):
            int_field(ctx.draft, path, label, default=default)
        elif isinstance(default, float) or default is None:
            float_field(ctx.draft, path, label, default=default)


def stage_view(stage: dict[str, Any] | None, what: str) -> None:
    """One stage of the last run: status, reason code and message."""
    if not stage:
        st.caption(f"{what}: no run yet.")
        return
    status = str(stage.get("status"))
    text = f"{STAGE_MARKS.get(status, '?')} **{what}: {status}**"
    if stage.get("reason"):
        text += f" · `{stage['reason']}`"
    st.markdown(text)
    if stage.get("message"):
        st.caption(str(stage["message"]))


def stale_settings(ctx: state.Context) -> list[str]:
    return [p for s in ctx.analysis.sections.values() if s.state is SectionState.STALE
            for p in s.stale_because if not p.startswith("physics")]


def report_view(report: dict[str, Any], ctx: state.Context) -> None:
    stale = stale_settings(ctx)
    if stale:
        st.warning("Stale: these meshing settings changed since this mesh was made: "
                   + ", ".join(f"`{p}`" for p in stale[:8]))
    assessment = report.get("assessment") or {}
    columns = st.columns(4)
    for column, (key, label) in zip(columns, ASSESSMENTS, strict=True):
        column.metric(label, str(assessment.get(key, "—")))
    if assessment.get("note"):
        st.caption(str(assessment["note"]))
    regions = report.get("regions") or {}
    if regions:
        st.caption(f"Regions: {regions.get('found')} found, {regions.get('expected')} expected")
    for weights in report.get("ami_weights") or []:
        st.caption(f"AMI {weights.get('source')} / {weights.get('target')}: sum(weights) "
                   f"{weights.get('minimum'):.3g}–{weights.get('maximum'):.3g}")
    st.markdown("**Measured values and acceptance limits**")
    metrics = report.get("metrics") or {}
    rows = [{"check": name.replace("_", " "), "limit": f"{limit:g}",
             "measured": "—" if metrics.get(name) is None else f"{metrics[name]:g}"}
            for name, limit in sorted((report.get("limits") or {}).items())]
    st.dataframe(rows, hide_index=True, width="stretch")
    for issue in report.get("issues") or []:
        st.markdown(f"- **{issue.get('severity')}** {issue.get('message')}")
    with st.expander("All checkMesh values"):
        st.json(metrics)


def folder_check(ctx: state.Context) -> None:
    with st.expander("Check a mesh folder (any polyMesh; nothing is run)"):
        st.caption("A case folder with constant/polyMesh, or the polyMesh folder. Quality and "
                   "the region count need checkMesh output in the folder (log.checkMesh).")
        folder = st.text_input("Mesh folder (inside WSL)", key="machines_check_folder")
        if st.button("Check", key="machines_check_folder_go", disabled=not folder):
            report = ctx.service.check_mesh_folder(Path(folder))
            st.write({
                "Patches": [f"{p.name} ({p.patch_type}, {p.n_faces:,} faces)"
                            for p in report.patches],
                "Cell zones": list(report.cell_zones) or "none",
                "AMI pairs": [f"{a} / {b}" for a, b in report.ami_pairs] or "none",
                "Regions": report.regions if report.regions is not None else "unknown",
                "Validity": report.quality.validity.value if report.quality else "not assessed",
                "Quality": report.quality.quality.value if report.quality else "not assessed",
            })
            render_issues(report.issues)


def render(ctx: state.Context) -> None:
    st.subheader("Mesh")
    stage_view(ctx.service.last_stages()["mesh"], "Mesh")
    report = ctx.service.reports()["mesh"]
    if report is None:
        st.info("No mesh report yet. Run the meshing in Run.")
    else:
        report_view(report, ctx)
    st.markdown("**Acceptance limits** (changing them re-runs validation only)")
    with apply_form("quality"):
        limits_fields(ctx)
    folder_check(ctx)
