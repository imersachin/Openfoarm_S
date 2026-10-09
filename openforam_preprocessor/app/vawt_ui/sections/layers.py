"""Boundary layers on the blades: count, growth, sizing method."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import bool_field, float_field, int_field, select_field
from app.vawt_ui import state
from app.vawt_ui.sections.common import apply_form, section_header, section_issues
from vawt.config import LayerSizing

SIZING_LABELS = {"RELATIVE": "Relative to the local cell size",
                 "ABSOLUTE": "Absolute thickness (m)"}


def render(ctx: state.Context) -> None:
    section_header(ctx, "layers", "Boundary layers")
    with apply_form("layers"):
        bool_field(ctx.draft, "layers.enabled", "Add boundary layers on the blades")
        a, b = st.columns(2)
        with a:
            int_field(ctx.draft, "layers.count", "Number of layers", min_value=1)
        with b:
            float_field(ctx.draft, "layers.expansion_ratio", "Expansion ratio", min_value=1.0)
        select_field(ctx.draft, "layers.sizing", "Sizing", [s.value for s in LayerSizing],
                     SIZING_LABELS)
        st.markdown("**Relative sizing** (fractions of the local cell size)")
        r1, r2 = st.columns(2)
        with r1:
            float_field(ctx.draft, "layers.final_layer_thickness", "Final layer thickness")
        with r2:
            float_field(ctx.draft, "layers.min_thickness", "Minimum thickness")
        st.markdown("**Absolute sizing** (m)")
        a1, a2 = st.columns(2)
        with a1:
            float_field(ctx.draft, "layers.first_layer_thickness", "First layer thickness")
        with a2:
            float_field(ctx.draft, "layers.min_thickness_m", "Minimum thickness")
    section_issues(ctx, "layers")
