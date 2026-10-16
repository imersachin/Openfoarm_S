"""Boundary values (section 9.4): a velocity or flow rate for each inlet patch,
a static (gauge) pressure for each outlet patch."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, section_header, section_issues
from app.machines_ui.sections.fields import float_field, vector_field

INLET_KINDS = {"VELOCITY": "Velocity (m/s)", "FLOW_RATE": "Volumetric flow rate (m³/s)"}


def patches_of(ctx: state.Context, kind: str) -> list[str]:
    return [str(p["name"]) for p in ctx.draft.get("patches") or []
            if p.get("type") == kind and p.get("name")]


def inlet_kind(ctx: state.Context, name: str) -> str | None:
    """The kind of an inlet's value, chosen outside the form so its fields follow."""
    current = get_path(ctx.draft, f"physics.inlets.{name}.kind")
    generation = st.session_state.get("draft_generation", 0)
    kind = st.radio(f"Inlet {name}", list(INLET_KINDS), format_func=INLET_KINDS.__getitem__,
                    index=list(INLET_KINDS).index(current) if current in INLET_KINDS else None,
                    key=f"machines_inlet_kind_{name}_{generation}", horizontal=True)
    if kind is not None and kind != current:
        ctx.draft["physics"].setdefault("inlets", {})[name] = {"kind": kind}
        state.bump_generation()
        st.rerun()
    return kind


def default_outlets(ctx: state.Context, outlets: list[str]) -> None:
    """An outlet without a value takes the model's default, 0 Pa: its entry is
    the empty one, which means exactly that (an outlet needs an entry)."""
    values = ctx.draft["physics"].setdefault("outlets", {})
    for name in outlets:
        values.setdefault(name, {})


def render(ctx: state.Context) -> None:
    section_header(ctx, "boundary")
    if not isinstance(ctx.draft.get("physics"), dict):
        st.info("Set the fluid first (Fluid and turbulence).")
        return
    inlets, outlets = patches_of(ctx, "INLET"), patches_of(ctx, "OUTLET")
    if not inlets and not outlets:
        st.info("No patch is typed INLET or OUTLET yet (Patches).")
        return
    default_outlets(ctx, outlets)
    kinds = {name: inlet_kind(ctx, name) for name in inlets}
    with apply_form("boundary"):
        for name, kind in kinds.items():
            if kind == "VELOCITY":
                vector_field(ctx.draft, f"physics.inlets.{name}.velocity",
                             f"Velocity at {name}", "m/s")
            elif kind == "FLOW_RATE":
                float_field(ctx.draft, f"physics.inlets.{name}.volumetric_flow_rate",
                            f"Flow rate at {name} (m³/s)", min_value=0.0,
                            help="Applied normal to the inlet.")
        for name in outlets:
            float_field(ctx.draft, f"physics.outlets.{name}.pressure_pa",
                        f"Static (gauge) pressure at outlet {name} (Pa)", default=0.0)
    section_issues(ctx, "boundary")
