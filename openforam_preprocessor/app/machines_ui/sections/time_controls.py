"""Time (section 9.6): the step is limited by both a rotation angle and a
maximum Courant number (adjustable time step); end time and write interval.

An imported machine (C1) steps by the angle alone, chosen as "what are you
studying?" with the hours a revolution beside each choice, and starts as
chosen (potential flow, a steady MRF solve first, or from rest)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.widgets import get_path, set_path
from app.machines_ui import state
from app.machines_ui.sections.common import (
    COURANT_NOTE,
    apply_form,
    section_header,
    section_issues,
)
from app.machines_ui.sections.fields import float_field, int_field, select_field
from case_setup.dictionaries import START_UP_LABELS
from case_setup.physics import (
    STEP_STUDIES,
    revolution_time,
    rpm_to_rad_s,
    step_for_degrees,
    steps_per_vane_passing,
)

UNIT_LABELS = {"REVOLUTIONS": "revolutions", "DEGREES": "degrees"}
OTHER = "other"


def study_label(degrees: float, study: str, hours: dict[float, float] | None,
                vanes: int | None) -> str:
    """'<study>: <deg> deg a step (about N steps a guide vane passing at Z
    vanes), about H h a revolution'."""
    text = f"{study}: {degrees:g} deg a step"
    if vanes:
        text += (f" (about {steps_per_vane_passing(degrees, vanes):.0f} steps a guide vane "
                 f"passing at {vanes} vanes)")
    if hours and degrees in hours:
        text += f", about {duration(hours[degrees])} a revolution"
    return text


def duration(hours: float) -> str:
    """'27 h', or minutes under an hour (a small mesh)."""
    return f"{hours:.0f} h" if hours >= 1 else f"{max(hours * 60, 1):.0f} min"


def study_field(ctx: state.Context, hours: dict[float, float] | None) -> None:
    """The time step as what is studied (outside the form: it writes at once)."""
    path = "physics.time.degrees_per_step"
    current = get_path(ctx.draft, path)
    shown = float(current) if current is not None else 1.0
    vanes = get_path(ctx.draft, "physics.guide_vanes.vanes")
    labels: dict[Any, str] = {d: study_label(d, s, hours, vanes) for d, s in STEP_STUDIES}
    labels[OTHER] = (f"Another step: {shown:g} deg" if shown not in labels
                     else "Another step (degrees, below)")
    options = [*(d for d, _ in STEP_STUDIES), OTHER]
    generation = st.session_state.get("draft_generation", 0)
    choice = st.radio("What are you studying?", options, format_func=labels.__getitem__,
                      index=options.index(shown) if shown in options else len(options) - 1,
                      key=f"machines_time_study_{generation}")
    if choice != OTHER and choice != shown:
        set_path(ctx.draft, path, choice)
        state.bump_generation()
        st.rerun()


def imported_render(ctx: state.Context) -> None:
    ranks = get_path(ctx.draft, "physics.ranks") or 16
    found = ctx.service.step_hours(int(ranks))
    hours, basis = found if found is not None else (None, "")
    study_field(ctx, hours)
    st.caption("A fixed step: an imported machine's largest Courant number sits in the wall "
               "cells, so it is not limited by one (C1). "
               + (f"Hours a revolution: {basis}"
                  + ("." if basis.startswith("measured") else f", on {ranks} ranks.")
                  if hours else
                  "The hours a revolution appear once the mesh is checked."))
    with apply_form("time"):
        float_field(ctx.draft, "physics.time.degrees_per_step",
                    "Rotation per time step (degrees)", min_value=0.0, max_value=90.0,
                    help="Set by the choice above; change it here for another step.")
        select_field(ctx.draft, "physics.start_up.method", "Start-up",
                     [k.value for k in START_UP_LABELS],
                     {k.value: v for k, v in START_UP_LABELS.items()},
                     default="POTENTIAL_FLOW",
                     help="Every start ran with the robust settings (C1); the steady MRF "
                          "start gave the calmest first steps.")
        int_field(ctx.draft, "physics.start_up.mrf_iterations", "Steady MRF iterations (that "
                  "start only)", min_value=1, default=100,
                  help="C1: 100 iterations took 21 min on 16 ranks (continuity 0.0012).")
        int_field(ctx.draft, "physics.ranks", "MPI ranks", min_value=1, default=16,
                  help="C1: 16 was the fastest on the owner's 24-core PC, 8 nearly as fast.")
        float_field(ctx.draft, "physics.time.end_revolutions", "End time (revolutions)",
                    min_value=0.0)
        float_field(ctx.draft, "physics.time.write_interval", "Write interval", min_value=0.0)
        select_field(ctx.draft, "physics.time.write_unit", "Write interval unit",
                     list(UNIT_LABELS), UNIT_LABELS)


def render(ctx: state.Context) -> None:
    section_header(ctx, "time")
    if not isinstance(ctx.draft.get("physics"), dict):
        st.info("Set the fluid first (Fluid and turbulence).")
        return
    if ctx.draft.get("imported_mesh") is not None:
        imported_render(ctx)
    else:
        with apply_form("time"):
            float_field(ctx.draft, "physics.time.degrees_per_step",
                        "Rotation per time step (degrees)", min_value=0.0, max_value=90.0)
            float_field(ctx.draft, "physics.time.max_courant", "Maximum Courant number",
                        min_value=0.0, max_value=100.0,
                        help="The solver shortens the step to keep it (adjustable time step).")
            float_field(ctx.draft, "physics.time.end_revolutions", "End time (revolutions)",
                        min_value=0.0)
            float_field(ctx.draft, "physics.time.write_interval", "Write interval",
                        min_value=0.0)
            select_field(ctx.draft, "physics.time.write_unit", "Write interval unit",
                         list(UNIT_LABELS), UNIT_LABELS)
        st.caption(COURANT_NOTE)
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
