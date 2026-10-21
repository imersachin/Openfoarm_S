"""Domain: a generated box or cylinder, or imported parts (each a closed
surface, its regions named in the file or one file per patch), and the joints
between imported surfaces (each meshed as a cyclicAMI pair)."""

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
    select_field,
    stl_fields,
    text_field,
    vector_field,
)
from machines.config import DomainKind, MachineType
from machines.presets import DOMAIN_CHOICES

KIND_LABELS = {"BOX": "Box", "CYLINDER": "Cylinder", "IMPORTED": "Imported parts"}
FORMAT_LABELS = {"NAMED_REGIONS": "One STL, patches as named regions (solid <name>)",
                 "ONE_FILE_PER_PATCH": "One STL per patch (the file name is the patch)"}


def _allowed(ctx: state.Context) -> list[str]:
    try:
        choice = DOMAIN_CHOICES[MachineType(str(ctx.draft.get("machine")))]
    except (KeyError, ValueError):
        return [k.value for k in DomainKind]
    return [k.value for k in DomainKind if k in choice.allowed]


def box_fields(ctx: state.Context) -> None:
    vector_field(ctx.draft, "domain.bounds.minimum", "Minimum corner", "m")
    vector_field(ctx.draft, "domain.bounds.maximum", "Maximum corner", "m")


def cylinder_fields(ctx: state.Context) -> None:
    axis_field(ctx.draft, "domain.axis", "Cylinder axis")
    a, b = st.columns(2)
    with a:
        float_field(ctx.draft, "domain.centre_u", "Centre u (m)")
        float_field(ctx.draft, "domain.axis_min", "Axis min (m)")
        float_field(ctx.draft, "domain.diameter", "Diameter (m)", min_value=0.0)
    with b:
        float_field(ctx.draft, "domain.centre_v", "Centre v (m)")
        float_field(ctx.draft, "domain.axis_max", "Axis max (m)")
        int_field(ctx.draft, "domain.segments", "Facets around", min_value=24, max_value=4096)


def part_editor(ctx: state.Context, index: int) -> None:
    path = f"domain.parts.{index}"
    name = get_path(ctx.draft, f"{path}.name") or f"part{index + 1}"
    many = get_path(ctx.draft, f"{path}.format") == "ONE_FILE_PER_PATCH"
    upload = st.file_uploader("Replace the file(s)", type=["stl"], accept_multiple_files=many,
                              key=f"machines_part_up_{index}")
    st.caption(STL_UPLOAD_NOTE)
    uploads = upload if isinstance(upload, list) else [upload] if upload else []
    stored = [p for i, u in enumerate(uploads)
              if (p := store_upload(ctx, u, f"machines_part_uploaded_{index}_{i}",
                                    f"part_{name}")) is not None]
    if stored:
        ctx.draft["domain"]["parts"][index]["files"] = [new_stl(p) for p in stored]
        state.bump_generation()
        st.rerun()
    with apply_form(f"part_{index}"):
        text_field(ctx.draft, f"{path}.name", "Part name")
        select_field(ctx.draft, f"{path}.format", "Region names", list(FORMAT_LABELS),
                     FORMAT_LABELS)
        for f_index in range(len(get_path(ctx.draft, f"{path}.files") or [])):
            stl_fields(ctx.draft, f"{path}.files.{f_index}")
        float_field(ctx.draft, f"{path}.cell_size", "Cell size (m)", min_value=0.0)
        vector_field(ctx.draft, f"{path}.location_in_mesh", "Mesh point (inside the part)", "m")


