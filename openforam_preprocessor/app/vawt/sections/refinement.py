"""Refinement: blade, interface, wake, features; snappyHexMesh quality controls."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.widgets import (
    bool_field,
    float_field,
    get_path,
    int_field,
    vector_field,
)
from app.vawt import state
from app.vawt.sections.common import apply_form, section_header, section_issues


def number_fields(draft: dict[str, Any], prefix: str) -> None:
    """One field per value of a flat settings group, typed by its current value."""
    for name, value in sorted((get_path(draft, prefix) or {}).items()):
        path, label = f"{prefix}.{name}", name.replace("_", " ")
        if isinstance(value, bool):
            bool_field(draft, path, label)
        elif isinstance(value, int):
            int_field(draft, path, label)
        else:
            float_field(draft, path, label)


def render(ctx: state.Context) -> None:
    section_header(ctx, "refinement", "Refinement")
    with apply_form("refinement"):
        st.markdown("**Blades and interface** (levels above the zone cell size)")
        a, b, c = st.columns(3)
        with a:
            int_field(ctx.draft, "refinement.blade_min_level", "Blade min level", min_value=0)
        with b:
            int_field(ctx.draft, "refinement.blade_max_level", "Blade max level", min_value=0)
        with c:
            int_field(ctx.draft, "refinement.interface_level", "Interface level", min_value=0)
        if get_path(ctx.draft, "refinement.wake") is not None:
            st.markdown("**Wake region**")
            vector_field(ctx.draft, "refinement.wake.box.minimum", "Minimum corner", "m")
            vector_field(ctx.draft, "refinement.wake.box.maximum", "Maximum corner", "m")
            int_field(ctx.draft, "refinement.wake.level", "Wake level", min_value=0)
        else:
            st.caption("No wake region (a preset adds one).")
        st.markdown("**Feature edges**")
        bool_field(ctx.draft, "refinement.extract_features", "Extract feature edges")
        int_field(ctx.draft, "refinement.feature_level", "Feature level", min_value=0)
        float_field(ctx.draft, "refinement.feature_angle_deg", "Feature angle (degrees)")
        with st.expander("Advanced: snappyHexMesh quality controls and cell limit"):
            number_fields(ctx.draft, "snappy_quality")
            int_field(ctx.draft, "max_global_cells", "Maximum cells (snappyHexMesh)",
                      min_value=1)
    section_issues(ctx, "refinement")
