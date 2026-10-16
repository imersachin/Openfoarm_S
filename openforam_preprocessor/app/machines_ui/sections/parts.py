"""Parts (Francis): the water passage from named slots, one STL each with its
patches as named regions (decision Q1): spiral casing, stay vanes (optional),
guide vanes, runner and draft tube. With the rotation axis, a point on it and
the runner outlet diameter D, the preset drafts the parts, the runner zone,
the mesh points and the joints between coincident regions."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.widgets import get_path
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, section_header, section_issues, store_upload
from app.machines_ui.sections.domain import joints_editor
from app.machines_ui.sections.fields import (
    axis_field,
    float_field,
    int_field,
    new_stl,
    stl_fields,
    vector_field,
)
from app.machines_ui.sections.patches import SUGGESTED

SLOTS = "machines_slot_inputs"  # {"slots": {key: StlSource}, "axis", "origin", "diameter"}
EXTRAS = "machines_francis_extras"  # the last draft's joints and suggestions


def _inputs(ctx: state.Context) -> dict[str, Any]:
    """The preset's inputs, taken from the draft when it already has the parts."""
    inputs: dict[str, Any] = st.session_state.setdefault(SLOTS, {"slots": {}})
    slots = inputs["slots"]
    domain = ctx.draft.get("domain") or {}
    for part in domain.get("parts") or []:
        if part.get("name") and part.get("files") and part["name"] not in slots:
            slots[part["name"]] = dict(part["files"][0])
    zones = ctx.draft.get("rotating_zones") or []
    if zones:
        zone = zones[0]
        files = (zone.get("shape") or {}).get("files") or []
        if files and zone.get("name") and zone["name"] not in slots:
            slots[zone["name"]] = dict(files[0])
        inputs.setdefault("axis", zone.get("axis"))
        inputs.setdefault("origin", (zone.get("shape") or {}).get("origin"))
    inputs.setdefault("origin", {"x": 0.0, "y": 0.0, "z": 0.0})
    return inputs


def slot_uploads(ctx: state.Context, inputs: dict[str, Any]) -> None:
    for slot in ctx.profile.slots:
        label = slot.label + ("" if slot.required else " (optional)")
        current = inputs["slots"].get(slot.key)
        with st.expander(f"{label}: {'file set' if current else 'no file yet'}",
                         expanded=current is None and slot.required):
            if slot.help:
                st.caption(slot.help)
            upload = st.file_uploader(f"{slot.label} STL (its patches as named regions)",
                                      type=["stl"], key=f"machines_slot_up_{slot.key}")
            stored = store_upload(ctx, upload, f"machines_slot_uploaded_{slot.key}", slot.key)
            if stored is not None:
                inputs["slots"][slot.key] = {**new_stl(stored),
                                             **{k: v for k, v in (current or {}).items()
                                                if k != "source_path"}}
                inputs["slots"][slot.key]["source_path"] = stored
                state.bump_generation()
                st.rerun()
            if current is not None:
                with apply_form(f"slot_{slot.key}"):
                    stl_fields(inputs, f"slots.{slot.key}")
                if not slot.required and st.button(f"Leave out the {slot.label.lower()}",
                                                   key=f"machines_slot_drop_{slot.key}"):
                    inputs["slots"].pop(slot.key)
                    state.bump_generation()
                    st.rerun()


def draft_box(ctx: state.Context, inputs: dict[str, Any]) -> None:
    st.markdown("**Rotation axis and runner size**")
    with apply_form("francis_axis"):
        axis_field(inputs, "axis", "Runner rotation axis")
        vector_field(inputs, "origin", "A point on the rotation axis", "m")
        float_field(inputs, "diameter", "Runner outlet diameter D (m)", min_value=0.0,
                    help="Sets the cell size (D / 24, a G0 R4 test value). After a draft, the "
                    "runner regions' measured diameters are shown as a check.")
    missing = [s.label for s in ctx.profile.slots if s.required and s.key not in inputs["slots"]]
    units = [s.label for s in ctx.profile.slots
             if s.key in inputs["slots"] and not inputs["slots"][s.key].get("source_units")]
    reasons = ([f"No file for: {', '.join(missing)}."] if missing else []) + \
        ([f"Choose the units of: {', '.join(units)}."] if units else []) + \
        ([] if inputs.get("axis") else ["Choose the rotation axis."]) + \
        ([] if inputs.get("diameter") else ["Give the runner outlet diameter D."])
    replaces = bool(get_path(ctx.draft, "domain.parts"))
    if st.button(("Redo" if replaces else "Build") + " the water passage", type="primary",
                 key="machines_francis_draft", disabled=bool(reasons)):
        result = ctx.service.draft_from_preset(ctx.profile, {
            "project_name": ctx.draft.get("project_name") or ctx.service.root.name,
            "axis": inputs["axis"], "origin": inputs["origin"],
            "diameter": inputs["diameter"], "slots": inputs["slots"]})
        if result.draft is not None:
            if ctx.draft.get("physics"):
                result.draft["physics"] = ctx.draft["physics"]
            state.replace_draft(result.draft)
            state.add_notes(result.notes)
            st.session_state[EXTRAS] = {"joints": result.joints,
                                        "diameters": result.outlet_diameters,
                                        "types": result.suggested_types}
            # Shown beside the patch table; applied only by its button (E5).
            st.session_state.setdefault(SUGGESTED, {}).update(result.suggested_types)
        state.add_messages(result.issues)
        st.rerun()
    for reason in reasons:
        st.caption(f"To build: {reason}")
    if replaces:
        st.caption("Building again replaces the parts, the runner zone, the patches and the "
                   "joints; physics settings are kept.")


def drafted(ctx: state.Context) -> None:
    extras = st.session_state.get(EXTRAS) or {}
    if extras.get("diameters"):
        st.caption("Measured runner region diameters (a check on D): " + ", ".join(
            f"{name} {d:.4g} m" for name, d in extras["diameters"].items()))
    if extras.get("joints"):
        st.markdown("**Proposed joints, with their measurements** (kept below; remove any "
                    "that is wrong)")
        st.dataframe([{"first": a, "second": b, **{k: round(v, 6) for k, v in m.items()}}
                      for a, b, m in extras["joints"]], hide_index=True,
                     width="stretch")
    parts = get_path(ctx.draft, "domain.parts") or []
    with apply_form("francis_parts"):
        for index, part in enumerate(parts):
            st.markdown(f"**{part.get('name')}**")
            a, b = st.columns([1, 3])
            with a:
                float_field(ctx.draft, f"domain.parts.{index}.cell_size", "Cell size (m)",
                            min_value=0.0)
            with b:
                vector_field(ctx.draft, f"domain.parts.{index}.location_in_mesh",
                             "Mesh point", "m")
        if ctx.draft.get("rotating_zones"):
            st.markdown(f"**{ctx.term('zone', 'runner').capitalize()} (rotating zone)**")
            a, b = st.columns([1, 3])
            with a:
                float_field(ctx.draft, "rotating_zones.0.cell_size", "Cell size (m)",
                            min_value=0.0)
                int_field(ctx.draft, "rotating_zones.0.interface_level", "Interface level",
                          min_value=0, max_value=10)
            with b:
                vector_field(ctx.draft, "rotating_zones.0.location_in_mesh", "Mesh point", "m")
    joints_editor(ctx, "parts")


def render(ctx: state.Context) -> None:
    section_header(ctx, "parts")
    inputs = _inputs(ctx)
    slot_uploads(ctx, inputs)
    draft_box(ctx, inputs)
    if get_path(ctx.draft, "domain.parts"):
        drafted(ctx)
    section_issues(ctx, "parts")
