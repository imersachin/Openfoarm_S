"""Refinement: per body (surface levels, feature edges), per zone (interface,
hole), per imported surface region (a level for a closed solid such as vanes
or blades), and the snappyHexMesh controls and cell limit."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, section_header, section_issues
from app.machines_ui.sections.fields import bool_field, float_field, int_field, number_fields


def region_levels(ctx: state.Context, path: str, owner: str, label: str) -> None:
    """A level per region of one imported surface (empty: not refined)."""
    regions = ctx.service.surface_regions(ctx.draft).get(owner, ())
    current: dict[str, Any] = get_path(ctx.draft, path) or {}
    rows = [{"region": r, "level": current.get(r)} for r in regions]
    if not rows:
        st.caption(f"{label}: its regions are listed once its file can be read.")
        return
    generation = st.session_state.get("draft_generation", 0)
    edited = st.data_editor(rows, key=f"machines_levels_{owner}_{generation}",
                            disabled=["region"], hide_index=True, width="stretch",
                            column_config={"level": st.column_config.NumberColumn(
                                "level", min_value=0, max_value=10, step=1)})
    if st.button(f"Apply {label} levels", key=f"machines_levels_apply_{owner}"):
        levels = {r["region"]: int(r["level"]) for r in edited if r.get("level") is not None}
        *parents, leaf = path.split(".")
        target: Any = ctx.draft
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[leaf] = levels
        state.bump_generation()
        st.rerun()


def render(ctx: state.Context) -> None:
    section_header(ctx, "refinement")
    bodies = ctx.draft.get("bodies") or []
    zones = ctx.draft.get("rotating_zones") or []
    with apply_form("refinement"):
        for index, body in enumerate(bodies):
            path = f"bodies.{index}.refinement"
            st.markdown(f"**{body.get('name') or f'Body {index + 1}'}** (levels above the "
                        "cell size of its mesh)")
            a, b, c = st.columns(3)
            with a:
                int_field(ctx.draft, f"{path}.min_level", "Min level", min_value=0)
            with b:
                int_field(ctx.draft, f"{path}.max_level", "Max level", min_value=0)
            with c:
                int_field(ctx.draft, f"{path}.feature_level", "Feature level", min_value=0)
            bool_field(ctx.draft, f"{path}.extract_features", "Extract feature edges")
            float_field(ctx.draft, f"{path}.feature_angle_deg", "Feature angle (degrees)")
        for index, zone in enumerate(zones):
            st.markdown(f"**Zone {zone.get('name') or index + 1}**")
            int_field(ctx.draft, f"rotating_zones.{index}.interface_level",
                      "Interface level", min_value=0, max_value=10)
            if get_path(ctx.draft, f"rotating_zones.{index}.shape.hole_diameter"):
                int_field(ctx.draft, f"rotating_zones.{index}.shape.hole_level",
                          "Hole interface level", min_value=0, max_value=10)
        with st.expander("Advanced: snappyHexMesh quality controls and cell limit"):
            number_fields(ctx.draft, "snappy_quality")
            int_field(ctx.draft, "max_global_cells", "Maximum cells (snappyHexMesh)",
                      min_value=1)
    for index, part in enumerate(get_path(ctx.draft, "domain.parts") or []):
        st.markdown(f"**Regions of {part.get('name')}**")
        region_levels(ctx, f"domain.parts.{index}.refinement", f"domain.parts.{index}",
                      str(part.get("name")))
    for index, zone in enumerate(zones):
        if get_path(ctx.draft, f"rotating_zones.{index}.shape.kind") == "IMPORTED":
            st.markdown(f"**Regions of zone {zone.get('name')}**")
            region_levels(ctx, f"rotating_zones.{index}.shape.refinement",
                          f"rotating_zones.{index}", str(zone.get("name")))
    section_issues(ctx, "refinement")
