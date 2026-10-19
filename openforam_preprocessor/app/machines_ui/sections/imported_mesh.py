"""Imported mesh (M1c): a Fluent or Gmsh .msh file meshed elsewhere.

The file (an upload, or a path inside WSL for a larger file), what was
detected (format, converter, the OpenFOAM version that converts it), the
bounding box and the units confirmed against it, the rotating cell zone, the
AMI pair, and the names the mesh will use. Suggestions are applied only by a
button; nothing is converted except by the box button or a run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, list_at, section_header, section_issues
from app.machines_ui.sections.fields import (
    UNIT_LABELS,
    axis_field,
    select_field,
    text_field,
    vector_field,
)
from core.config.models import LengthUnit
from machines.msh_import.cases import converter, needs_resave
from machines.msh_import.inspection import MeshInspection

UPLOADED = "machines_mesh_uploaded"  # the file_id of the last stored upload


def mesh_of(draft: dict[str, Any]) -> dict[str, Any]:
    mesh = draft.get("imported_mesh")
    if not isinstance(mesh, dict):
        mesh = {}
        draft["imported_mesh"] = mesh
    return mesh


def choose(ctx: state.Context, path: str, sha256: str | None) -> None:
    """Use this file: its path and content hash (a changed file is found later),
    and a rotating zone to fill in."""
    mesh = mesh_of(ctx.draft)
    mesh["source_path"] = path
    if not mesh.get("zones"):
        mesh["zones"] = [{}]
    if sha256:
        mesh["sha256"] = sha256
    else:
        mesh.pop("sha256", None)
    state.bump_generation()


def file_choice(ctx: state.Context) -> None:
    upload = st.file_uploader("Upload a .msh file (Fluent or Gmsh; the format is read from "
                              "the content, not the name)", key="machines_mesh_upload")
    if upload is not None and st.session_state.get(UPLOADED) != upload.file_id:
        try:
            stored = ctx.service.store_mesh_file(upload.name, upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[UPLOADED] = upload.file_id
            choose(ctx, str(stored), ctx.service.inspect_mesh(stored).sha256)
            st.rerun()
    with st.expander("Or give its path inside WSL (a large file is read where it is)"):
        given = st.text_input("Mesh file path", key="machines_mesh_path")
        if st.button("Use this file", key="machines_mesh_use_path", disabled=not given):
            path = Path(given).expanduser()
            choose(ctx, str(path), ctx.service.inspect_mesh(path).sha256)
            st.rerun()


def detected(ctx: state.Context, inspection: MeshInspection) -> None:
    info = inspection.info
    rows: dict[str, Any] = {"File": str(info.path), "Size": f"{info.size:,} bytes"}
    if info.format is not None:
        kind = "binary" if info.binary else "ASCII"
        rows["Format"] = f"{info.format.value} {info.version or ''} ({kind})".replace("  ", " ")
        rows["Converter"] = converter(info) + (
            " (after re-saving as MSH 2.2 ASCII with gmsh)" if needs_resave(info) else "")
    rows["OpenFOAM"] = ctx.service.environment().openfoam_version or "not sourced"
    rows["Patches"] = [f"{n} ({info.patch_kinds[n]})" if n in info.patch_kinds else n
                       for n in info.patches] or "none found"
    rows["Cell zones"] = list(info.cell_zones) or "none found"
    if info.declared_cells is not None:
        rows["Cells (declared by the file)"] = f"{info.declared_cells:,}"
    elif info.nodes:
        rows["Nodes"] = f"{info.nodes:,}"
    st.write(rows)


def box_view(ctx: state.Context, inspection: MeshInspection) -> None:
    """The box in the file's units, its size in metres for each unit, and the
    units confirmed against it (never assumed)."""
    box = inspection.box
    if box is None:
        st.info("The box cannot be read from this file directly. Convert it once (at scale "
                "1, not a verified mesh; a run reuses the conversion).")
        if st.button("Convert to show the box", key="machines_mesh_box",
                     disabled=ctx.active or not inspection.info.readable):
            result = ctx.service.convert_for_box(inspection.info.path)
            state.add_messages(result.issues)
            st.rerun()
    else:
        low, high = box
        size = [h - lo for lo, h in zip(low, high, strict=True)]
        st.markdown(f"**Bounding box** ({inspection.box_source}), in the file's units: "
                    f"min ({', '.join(f'{v:g}' for v in low)}), "
                    f"max ({', '.join(f'{v:g}' for v in high)})")
        st.dataframe([{"units": UNIT_LABELS[u.value],
                       "size in m": " x ".join(f"{s * u.to_metres:.4g}" for s in size)}
                      for u in LengthUnit], hide_index=True, width="stretch")
    with apply_form("imported_units"):
        select_field(ctx.draft, "imported_mesh.source_units", "Units of the mesh file",
                     [u.value for u in LengthUnit], UNIT_LABELS,
                     placeholder="Choose the units (never assumed)",
                     help="Check the size in metres above against the real machine.")


def zone_view(ctx: state.Context, inspection: MeshInspection) -> None:
    mesh = mesh_of(ctx.draft)
    zones = list_at(mesh, "zones") if isinstance(mesh.get("zones"), list) else []
    st.markdown("**Rotating zone** (one cell zone of the mesh)")
    if not zones:
        if st.button("Add the rotating zone", key="machines_mesh_add_zone"):
            list_at(mesh, "zones").append({})
            state.bump_generation()
            st.rerun()
        return
    if not inspection.info.cell_zones:
        st.warning("The file has no cell zone to rotate (see the issues below).")
    with apply_form("imported_zone"):
        text_field(ctx.draft, "imported_mesh.zones.0.name", "Zone name (used in the physics)",
                   help="Letters, digits and '_'; e.g. rotor or runner.")
        select_field(ctx.draft, "imported_mesh.zones.0.cell_zone", "Cell zone that rotates",
                     list(inspection.info.cell_zones), placeholder="Choose the cell zone")
        axis_field(ctx.draft, "imported_mesh.zones.0.axis", "Rotation axis")
        vector_field(ctx.draft, "imported_mesh.zones.0.origin", "A point on the axis", "m")
    name = zones[0].get("name")
    radius = ctx.service.zone_radius(ctx.draft, str(name)) if name else None
    st.caption(f"Zone radius {radius:.4g} m (computed from the zone)" if radius
               else "Zone radius: computed from the zone once the mesh is imported.")


def pairs_view(ctx: state.Context, inspection: MeshInspection) -> None:
    mesh = mesh_of(ctx.draft)
    pairs = mesh["ami_pairs"] if isinstance(mesh.get("ami_pairs"), list) else []
    st.markdown("**AMI pair** (the two sides of the rotating zone's interface; they keep "
                "their names)")
    for index, pair in enumerate(list(pairs)):
        left, right = st.columns([4, 1])
        left.markdown(f"`{pair.get('first')}` / `{pair.get('second')}`")
        if right.button("Remove", key=f"machines_ami_remove_{index}"):
            pairs.pop(index)
            state.bump_generation()
            st.rerun()
    listed = {(p.get("first"), p.get("second")) for p in pairs}
    offered = [s for s in inspection.suggested_pairs if (s[0], s[1]) not in listed]
    sha = inspection.sha256
    for first, second, why in offered:
        geometry = ctx.service.mesh_patch_geometry((first, second), sha) if sha else {}
        evidence = "; ".join(
            f"{g.name}: {g.faces:,} faces, area {g.area:.4g}, centre "
            f"({', '.join(f'{c:.3g}' for c in g.centroid)})" for g in geometry.values())
        st.caption(f"Suggested: `{first}` / `{second}` ({why})"
                   + (f". {evidence}" if evidence else
                      ". Convert to show the box to see their areas and centres."))
        if st.button(f"Use {first} / {second}", key=f"machines_ami_use_{first}_{second}"):
            list_at(mesh, "ami_pairs").append({"first": first, "second": second})
            state.bump_generation()
            st.rerun()
    patches = list(inspection.info.patches)
    first_col, second_col, add = st.columns([2, 2, 1])
    side_a = first_col.selectbox("First side", patches, index=None, key="machines_ami_first")
    side_b = second_col.selectbox("Second side", patches, index=None,
                                  key="machines_ami_second")
    if add.button("Add pair", key="machines_ami_add",
                  disabled=not (side_a and side_b and side_a != side_b)):
        list_at(mesh, "ami_pairs").append({"first": side_a, "second": side_b})
        state.bump_generation()
        st.rerun()


def names_used(draft: dict[str, Any], inspection: MeshInspection) -> list[dict[str, str]]:
    """Original name -> name used, for every patch of the file."""
    mesh = mesh_of(draft)
    ami = {n for p in mesh.get("ami_pairs") or [] for n in (p.get("first"), p.get("second"))}
    renamed = {(p.get("source") or {}).get("ref"): p.get("name")
               for p in draft.get("patches") or []
               if (p.get("source") or {}).get("kind") == "MESH_PATCH"}
    rows = []
    for name in inspection.info.patches:
        if name in ami:
            rows.append({"original name": name, "name used": name, "as": "AMI (cyclicAMI)"})
        elif name in renamed:
            used = str(renamed[name])
            rows.append({"original name": name, "name used": used,
                         "as": "renamed" if used != name else "typed"})
        else:
            rows.append({"original name": name, "name used": "—", "as": "no type yet"})
    return rows


def render(ctx: state.Context) -> None:
    section_header(ctx, "imported_mesh")
    file_choice(ctx)
    path = mesh_of(ctx.draft).get("source_path")
    if not path:
        st.info("Choose the .msh file first.")
        section_issues(ctx, "imported_mesh")
        return
    inspection = ctx.service.inspect_mesh(Path(str(path)))
    chosen = mesh_of(ctx.draft).get("sha256")
    if inspection.sha256 and chosen and inspection.sha256 != chosen:
        st.warning("The file has changed since it was chosen. Check the box, zones and "
                   "patches again, then use it as it is now.")
        if st.button("Use the file as it is now", key="machines_mesh_rehash"):
            choose(ctx, str(path), inspection.sha256)
            st.rerun()
    detected(ctx, inspection)
    # The file's own findings, also while the rest of the draft is incomplete
    # (validation reads the file only for a complete configuration).
    render_issues(inspection.issues)
    if inspection.info.readable:
        box_view(ctx, inspection)
        zone_view(ctx, inspection)
        pairs_view(ctx, inspection)
        st.markdown("**Names in the mesh** (original name -> name used)")
        st.dataframe(names_used(ctx.draft, inspection), hide_index=True, width="stretch")
    shown = {(i.code, i.message) for i in inspection.issues}
    status = ctx.analysis.sections.get("imported_mesh")
    render_issues([i for i in (status.issues if status else ())
                   if (i.code, i.message) not in shown])
