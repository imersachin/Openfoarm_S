"""Rotation (section 9.3): per rotating zone, the speed as rpm or tip-speed
ratio (as the machine allows) and the direction, explicit by the right-hand
rule about the zone's axis, never assumed. Both rad/s and rpm are shown, with
the tip radius a tip-speed ratio used. The tip radius measured from the rotor
is offered beside it and set only by its button (M1c), labelled as the outer
swept radius (blade corners), which can be slightly larger than the blade
mounting radius a tip-speed ratio usually uses (U1, owner)."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import get_path, render_issues, set_path
from app.machines_ui import state
from app.machines_ui.sections.common import (
    apply_form,
    rotating_zones,
    section_header,
    section_issues,
)
from app.machines_ui.sections.fields import float_field, select_field
from case_setup.physics import rad_s_to_rpm, rpm_to_rad_s, tip_speed_omega

SPEED_LABELS = {"RPM": "Rotation speed (rpm)", "TIP_SPEED_RATIO": "Tip-speed ratio λ"}
SWEPT_RADIUS = "outer swept radius (blade corners)"
TSR_RADIUS_NOTE = ("A tip-speed ratio usually uses the blade mounting radius, which may be "
                   "slightly smaller than the outer swept radius.")
DIRECTION_LABELS = {
    "POSITIVE": "Positive: counter-clockwise seen from the +axis tip (right-hand rule)",
    "NEGATIVE": "Negative: clockwise seen from the +axis tip",
}


def speed_kind(ctx: state.Context, zone: str) -> str | None:
    """How the zone's speed is given, chosen outside the form so its fields follow."""
    path = f"physics.rotation.{zone}"
    options = list(ctx.profile.speed_inputs)
    current = get_path(ctx.draft, f"{path}.speed.kind")
    if len(options) == 1:
        return options[0]  # written with the first value entered (complete_kind)
    generation = st.session_state.get("draft_generation", 0)
    kind = st.radio(f"Speed of {zone} as", options, format_func=SPEED_LABELS.__getitem__,
                    index=options.index(current) if current in options else None,
                    horizontal=True, key=f"machines_speed_kind_{zone}_{generation}")
    if kind is not None and kind != current:
        rotation = ctx.draft["physics"].setdefault("rotation", {})
        rotation.setdefault(zone, {})["speed"] = {"kind": kind}
        state.bump_generation()
        st.rerun()
    return kind


def complete_kind(ctx: state.Context, zone: str, kind: str | None) -> None:
    """A speed value entered without its kind (a single-input machine) gets it."""
    speed = get_path(ctx.draft, f"physics.rotation.{zone}.speed")
    if kind and isinstance(speed, dict) and speed and speed.get("kind") != kind:
        speed["kind"] = kind


def omega_text(ctx: state.Context, zone: str) -> str | None:
    speed = get_path(ctx.draft, f"physics.rotation.{zone}.speed") or {}
    try:
        if speed.get("kind") == "RPM" and speed.get("rpm"):
            omega = rpm_to_rad_s(float(speed["rpm"]))
        elif speed.get("kind") == "TIP_SPEED_RATIO" and speed.get("free_stream_speed"):
            omega = tip_speed_omega(float(speed["tip_speed_ratio"]),
                                    float(speed["free_stream_speed"]), float(speed["radius"]))
        else:
            return None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    text = f"ω = {omega:.6g} rad/s = {rad_s_to_rpm(omega):.6g} rpm"
    if speed.get("kind") == "TIP_SPEED_RATIO":
        text += f", with tip radius R = {float(speed['radius']):g} m"
    return text


def direction_field(ctx: state.Context, zone: str, axis: str | None) -> None:
    labels = {k: v.replace("+axis", f"+{axis}" if axis else "+axis")
              for k, v in DIRECTION_LABELS.items()}
    select_field(ctx.draft, f"physics.rotation.{zone}.direction", "Direction", list(labels),
                 labels, placeholder="Choose (never assumed)",
                 help="Check it against the real rotor: the 3D pane draws the arc.")


def render(ctx: state.Context) -> None:
    section_header(ctx, "rotation")
    if not isinstance(ctx.draft.get("physics"), dict):
        st.info("Set the fluid first (Fluid and turbulence).")
        return
    zones = rotating_zones(ctx.draft)
    if not zones:
        st.info("No rotating zone yet.")
        return
    kinds = {zone["name"]: speed_kind(ctx, zone["name"]) for zone in zones}
    measured = {zone["name"]: ctx.service.measured_tip_radius(ctx.draft, zone["name"])
                for zone in zones if kinds[zone["name"]] == "TIP_SPEED_RATIO"}
    with apply_form("rotation"):
        for zone in zones:
            name, axis = zone["name"], zone.get("axis")
            path = f"physics.rotation.{name}"
            st.markdown(f"**{name}** (axis +{axis})" if axis else f"**{name}**")
            if kinds[name] == "RPM":
                float_field(ctx.draft, f"{path}.speed.rpm", "Rotation speed (rpm)",
                            min_value=0.0)
            elif kinds[name] == "TIP_SPEED_RATIO":
                float_field(ctx.draft, f"{path}.speed.tip_speed_ratio", "Tip-speed ratio λ",
                            min_value=0.0)
                float_field(ctx.draft, f"{path}.speed.radius", "Tip radius R (m)",
                            min_value=0.0)
                found = measured.get(name)
                if found:
                    st.caption(f"Measured: {found.radius:.4g} m {found.source}, the "
                               f"{SWEPT_RADIUS}. {TSR_RADIUS_NOTE}")
                else:
                    st.caption("Measured: not available yet (no rotor geometry to measure).")
                float_field(ctx.draft, f"{path}.speed.free_stream_speed",
                            "Free-stream speed U (m/s; empty: the velocity inlet's)",
                            min_value=0.0, help="ω = λ U / R.")
            direction_field(ctx, name, axis)
            complete_kind(ctx, name, kinds[name])
    for name, found in measured.items():
        if found is not None and st.button(f"Use measured R for {name}: {SWEPT_RADIUS}, "
                                           f"{found.radius:.4g} m",
                                           key=f"machines_use_measured_{name}"):
            set_path(ctx.draft, f"physics.rotation.{name}.speed.radius", found.radius)
            state.bump_generation()
            st.rerun()
    for zone in zones:
        text = omega_text(ctx, zone["name"])
        if text:
            st.caption(f"{zone['name']}: {text}")
    render_issues(ctx.service.tip_radius_issues(ctx.draft))
    section_issues(ctx, "rotation")
