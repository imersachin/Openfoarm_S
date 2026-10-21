"""Imported mesh (M1c): a Fluent or Gmsh .msh file meshed elsewhere.

The file (an upload up to 200 MB, or a file picked on this computer: a
Windows drive or WSL, M2a; one on a Windows drive is copied into the project
first, with its progress shown and its hash checked), what was
detected (format, converter, the OpenFOAM version that converts it), the
bounding box and the units confirmed against it, the rotating cell zone, the
AMI pair, and the names the mesh will use. Suggestions are applied only by a
button; nothing is converted except by the box button or a run.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import streamlit as st

from app.components.upload_notes import MSH_UPLOAD_NOTE
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
from machines import disk
from machines.msh_import.cases import converter, needs_resave
from machines.msh_import.inspection import MeshInspection

UPLOADED = "machines_mesh_uploaded"  # the file_id of the last stored upload
FOLDER = "machines_disk_folder"  # the folder the picker shows
WAITING = "machines_disk_copy"  # the copy whose file is used when it is done


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
    st.caption(MSH_UPLOAD_NOTE)
    if upload is not None and st.session_state.get(UPLOADED) != upload.file_id:
        try:
            stored = ctx.service.store_mesh_file(upload.name, upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[UPLOADED] = upload.file_id
            choose(ctx, str(stored), ctx.service.inspect_mesh(stored).sha256)
            st.rerun()
    with st.expander("Or pick a file on this computer (Windows drives and WSL)",
                     expanded=not mesh_of(ctx.draft).get("source_path")):
        picker(ctx)
    copies(ctx)


def use_file(ctx: state.Context, path: Path) -> None:
    """Use a picked file: one on a Windows drive is copied into the project first
    (read in place it is 6.5 times slower, E0), any other is used where it is."""
    if disk.on_windows_drive(path):
        # Not started again when this same file is being copied: its progress shows.
        target, _ = ctx.service.start_mesh_copy(path)
        st.session_state[WAITING] = str(target)
        return
    choose(ctx, str(path), ctx.service.inspect_mesh(path).sha256)


def _size(size: int) -> str:
    return f"{size / 1e6:,.1f} MB" if size < 1e9 else f"{size / 1e9:,.2f} GB"


def picker(ctx: state.Context) -> None:
    roots = ctx.service.mesh_roots()
    remembered = ctx.service.mesh_folder()
    current = st.session_state.get(FOLDER) or remembered or str(roots[0].path)
    columns = st.columns(len(roots) + 1)
    for column, root in zip(columns, roots, strict=False):
        if column.button(root.label, key=f"machines_disk_root_{root.label}",
                         help=str(root.path)):
            st.session_state[FOLDER] = str(root.path)
            st.rerun()
    if remembered and columns[-1].button("My mesh folder", key="machines_disk_mine",
                                         help=remembered):
        st.session_state[FOLDER] = remembered
        st.rerun()
    typed = st.text_input("A folder or a .msh file", key="machines_disk_typed")
    st.caption(r"For example `E:\Meshes\Francis`, `\\wsl$\Ubuntu-24.04\root\meshes` or "
               "`/root/meshes`.")
    # Never disabled: a typed path is committed by the click itself (disabled until
    # Enter, the first click did nothing; M2a browser check).
    if st.button("Go", key="machines_disk_go") and not typed:
        st.warning("Type a folder or a .msh file first.")
    elif st.session_state.get("machines_disk_go") and typed:
        resolved = disk.to_wsl(typed)
        if resolved.path is not None and resolved.path.is_file():
            if disk.inside(resolved.path, roots) is None:
                st.error(f"{resolved.path} is outside the folders the app may read.")
            else:
                use_file(ctx, resolved.path)
                st.rerun()
        else:
            st.session_state[FOLDER] = str(resolved.path) if resolved.path else typed
            st.rerun()
    found = ctx.service.list_mesh_folder(current)
    if found.path is None:
        st.warning(found.message)
        return
    top, up, keep = st.columns([5, 1, 3])
    top.markdown(f"**{found.root.label if found.root else ''}** `{found.path}`")
    if found.root is not None and found.path != found.root.path and up.button(
            "Up", key="machines_disk_up"):
        st.session_state[FOLDER] = str(found.path.parent)
        st.rerun()
    if str(found.path) != remembered and keep.button(
            "Remember this folder", key="machines_disk_remember",
            help="Listed first whenever Imported mesh opens (an app setting)."):
        ctx.service.remember_mesh_folder(found.path)
        st.rerun()
    if found.folders:
        names = [f.name for f in found.folders]
        chosen = st.selectbox("Folders", names, index=None, key=f"machines_disk_sub_{found.path}",
                              placeholder=f"{len(names)} folder(s): open one")
        if chosen:
            st.session_state[FOLDER] = str(found.path / chosen)
            st.rerun()
    if not found.files:
        st.caption("No .msh file in this folder.")
        return
    st.caption(f"{len(found.files)} .msh file(s). The summary reads only the file's "
               "headers; using a file reads it fully.")
    for entry in found.files:
        summary = ctx.service.mesh_summary(entry.path)
        text, use = st.columns([6, 1])
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.modified))
        details = (f"{summary.format} ({'binary' if summary.binary else 'ASCII'})"
                   + (f", {summary.declared_cells:,} cells" if summary.declared_cells else "")
                   + f", {summary.cell_zones} cell zone(s), {summary.boundary_zones} boundary "
                   "zone(s)" + (f", **{summary.unnamed} without a name**" if summary.unnamed
                                else "")
                   if summary.format else summary.note)
        text.markdown(f"`{entry.name}` · {_size(entry.size)} · {when}  \n{details}")
        label = "Copy and use" if disk.on_windows_drive(entry.path) else "Use"
        if use.button(label, key=f"machines_disk_use_{entry.path}",
                      disabled=not summary.format):
            use_file(ctx, entry.path)
            st.rerun()


@st.fragment(run_every=1.0)
def copies(ctx: state.Context) -> None:
    """Copies into this project: progress while copying (also after the page is
    shown again), then the file is used, or the failure said."""
    for copy in ctx.service.mesh_copies():
        target = Path(copy["target"])
        name = target.name
        state_ = copy["state"]
        if state_ == "copying":
            done = copy["copied"] / copy["size"] if copy["size"] else 1.0
            left = copy.get("remaining_s")
            st.progress(min(done, 1.0), text=(
                f"Copying {name} into the project: {copy['copied'] / 1e6:,.0f} of "
                f"{copy['size'] / 1e6:,.0f} MB, {copy['rate'] / 1e6:,.0f} MB/s"
                + (f", about {left:,.0f} s left" if left is not None else "")
                + (f" ({copy['message']})" if copy.get("message") else "")))
            if st.button("Cancel the copy", key=f"machines_copy_cancel_{name}"):
                ctx.service.cancel_mesh_copy(target)
            continue
        if state_ == "done" and st.session_state.get(WAITING) == str(target):
            st.session_state.pop(WAITING, None)
            ctx.service.forget_mesh_copy(target)
            choose(ctx, str(target), copy["sha256"] or ctx.service.inspect_mesh(target).sha256)
            st.rerun(scope="app")
        if state_ == "done":
            # A copy finished while the page was away (refreshed): offer it.
            if mesh_of(ctx.draft).get("source_path") != str(target):
                text, use, drop = st.columns([5, 1, 1])
                text.caption(f"Copied into the project and checked: `{name}`.")
                if use.button("Use it", key=f"machines_copy_use_{name}"):
                    ctx.service.forget_mesh_copy(target)
                    choose(ctx, str(target), copy["sha256"] or None)
                    st.rerun(scope="app")
                if drop.button("Dismiss", key=f"machines_copy_drop_{name}"):
                    ctx.service.forget_mesh_copy(target)
                    st.rerun(scope="app")
            else:
                ctx.service.forget_mesh_copy(target)
            continue
        st.warning(f"{name}: {copy['message']}")
        if st.button("Dismiss", key=f"machines_copy_dismiss_{name}"):
            ctx.service.forget_mesh_copy(target)
            st.rerun(scope="app")


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