def imported_fields(ctx: state.Context) -> None:
    parts = ctx.draft["domain"].setdefault("parts", [])
    for index, part in enumerate(parts):
        with st.expander(part.get("name") or f"Part {index + 1}", expanded=True):
            part_editor(ctx, index)
            if st.button("Remove this part", key=f"machines_remove_part_{index}"):
                parts.pop(index)
                state.bump_generation()
                st.rerun()
    with st.expander("Add a part"):
        name = st.text_input("Part name", key="machines_new_part_name")
        upload = st.file_uploader("Part STL (patches as named regions)", type=["stl"],
                                  key="machines_new_part_up")
        st.caption(STL_UPLOAD_NOTE)
        stored = store_upload(ctx, upload, "machines_new_part_uploaded", f"part_{name}")
        if stored is not None and name:
            parts.append({"name": name, "format": "NAMED_REGIONS", "files": [new_stl(stored)]})
            state.bump_generation()
            st.rerun()
    joints_editor(ctx)


def joints_editor(ctx: state.Context, key: str = "domain") -> None:
    """The joints between imported surfaces: proposed from coincident regions,
    confirmed by keeping them here."""
    st.markdown("**Joints** (coincident regions of two imported surfaces; each becomes a "
                "cyclicAMI pair)")
    joints = list_at(ctx.draft, "joints")
    rows = [{"first": j.get("first"), "second": j.get("second"), "level": j.get("level", 1)}
            for j in joints]
    generation = st.session_state.get("draft_generation", 0)
    edited = st.data_editor(rows, num_rows="dynamic", width="stretch",
                            key=f"machines_joints_{key}_{generation}",
                            column_config={"level": st.column_config.NumberColumn(
                                "level", min_value=0, max_value=10, step=1)})
    if st.button("Apply joints", key=f"machines_apply_joints_{key}"):
        ctx.draft["joints"] = [{"first": r["first"], "second": r["second"],
                                "level": int(r.get("level") or 1)}
                               for r in edited if r.get("first") and r.get("second")]
        state.bump_generation()
        st.rerun()
    if st.button("Propose joints from coincident regions", key=f"machines_propose_{key}"):
        found = ctx.service.propose_joints(ctx.draft)
        st.session_state[f"machines_proposed_{key}"] = found
    proposed = st.session_state.get(f"machines_proposed_{key}")
    if proposed is not None:
        if not proposed:
            st.caption("No coincident regions were found.")
        else:
            st.dataframe([{"first": a, "second": b, **{k: round(v, 6) for k, v in m.items()}}
                          for a, b, m in proposed], hide_index=True, width="stretch")
            if st.button("Use these joints", key=f"machines_use_proposed_{key}"):
                ctx.draft["joints"] = [{"first": a, "second": b, "level": 1}
                                       for a, b, _ in proposed]
                st.session_state.pop(f"machines_proposed_{key}", None)
                state.bump_generation()
                st.rerun()


def render(ctx: state.Context) -> None:
    section_header(ctx, "domain")
    allowed = _allowed(ctx)
    current = get_path(ctx.draft, "domain.kind")
    index = allowed.index(current) if current in allowed else None
    kind: str | None = st.selectbox(
        "Domain", allowed, index=index, format_func=lambda k: KIND_LABELS.get(k, k),
        placeholder="Choose the domain", key="machines_domain_kind")
    if kind is not None and kind != current:
        if st.button(f"Use a {KIND_LABELS[kind].lower()} domain", key="machines_set_domain"):
            template: dict[str, Any] = {"kind": kind}
            if kind == "IMPORTED":
                template["parts"] = []
            ctx.draft["domain"] = template
            state.bump_generation()
            st.rerun()
        st.caption("Changing the kind replaces the domain's settings.")
    if current is None:
        section_issues(ctx, "domain")
        return
    if current == "IMPORTED":
        imported_fields(ctx)
    else:
        with apply_form("domain"):
            if current == "BOX":
                box_fields(ctx)
            else:
                cylinder_fields(ctx)
            float_field(ctx.draft, "domain.cell_size", "Cell size (m)", min_value=0.0)
            vector_field(ctx.draft, "domain.location_in_mesh",
                         "Mesh point (inside the domain, outside every zone and body)", "m")
    section_issues(ctx, "domain")
