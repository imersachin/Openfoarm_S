"""Rotating zones: a cylinder (with an optional hole for a pole) or an imported
closed surface; axis, cell size, mesh point, interface refinement. Custom adds
and removes zones; a preset machine has its one zone."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.upload_notes import STL_UPLOAD_NOTE
from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import (
    apply_form,
    list_at,
    section_header,
    section_issues,
    store_upload,
)
from app.machines_ui.sections.fields import (
    axis_field,
    float_field,
    int_field,
    new_stl,
    stl_fields,
    text_field,
    vector_field,
)
from machines.profiles import PresetKind


def new_cylinder_zone(name: str) -> dict[str, Any]:
    return {"name": name, "shape": {"kind": "CYLINDER"}, "interface_level": 1}


def zone_editor(ctx: state.Context, index: int) -> None:
    path = f"rotating_zones.{index}"
    kind = get_path(ctx.draft, f"{path}.shape.kind")
    if kind == "IMPORTED":
        upload = st.file_uploader("Replace the zone surface STL", type=["stl"],
                                  key=f"machines_zone_up_{index}")
        st.caption(STL_UPLOAD_NOTE)
        stored = store_upload(ctx, upload, f"machines_zone_uploaded_{index}", "zones")
        if stored is not None:
            ctx.draft["rotating_zones"][index]["shape"]["files"] = [new_stl(stored)]
            state.bump_generation()
            st.rerun()
    with apply_form(f"zone_{index}"):
        text_field(ctx.draft, f"{path}.name", "Zone name")
        axis_field(ctx.draft, f"{path}.axis", "Rotation axis")
        if kind == "CYLINDER":
            st.markdown("**Cylinder** (centre in the plane normal to the axis)")
            a, b = st.columns(2)
            with a:
                float_field(ctx.draft, f"{path}.shape.centre_u", "Centre u (m)")
                float_field(ctx.draft, f"{path}.shape.axis_min", "Axis min (m)")
                float_field(ctx.draft, f"{path}.shape.diameter", "Diameter (m)", min_value=0.0)
            with b:
                float_field(ctx.draft, f"{path}.shape.centre_v", "Centre v (m)")
                float_field(ctx.draft, f"{path}.shape.axis_max", "Axis max (m)")
                float_field(ctx.draft, f"{path}.shape.hole_diameter",
                            "Hole diameter (m; empty: no hole)", min_value=0.0,
                            help="An annular zone: a stationary pole passes through the hole.")
        elif kind == "IMPORTED":
            files = get_path(ctx.draft, f"{path}.shape.files") or []
            for f_index in range(len(files)):
                stl_fields(ctx.draft, f"{path}.shape.files.{f_index}")
            vector_field(ctx.draft, f"{path}.shape.origin", "A point on the rotation axis", "m")
        float_field(ctx.draft, f"{path}.cell_size", "Cell size (m)", min_value=0.0)
        vector_field(ctx.draft, f"{path}.location_in_mesh",
                     "Mesh point (inside the zone, outside every body)", "m")
        int_field(ctx.draft, f"{path}.interface_level", "Interface refinement level",
                  min_value=0, max_value=10)


def add_zone(ctx: state.Context) -> None:
    zones = list_at(ctx.draft, "rotating_zones")
    with st.expander("Add a rotating zone"):
        name = st.text_input("Name", key="machines_new_zone_name")
        cylinder, imported = st.columns(2)
        if cylinder.button("Add a cylinder zone", key="machines_add_cyl", disabled=not name):
            zones.append(new_cylinder_zone(name))
            state.bump_generation()
            st.rerun()
        upload = imported.file_uploader("…or an imported zone surface (STL)", type=["stl"],
                                        key="machines_new_zone_up")
        imported.caption(STL_UPLOAD_NOTE)
        stored = store_upload(ctx, upload, "machines_new_zone_uploaded", "zones")
        if stored is not None and name:
            zones.append({"name": name, "shape": {"kind": "IMPORTED", "format": "NAMED_REGIONS",
                                                  "files": [new_stl(stored)]},
                          "interface_level": 1})
            state.bump_generation()
            st.rerun()


def render(ctx: state.Context) -> None:
    section_header(ctx, "zones")
    zones = list_at(ctx.draft, "rotating_zones")
    custom = ctx.profile.preset is PresetKind.NONE
    if not zones:
        st.info("No rotating zone yet." + ("" if custom else
                                           " The preset in the first section adds it."))
    for index, zone in enumerate(zones):
        with st.expander(zone.get("name") or f"Zone {index + 1}", expanded=True):
            zone_editor(ctx, index)
            if custom and st.button("Remove this zone", key=f"machines_remove_zone_{index}"):
                zones.pop(index)
                state.bump_generation()
                st.rerun()
    if custom:
        add_zone(ctx)
    section_issues(ctx, "zones")
