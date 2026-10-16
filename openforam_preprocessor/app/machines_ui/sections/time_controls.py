"""Time (section 9.6): the step is limited by both a rotation angle and a
maximum Courant number (adjustable time step); end time and write interval."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, section_header, section_issues
from app.machines_ui.sections.fields import float_field, select_field
from case_setup.physics import revolution_time, rpm_to_rad_s, step_for_degrees

UNIT_LABELS = {"REVOLUTIONS": "revolutions", "DEGREES": "degrees"}


def render(ctx: state.Context) -> None:
    section_header(ctx, "time")
    if not isinstance(ctx.draft.get("physics"), dict):
        st.info("Set the fluid first (Fluid and turbulence).")
        return
    with apply_form("time"):
        float_field(ctx.draft, "physics.time.degrees_per_step",
                    "Rotation per time step (degrees)", min_value=0.0, max_value=90.0)
        float_field(ctx.draft, "physics.time.max_courant", "Maximum Courant number",
                    min_value=0.0, max_value=100.0,
                    help="The solver shortens the step to keep it (adjustable time step).")
        float_field(ctx.draft, "physics.time.end_revolutions", "End time (revolutions)",
                    min_value=0.0)
        float_field(ctx.draft, "physics.time.write_interval", "Write interval", min_value=0.0)
        select_field(ctx.draft, "physics.time.write_unit", "Write interval unit",
                     list(UNIT_LABELS), UNIT_LABELS)
    rotation = ctx.draft["physics"].get("rotation") or {}
    rpm = next((get_path(r, "speed.rpm") for r in rotation.values()
                if isinstance(r, dict) and get_path(r, "speed.kind") == "RPM"), None)
    degrees = get_path(ctx.draft, "physics.time.degrees_per_step")
    try:
        if rpm and degrees:
            omega = rpm_to_rad_s(float(rpm))
            st.caption(f"At {float(rpm):g} rpm: at most "
                       f"{step_for_degrees(float(degrees), omega):.4g} s per step; one "
                       f"revolution takes {revolution_time(omega):.4g} s.")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    section_issues(ctx, "time")
