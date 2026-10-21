"""Bodies: the solid surfaces (rotor, blades, pole, hub). For a machine with a
rotor preset (HAWT, VAWT) this is the Rotor page: upload, measure, then the
preset drafts the zone, domain and patches. Custom: any number of bodies."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.upload_notes import STL_UPLOAD_NOTE
from app.components.widgets import get_path, render_issues
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
    new_stl,
    select_field,
    stl_fields,
    text_field,
)
from machines.config import Motion
from machines.profiles import PresetKind
from vawt.config import Axis

ROTOR = "machines_rotor_inputs"  # the preset's inputs before the draft has a rotor
MOTION_LABELS = {"STATIONARY": "Stationary", "ROTATING": "Rotating",
                 "SPLIT": "Split (the part inside its zone rotates)"}


def _rotor_inputs(ctx: state.Context) -> dict[str, Any]:
    inputs: dict[str, Any] = st.session_state.setdefault(ROTOR, {})
    bodies = ctx.draft.get("bodies") or []
    if not inputs.get("source") and bodies:
        inputs["source"] = dict(bodies[0]["source"])
    zones = ctx.draft.get("rotating_zones") or []
    if not inputs.get("axis") and zones and zones[0].get("axis"):
        inputs["axis"] = zones[0]["axis"]
    if not inputs.get("flow_axis") and ctx.draft.get("flow_axis"):
        inputs["flow_axis"] = ctx.draft["flow_axis"]
    return inputs


def preset_box(ctx: state.Context) -> None:
    """Upload, measure and draft (HAWT, VAWT)."""
    rotor = ctx.term("body", "rotor")
    inputs = _rotor_inputs(ctx)
    upload = st.file_uploader(f"{rotor.capitalize()} STL (copied into the project's inputs/)",
                              type=["stl"], key="machines_rotor_upload")
    st.caption(STL_UPLOAD_NOTE)
    stored = store_upload(ctx, upload, "machines_rotor_uploaded")
    if stored is not None:
        inputs["source"] = {**new_stl(stored), **{k: v for k, v in (inputs.get("source") or {})
                                                  .items() if k != "source_path"}}
        inputs["source"]["source_path"] = stored
        state.bump_generation()
        st.rerun()
    if not inputs.get("source"):
        st.info(f"Upload the {rotor} STL to start.")
        return
    vawt = ctx.profile.preset is PresetKind.VAWT
    with apply_form("rotor_inputs"):
        stl_fields(inputs, "source")
        axis_field(inputs, "axis", f"{rotor.capitalize()} axis (rotation axis)",
                   help="The axis the rotor turns about; a suggestion is shown, never applied.")
        if vawt:
            axis_field(inputs, "flow_axis", "Flow axis (wind from its minimum to its maximum)")
        else:
            st.caption("Flow runs along the rotor axis, from its minimum (inlet) to its "
                       "maximum (outlet): orient the rotor so the wind arrives from the "
                       "axis minimum.")
    source = inputs["source"]
    if not source.get("source_units"):
        return
    axis = Axis(inputs["axis"]) if inputs.get("axis") else None
    info, issues = ctx.service.rotor_info(source, axis)
    if info is None:
        render_issues(issues)
        return
    metrics = info.metrics
    st.write({
        "Suggested axis": metrics.suggested_axis.value if metrics.suggested_axis else "none",
        "Note": metrics.suggestion_note,
        "Extents (m)": [round(e, 6) for e in metrics.extents],
        f"Swept diameter about +{axis.value} (m)" if axis else "Swept diameter":
            round(info.diameter, 6) if info.diameter else "confirm the axis first",
    })
    ready = axis is not None and (not vawt or bool(inputs.get("flow_axis")))
    replaces = bool(ctx.draft.get("rotating_zones"))
    label = f"{'Redo' if replaces else 'Apply'} the {ctx.profile.label} preset"
    if replaces:
        st.caption("The preset replaces the zone, domain, patches and layers of the draft; "
                   "physics settings are kept.")
    if st.button(label, key="machines_apply_preset", type="primary", disabled=not ready):
        base = {"project_name": ctx.draft.get("project_name") or ctx.service.root.name,
                "source": source, "axis": inputs["axis"]}
        if vawt:
            base["flow_axis"] = inputs["flow_axis"]
        result = ctx.service.draft_from_preset(ctx.profile, base)
        if result.draft is not None:
            if ctx.draft.get("physics"):
                result.draft["physics"] = ctx.draft["physics"]
            state.replace_draft(result.draft)
            state.add_notes(result.notes)
        state.add_messages(result.issues)
        st.rerun()


def body_editor(ctx: state.Context, index: int, *, fixed: bool) -> None:
    """One body's settings; fixed: name and motion come from the preset."""
    path = f"bodies.{index}"
    zones = [z.get("name") for z in ctx.draft.get("rotating_zones") or [] if z.get("name")]
    upload = st.file_uploader("Replace the STL", type=["stl"], key=f"machines_body_up_{index}")
    st.caption(STL_UPLOAD_NOTE)
    stored = store_upload(ctx, upload, f"machines_body_uploaded_{index}")
    if stored is not None:
        ctx.draft["bodies"][index]["source"]["source_path"] = stored
        state.bump_generation()
        st.rerun()
    with apply_form(f"body_{index}"):
        if fixed:
            st.caption(f"Name `{get_path(ctx.draft, f'{path}.name')}` · motion "
                       f"{MOTION_LABELS.get(str(get_path(ctx.draft, f'{path}.motion')), '—')}")
        else:
            text_field(ctx.draft, f"{path}.name", "Body name (also its patch's source)")
            select_field(ctx.draft, f"{path}.motion", "Motion", [m.value for m in Motion],
                         MOTION_LABELS, placeholder="Choose (never assumed)")
            if get_path(ctx.draft, f"{path}.motion") not in (None, "STATIONARY"):
                select_field(ctx.draft, f"{path}.zone", "Its rotating zone", zones,
                             placeholder="Choose the zone" if zones else "Add a zone first")
            else:
                ctx.draft["bodies"][index]["zone"] = None
        stl_fields(ctx.draft, f"{path}.source")


