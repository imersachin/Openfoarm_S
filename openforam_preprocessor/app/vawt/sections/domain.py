"""Domain: bounds, cell size, patch names, mesh point.

The domain cannot be switched off here: without it the project is rotor-only
(CELL_ZONE), which is not offered until a real OpenFOAM run proves it.
"""

from __future__ import annotations

import streamlit as st

from app.components.widgets import float_field, text_field, vector_field
from app.vawt import state
from app.vawt.sections.common import apply_form, section_header, section_issues

PATCHES = (("inlet", "Inlet (minimum face of the flow axis)"),
           ("outlet", "Outlet (maximum face)"), ("lateral_min", "Lateral min"),
           ("lateral_max", "Lateral max"), ("axial_min", "Axial min"),
           ("axial_max", "Axial max"))


def render(ctx: state.Context) -> None:
    section_header(ctx, "domain", "Domain")
    if ctx.draft.get("domain") is None:
        st.info("No domain yet: fill the settings from a preset in Rotating zone.")
        section_issues(ctx, "domain")
        return
    with apply_form("domain"):
        vector_field(ctx.draft, "domain.bounds.minimum", "Minimum corner", "m")
        vector_field(ctx.draft, "domain.bounds.maximum", "Maximum corner", "m")
        float_field(ctx.draft, "domain.cell_size", "Cell size (m)", min_value=0.0)
        vector_field(ctx.draft, "domain.location_in_mesh", "Mesh point",
                     "m; inside the domain, outside the cylinder")
        with st.expander("Patch names"):
            for key, label in PATCHES:
                text_field(ctx.draft, f"domain.patches.{key}", label)
    section_issues(ctx, "domain")
