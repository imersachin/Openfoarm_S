"""Fluid and turbulence (section 9.1-9.2). Without physics the project is meshed
only; adding it makes a run also set up the case (never solve it)."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, confirm, section_header, section_issues
from app.machines_ui.sections.fields import float_field, select_field
from case_setup.physics import AIR, WATER, PhysicsConfig

MODEL_LABELS = {"K_OMEGA_SST": "k-omega SST", "LAMINAR": "Laminar"}
PRESETS = {"Air": AIR, "Water": WATER}


def render(ctx: state.Context) -> None:
    section_header(ctx, "fluid")
    if not isinstance(ctx.draft.get("physics"), dict):
        st.info("No physics yet: a run meshes only. Start from a fluid preset (about 20 °C, "
                "starting values to review):")
        columns = st.columns(len(PRESETS))
        for column, (name, fluid) in zip(columns, PRESETS.items(), strict=True):
            if column.button(f"{name}: density {fluid.density:g} kg/m³, ν "
                             f"{fluid.kinematic_viscosity:g} m²/s", key=f"machines_fluid_{name}"):
                physics = PhysicsConfig(fluid=fluid).model_dump(mode="json")
                # Outlets already typed take the default 0 Pa (the empty entry).
                physics["outlets"] = {str(p["name"]): {} for p in ctx.draft.get("patches") or []
                                      if p.get("type") == "OUTLET" and p.get("name")}
                ctx.draft["physics"] = physics
                state.bump_generation()
                st.rerun()
        return
    with apply_form("fluid"):
        st.markdown("**Fluid**")
        float_field(ctx.draft, "physics.fluid.density", "Density (kg/m³)", min_value=0.0,
                    help="Converts the pressures in Pa to kinematic pressure.")
        float_field(ctx.draft, "physics.fluid.kinematic_viscosity",
                    "Kinematic viscosity ν (m²/s)", min_value=0.0)
        st.markdown("**Turbulence**")
        select_field(ctx.draft, "physics.turbulence.model", "Model", list(MODEL_LABELS),
                     MODEL_LABELS)
        if get_path(ctx.draft, "physics.turbulence.model") == "K_OMEGA_SST":
            float_field(ctx.draft, "physics.turbulence.intensity_percent",
                        "Inlet turbulence intensity I (%)", min_value=0.0, max_value=100.0)
            float_field(ctx.draft, "physics.turbulence.length_scale",
                        "Inlet turbulence length scale L (m)", min_value=0.0,
                        help="k = 1.5 (U I)², ω = √k / (C_μ^0.25 L), C_μ = 0.09.")
    section_issues(ctx, "fluid")

    def remove() -> None:
        ctx.draft.pop("physics", None)
        state.bump_generation()

    confirm("physics", "Remove every physics setting? The project is then meshed only and "
            "no case is set up.", "Remove physics", remove,
            button_label="Remove the physics (mesh only)…")