def add_body(ctx: state.Context) -> None:
    with st.expander("Add a body"):
        upload = st.file_uploader("Body STL", type=["stl"], key="machines_new_body_up")
        st.caption(STL_UPLOAD_NOTE)
        name = st.text_input("Name", key="machines_new_body_name")
        stored = st.session_state.get("machines_new_body_path")
        path = store_upload(ctx, upload, "machines_new_body_uploaded")
        if path is not None:
            st.session_state["machines_new_body_path"] = path
            stored = path
        if st.button("Add body", key="machines_add_body", disabled=not (stored and name)):
            list_at(ctx.draft, "bodies").append({"name": name, "source": new_stl(str(stored))})
            st.session_state.pop("machines_new_body_path", None)
            state.bump_generation()
            st.rerun()


def render(ctx: state.Context) -> None:
    section_header(ctx, "bodies")
    preset = ctx.profile.preset in (PresetKind.HAWT, PresetKind.VAWT)
    if preset:
        preset_box(ctx)
        bodies = ctx.draft.get("bodies") or []
        if bodies:
            st.markdown(f"**The {ctx.term('body', 'rotor')} in the draft**")
            body_editor(ctx, 0, fixed=True)
    else:
        bodies = list_at(ctx.draft, "bodies")
        if not bodies:
            st.info("No bodies yet. A machine whose rotating walls are regions of imported "
                    "surfaces needs none.")
        for index, body in enumerate(bodies):
            with st.expander(f"{body.get('name') or f'Body {index + 1}'}", expanded=True):
                body_editor(ctx, index, fixed=False)
                if st.button("Remove this body", key=f"machines_remove_body_{index}"):
                    bodies.pop(index)
                    state.bump_generation()
                    st.rerun()
        add_body(ctx)
    section_issues(ctx, "bodies")
