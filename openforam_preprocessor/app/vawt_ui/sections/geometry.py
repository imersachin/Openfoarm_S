"""Geometry: upload, units, transform, validation issues, rotor metrics."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import (
    float_field,
    get_path,
    select_field,
    set_path,
    text_field,
    vector_field,
)
from app.vawt_ui import state
from app.vawt_ui.sections.common import apply_form, section_header, section_issues
from core.config.models import LengthUnit
from vawt.config import Axis

UNIT_LABELS = {
    "m": "metres (m)", "cm": "centimetres (cm)", "mm": "millimetres (mm)",
    "um": "micrometres (µm)", "in": "inches (in)", "ft": "feet (ft)",
}
UPLOADED = "vawt_uploaded_id"


def render(ctx: state.Context) -> None:
    section_header(ctx, "geometry", "Geometry")
    upload = st.file_uploader("Rotor STL (copied into the project's inputs/)", type=["stl"])
    if upload is not None and st.session_state.get(UPLOADED) != upload.file_id:
        try:
            stored = ctx.service.store_source_file(upload.name, upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[UPLOADED] = upload.file_id
            set_path(ctx.draft, "geometry.source_path", str(stored))
            state.bump_generation()
            st.rerun()
    source = get_path(ctx.draft, "geometry.source_path")
    st.markdown(f"**Source file:** `{source}`" if source else "**Source file:** none yet")

    with apply_form("geometry"):
        select_field(ctx.draft, "geometry.source_units", "Units of the STL file",
                     [u.value for u in LengthUnit], UNIT_LABELS,
                     placeholder="Choose the units (never assumed)")
        float_field(ctx.draft, "geometry.scale", "Scale", min_value=0.0)
        vector_field(ctx.draft, "geometry.rotation_deg", "Rotation", "degrees, X then Y then Z")
        vector_field(ctx.draft, "geometry.translation", "Translation", "m")
        text_field(ctx.draft, "geometry.patch_name", "Rotor patch name")
    section_issues(ctx, "geometry")

    if source and get_path(ctx.draft, "geometry.source_units"):
        axis = get_path(ctx.draft, "rotor.axis")
        metrics, _ = ctx.service.rotor_metrics(ctx.draft["geometry"],
                                               Axis(axis) if axis else None)
        if metrics is not None:
            st.markdown("**Rotor metrics** (after units and transform)")
            st.write({
                "Suggested rotor axis": metrics.suggested_axis.value
                if metrics.suggested_axis else "none",
                "Note": metrics.suggestion_note,
                "Extents (m)": [round(e, 6) for e in metrics.extents],
                "Diameter D (m)": metrics.diameter,
                "Span H (m)": metrics.span,
            })
