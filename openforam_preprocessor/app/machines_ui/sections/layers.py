"""Boundary layers per body, and the estimated first-cell y+ (section 9.2,
deferred from G6): the y+ of an absolute first layer, and the first-layer
thickness for a target y+. Both are estimates, shown with their source."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, section_header, section_issues
from app.machines_ui.sections.fields import bool_field, float_field, int_field, select_field
from case_setup.wall_cells import SOURCE
from machines.project_store import WallReference

SIZING_LABELS = {"RELATIVE": "Relative to the local cell size",
                 "ABSOLUTE": "Absolute thickness (m)"}


def layer_fields(ctx: state.Context, index: int) -> None:
    path = f"bodies.{index}.layers"
    bool_field(ctx.draft, f"{path}.enabled", "Add boundary layers")
    if not get_path(ctx.draft, f"{path}.enabled"):
        return
    a, b = st.columns(2)
    with a:
        int_field(ctx.draft, f"{path}.count", "Number of layers", min_value=1, max_value=20)
    with b:
        float_field(ctx.draft, f"{path}.expansion_ratio", "Expansion ratio", min_value=1.0,
                    max_value=2.0)
    select_field(ctx.draft, f"{path}.sizing", "Sizing", list(SIZING_LABELS), SIZING_LABELS)
    if get_path(ctx.draft, f"{path}.sizing") == "ABSOLUTE":
        float_field(ctx.draft, f"{path}.first_layer_thickness", "First layer thickness (m)",
                    min_value=0.0)
        float_field(ctx.draft, f"{path}.min_thickness_m", "Minimum thickness (m)",
                    min_value=0.0)
    else:
        float_field(ctx.draft, f"{path}.final_layer_thickness",
                    "Final layer thickness (fraction of the cell)", min_value=0.0, max_value=1.0)
        float_field(ctx.draft, f"{path}.min_thickness", "Minimum thickness (fraction)",
                    min_value=0.0, max_value=1.0)


def reference_form(ctx: state.Context) -> WallReference:
    """The estimate's inputs, kept in the project's UI settings (never a run input)."""
    reference = ctx.settings.wall
    suggestions = ctx.service.speed_suggestions(ctx.draft)
    with st.form("machines_wall_reference", border=False):
        length = st.number_input("Reference length L (m), a chord say", min_value=0.0,
                                 value=reference.length_m, format="%g")
        options = {"": "Type a speed"} | {f"{s.speed:.6g}": f"{s.label}: {s.speed:.4g} m/s"
                                          for s in suggestions}
        pick = st.selectbox("Reference speed: a suggestion", list(options),
                            format_func=options.__getitem__)
        speed = st.number_input("Reference speed U (m/s)", min_value=0.0,
                                value=reference.speed_m_s, format="%g")
        target = st.number_input("Target y+", min_value=0.0, value=reference.target_y_plus,
                                 format="%g")
        saved = st.form_submit_button("Apply")
    if saved:
        chosen = float(pick) if pick else speed
        updated = WallReference(length_m=length or None, speed_m_s=chosen or None,
                                target_y_plus=target or None)
        ctx.service.save_ui_settings(ctx.settings.model_copy(update={"wall": updated}))
        st.rerun()
    return reference


def estimates(ctx: state.Context, reference: WallReference) -> None:
    bodies = ctx.draft.get("bodies") or []
    absolute = [(i, b) for i, b in enumerate(bodies)
                if get_path(ctx.draft, f"bodies.{i}.layers.sizing") == "ABSOLUTE"
                and get_path(ctx.draft, f"bodies.{i}.layers.first_layer_thickness")]
    shown = False
    for index, body in absolute:
        thickness = float(get_path(ctx.draft, f"bodies.{index}.layers.first_layer_thickness"))
        result = ctx.service.wall_estimate(ctx.draft, reference, thickness)
        for note in result.notes:
            st.caption(note)
        if result.forward is not None:
            shown = True
            st.markdown(f"**{body.get('name')}**: first layer {thickness:.4g} m gives an "
                        f"estimated **y+ ≈ {result.forward.y_plus:.3g}** (Re "
                        f"{result.forward.reynolds:.3g}, Cf {result.forward.skin_friction:.3g}, "
                        f"u_tau {result.forward.friction_velocity:.3g} m/s).")
    result = ctx.service.wall_estimate(ctx.draft, reference)
    if result.reverse is not None:
        shown = True
        thickness = result.reverse.first_layer_thickness
        st.markdown(f"For a target **y+ = {reference.target_y_plus:g}**: estimated first "
                    f"layer thickness **≈ {thickness:.3g} m**.")
        minimum = st.number_input(
            "Minimum layer thickness (m), required with absolute sizing", min_value=0.0,
            value=None, format="%g", key="machines_use_min_thickness",
            help="snappyHexMesh drops layers thinner than this; never assumed.")
        for index, body in enumerate(bodies):
            current = get_path(ctx.draft, f"bodies.{index}.layers.min_thickness_m")
            chosen = minimum or current
            if st.button(f"Use {thickness:.3g} m for {body.get('name')}",
                         key=f"machines_use_layer_{index}", disabled=not chosen):
                layers = ctx.draft["bodies"][index].setdefault("layers", {})
                layers.update(enabled=True, sizing="ABSOLUTE", first_layer_thickness=thickness,
                              min_thickness_m=float(chosen))
                state.bump_generation()
                st.rerun()
        if bodies and not minimum and not all(
                get_path(ctx.draft, f"bodies.{i}.layers.min_thickness_m")
                for i in range(len(bodies))):
            st.caption("Give the minimum layer thickness to use this thickness.")
    elif not shown:
        for note in result.notes:
            st.caption(note)
    if not absolute and bodies:
        st.caption("A y+ estimate for a body appears when its layers use absolute sizing.")
    st.caption(SOURCE)


def render(ctx: state.Context) -> None:
    section_header(ctx, "layers")
    bodies = ctx.draft.get("bodies") or []
    if bodies:
        with apply_form("layers"):
            for index, body in enumerate(bodies):
                st.markdown(f"**{body.get('name') or f'Body {index + 1}'}**")
                layer_fields(ctx, index)
    else:
        st.info("Boundary layers are set per body. This machine's walls are regions of "
                "imported surfaces, which take no layers in this version; the y+ estimate "
                "below still gives the first-cell size to aim for.")
    section_issues(ctx, "layers")
    st.markdown("**Estimated first-cell y+** (an estimate, not a solver result)")
    reference = reference_form(ctx)
    estimates(ctx, reference)
