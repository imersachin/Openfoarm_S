"""Operating point (Francis; decision Q2): discharge Q (a flow-rate inlet),
runner speed N (rpm) and its direction, outlet pressure (Pa, default 0). The
net head is not an input: a rated value can be kept as a labelled reference."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.boundary import default_outlets, patches_of
from app.machines_ui.sections.common import apply_form, section_header, section_issues
from app.machines_ui.sections.fields import float_field
from app.machines_ui.sections.rotation import complete_kind, direction_field, omega_text
from machines.presets.francis import SPECIFIC_SPEED_SOURCE


def flow_rate_kind(ctx: state.Context, inlet: str) -> None:
    """A discharge entered makes its inlet a flow-rate inlet (the turbine's Q)."""
    value = get_path(ctx.draft, f"physics.inlets.{inlet}")
    if isinstance(value, dict) and "volumetric_flow_rate" in value \
            and value.get("kind") != "FLOW_RATE":
        ctx.draft["physics"]["inlets"][inlet] = {
            "kind": "FLOW_RATE", "volumetric_flow_rate": value["volumetric_flow_rate"]}


def render(ctx: state.Context) -> None:
    section_header(ctx, "operating_point")
    if not isinstance(ctx.draft.get("physics"), dict):
        st.info("Set the fluid first (Fluid and turbulence; Water for a turbine).")
        return
    inlets, outlets = patches_of(ctx, "INLET"), patches_of(ctx, "OUTLET")
    zones = [z for z in ctx.draft.get("rotating_zones") or [] if z.get("name")]
    if not inlets or not outlets or not zones:
        st.info("Type the inlet and outlet patches (Patch types) and build the water "
                "passage first.")
        return
    runner = zones[0]
    with apply_form("operating_point", on_apply=lambda: default_outlets(ctx, outlets)):
        for name in inlets:
            float_field(ctx.draft, f"physics.inlets.{name}.volumetric_flow_rate",
                        f"Discharge Q at {name} (m³/s)", min_value=0.0,
                        help="A flow-rate inlet, applied normal to the inlet.")
            flow_rate_kind(ctx, name)
        float_field(ctx.draft, f"physics.rotation.{runner['name']}.speed.rpm",
                    f"Runner speed N (rpm), {runner['name']}", min_value=0.0)
        complete_kind(ctx, runner["name"], "RPM")
        direction_field(ctx, runner["name"], runner.get("axis"))
        for name in outlets:
            float_field(ctx.draft, f"physics.outlets.{name}.pressure_pa",
                        f"Outlet static (gauge) pressure at {name} (Pa)", default=0.0)
    text = omega_text(ctx, runner["name"])
    if text:
        st.caption(text)
    section_issues(ctx, "operating_point")

    st.markdown("**Rated net head (reference only)**")
    with st.form("machines_net_head", border=False):
        head = st.number_input("Rated net head H (m): shown for reference, never a case input",
                               min_value=0.0, value=ctx.settings.rated_net_head_m, format="%g")
        saved = st.form_submit_button("Keep as reference")
    if saved:
        ctx.service.save_ui_settings(
            ctx.settings.model_copy(update={"rated_net_head_m": head or None}))
        st.rerun()
    reference = ctx.service.operating_reference(ctx.draft, ctx.settings)
    if "specific_speed_nq" in reference:
        st.caption(f"Specific speed n_q ≈ {reference['specific_speed_nq']:.4g} (reference). "
                   + SPECIFIC_SPEED_SOURCE)
    elif ctx.settings.rated_net_head_m:
        st.caption("The specific speed appears once Q and N are set.")
