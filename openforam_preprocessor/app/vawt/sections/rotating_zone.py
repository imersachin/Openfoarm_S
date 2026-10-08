"""Rotating zone: rotor and flow axes, cylinder, cell size, mesh point; presets.

The interface type is not shown: only AMI is offered until CELL_ZONE is
proven by a real OpenFOAM run (owner decision).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.widgets import float_field, get_path, select_field, vector_field
from app.vawt import state
from app.vawt.sections.common import apply_form, section_header, section_issues
from vawt.config import Axis
from vawt.presets import PresetKind

AXES = [a.value for a in Axis]
KEEP = ("schema_version", "project_name", "openfoam_profile", "geometry")


def preset_box(ctx: state.Context) -> None:
    geometry = ctx.draft.get("geometry") or {}
    with st.expander("Fill the settings from a preset",
                     expanded=ctx.draft.get("rotating_zone") is None):
        if not (geometry.get("source_path") and geometry.get("source_units")):
            st.info("Add the rotor STL and its units in Geometry first.")
            return
        metrics, _ = ctx.service.rotor_metrics(geometry)
        suggested = metrics.suggested_axis.value if metrics and metrics.suggested_axis else None
        current = get_path(ctx.draft, "rotor.axis") or suggested
        axis = st.selectbox("Rotor axis", AXES, index=AXES.index(current) if current else None,
                            key="vawt_preset_axis",
                            help=f"Suggested from the geometry: {suggested or 'none'}.")
        flow = st.selectbox("Flow axis (inlet on its minimum face)",
                            [a for a in AXES if a != axis], index=None, key="vawt_preset_flow")
        kind = st.selectbox("Preset", [k.value for k in PresetKind], key="vawt_preset_kind")
        replace = ctx.draft.get("rotating_zone") is None or st.checkbox(
            "Replace the current rotating-zone, domain, refinement and layer settings",
            key="vawt_preset_replace")
        if st.button("Fill from preset", key="vawt_preset_fill",
                     disabled=not (axis and flow and replace)):
            base: dict[str, Any] = {k: ctx.draft[k] for k in KEEP if k in ctx.draft}
            filled, issues = ctx.service.draft_from_preset(base, Axis(str(axis)), Axis(str(flow)),
                                                           PresetKind(kind))
            if filled is not None:
                state.replace_draft(filled)
            state.add_messages(issues)
            st.rerun()


def render(ctx: state.Context) -> None:
    section_header(ctx, "rotating_zone", "Rotating zone")
    preset_box(ctx)
    if ctx.draft.get("rotating_zone") is None:
        section_issues(ctx, "rotating_zone")
        return
    with apply_form("rotating_zone"):
        left, right = st.columns(2)
        with left:
            select_field(ctx.draft, "rotor.axis", "Rotor axis", AXES)
        with right:
            select_field(ctx.draft, "rotor.flow_axis", "Flow axis", AXES)
        st.markdown("**Cylinder** (coordinates across the axis: u, v; along it: min, max)")
        u, v = st.columns(2)
        with u:
            float_field(ctx.draft, "rotating_zone.centre_u", "Centre u (m)")
            float_field(ctx.draft, "rotating_zone.axis_min", "Axial min (m)")
            float_field(ctx.draft, "rotating_zone.diameter", "Diameter (m)", min_value=0.0)
        with v:
            float_field(ctx.draft, "rotating_zone.centre_v", "Centre v (m)")
            float_field(ctx.draft, "rotating_zone.axis_max", "Axial max (m)")
            float_field(ctx.draft, "rotating_zone.cell_size", "Cell size (m)", min_value=0.0)
        vector_field(ctx.draft, "rotating_zone.location_in_mesh", "Mesh point",
                     "m; inside the cylinder, outside the rotor")
    used = ctx.analysis.outcome.zone_cell_size
    if used is not None:
        st.caption(f"Cell size used in the mesh: {used:.6g} m")
    section_issues(ctx, "rotating_zone")
