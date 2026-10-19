"""Mesh results, apart from case setup: the mesh stage of the last run,
validity and quality shown separately (simulation suitability and CFD
accuracy are never assessed), measured values against the acceptance limits,
and a check of any mesh folder (one this app did not build, too). For an
imported mesh (M1c): what the import did, and the optional check of the AMI
weights over one revolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form
from app.machines_ui.sections.fields import bool_field, float_field, int_field
from core.issues import Issue
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
    when = f" {report['ami_weights_when']}" if report.get("ami_weights_when") else ""
    span = report.get("ami_weights_range")
    for weights in report.get("ami_weights") or []:
        st.caption(f"AMI {weights.get('source')} / {weights.get('target')}{when}: "
                   f"sum(weights) {weights.get('minimum'):.3g}–{weights.get('maximum'):.3g}"
                   + (f" (WARNING outside {span[0]:g}–{span[1]:g})" if span else ""))
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


def import_view(report: dict[str, Any]) -> None:
    """What the import did: converter, OpenFOAM, cells, names, zone radius, areas."""
    openfoam = report.get("openfoam") or {}
    st.markdown(f"**Imported** from `{report.get('source')}` ({report.get('format')} "
                f"{report.get('version') or ''}) with {report.get('converter')} on OpenFOAM "
                f"{openfoam.get('version')}; units {report.get('units')} "
                f"(x {report.get('scale_to_metres'):g} to metres).")
    st.caption(f"Cells: {report.get('cells')}"
               + (f" (the file declares {report['declared_cells']})"
                  if report.get("declared_cells") is not None else "")
               + f" · cell zones: {', '.join(report.get('cell_zones') or []) or 'none'}")
    for zone, radius in (report.get("zone_radius_m") or {}).items():
        if radius is not None:
            st.caption(f"Zone {zone}: radius {radius:.4g} m (computed from the zone)")
    areas = report.get("patch_area_m2") or {}
    rows = [{"original name": name, "name used": used,
             "area m² (measured from the mesh)": f"{areas[used]:.4g}" if used in areas else "—"}
            for name, used in (report.get("names_used") or {}).items()]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")


def revolution_view(ctx: state.Context, report: dict[str, Any] | None) -> None:
    """The optional check of the AMI weights over one revolution."""
    st.markdown("**AMI weights over one revolution** (optional: the zone is turned without "
                "solving the flow)")
    if st.button("Check over one revolution (36 x 10 deg)", key="machines_ami_revolution",
                 disabled=ctx.active):
        with st.spinner("Turning the zone…"):
            result = ctx.service.check_ami_revolution()
        state.add_messages(i for i in result.issues if not result.done
                           or i.code != "AMI_WEIGHTS_OUT_OF_RANGE")
        st.rerun()
    if report is None:
        st.caption("Not checked yet.")
        return
    if not ctx.service.revolution_is_current(report):
        st.warning("This check is of an earlier mesh or rotating zone (cell zone, axis or "
                   "point); check again.")
    span = report.get("range") or [0.95, 1.05]
    for weights in report.get("weights") or []:
        st.caption(f"AMI {weights.get('source')} / {weights.get('target')} "
                   f"{report.get('when')}: sum(weights) {weights.get('minimum'):.4g}–"
                   f"{weights.get('maximum'):.4g} (WARNING outside {span[0]:g}–{span[1]:g})")
    st.caption(f"{report.get('steps')} of {report.get('planned_steps')} steps; the turned mesh "
               f"failed a mesh check at {report.get('failed_steps')}. {report.get('note')}")
    render_issues([Issue.from_dict(i) for i in report.get("issues") or []])


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
    reports = ctx.service.reports()
    report = reports["mesh"]
    imported = isinstance(ctx.draft.get("imported_mesh"), dict)
    if imported and reports["import"] is not None:
        import_view(reports["import"])
    if report is None:
        st.info("No mesh report yet. Run the meshing in Run.")
    else:
        report_view(report, ctx)
    if imported and report is not None:
        revolution_view(ctx, reports["ami_revolution"])
    st.markdown("**Acceptance limits** (changing them re-runs validation only)")
    with apply_form("quality"):
        limits_fields(ctx)
    folder_check(ctx)
