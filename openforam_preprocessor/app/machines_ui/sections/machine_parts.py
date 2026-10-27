"""Imported mesh, a machine of several .msh files (M2b; docs/m2b_assembly_notes.md).

The files (picked on this computer, a folder at once, or uploaded), each a
part with a role and a short prefix, its units confirmed against its box;
the machine axis; "Read the parts" (each converted once, at scale 1) and the
passage count found for each, confirmed by a button; which part rotates; the
AMI pairs; the names the mesh will use. Nothing is applied by itself.
"""

from __future__ import annotations

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
from machines.msh_import.part_views import (
    ROLES,
    PartView,
    confirmation,
    confirmed,
    suggest_role,
    summary,
)

CUSTOM = "custom"


def mesh_of(draft: dict[str, Any]) -> dict[str, Any]:
    mesh = draft.get("imported_mesh")
    assert isinstance(mesh, dict)
    return mesh


def is_parts(draft: dict[str, Any]) -> bool:
    mesh = draft.get("imported_mesh")
    return isinstance(mesh, dict) and "parts" in mesh


def add_part(ctx: state.Context, path: str, sha256: str | None) -> None:
    """A file as a new part (once): its role, prefix and units are the user's."""
    parts = list_at(mesh_of(ctx.draft), "parts")
    if any(p.get("source_path") == path for p in parts):
        return
    parts.append({"source_path": path, **({"sha256": sha256} if sha256 else {})})
    state.bump_generation()


def _size(size: int) -> str:
    return f"{size / 1e6:,.1f} MB" if size < 1e9 else f"{size / 1e9:,.2f} GB"


def role_view(ctx: state.Context, view: PartView) -> None:
    """The part's role (SC, GV, RU, DT or custom) and prefix, set by the user."""
    part = mesh_of(ctx.draft)["parts"][view.index]
    base = f"imported_mesh.parts.{view.index}"
    current = part.get("prefix")
    role = current if current in ROLES and part.get("name") == ROLES[current] else (
        CUSTOM if current else None)
    choices = [*ROLES, CUSTOM]
    labels = {**{k: f"{v} ({k})" for k, v in ROLES.items()}, CUSTOM: "Custom name and prefix"}
    hint = suggest_role(view.path)
    chosen = st.selectbox("Part", choices, index=choices.index(role) if role else None,
                          format_func=labels.__getitem__, placeholder="Choose the part",
                          key=f"machines_part_role_{view.index}",
                          help=f"The file name suggests: {ROLES[hint]} ({hint})." if hint
                          else None)
    if chosen != role and chosen in ROLES:
        part["name"], part["prefix"] = ROLES[chosen], chosen
        state.bump_generation()
        st.rerun()
    if chosen == CUSTOM or role == CUSTOM:
        with apply_form(f"part_name_{view.index}"):
            text_field(ctx.draft, f"{base}.name", "Part name", help="e.g. Stay vanes")
            text_field(ctx.draft, f"{base}.prefix", "Prefix (short name)",
                       help="1-8 letters and digits, starting with a letter; every patch "
                            "and cell zone of the part is used as <prefix>_<name>.")
    if hint and not current:
        st.caption(f"The file name suggests {ROLES[hint]} ({hint}); choose it above to use it.")


def units_view(ctx: state.Context, view: PartView) -> None:
    box = view.info.box
    if box is None:
        st.caption("The box cannot be read from this file directly; it is shown once the "
                   "part is read.")
    else:
        low, high = box
        size = [h - lo for lo, h in zip(low, high, strict=True)]
        st.caption("Box in the file's units: " + " x ".join(f"{s:g}" for s in size) + "; in m: "
                   + "; ".join(f"{UNIT_LABELS[u.value]} " + " x ".join(
                       f"{s * u.to_metres:.4g}" for s in size) for u in LengthUnit))
    with apply_form(f"part_units_{view.index}"):
        select_field(ctx.draft, f"imported_mesh.parts.{view.index}.source_units",
                     "Units of the file", [u.value for u in LengthUnit], UNIT_LABELS,
                     placeholder="Choose the units (never assumed)")


def passages_view(ctx: state.Context, view: PartView) -> None:
    """The passage count found, confirmed by a button (never applied by itself)."""
    part = mesh_of(ctx.draft)["parts"][view.index]
    found = view.periodicity
    st.markdown(f"**Passages:** {summary(view)}")
    if found is None or found.passages is None:
        if found is not None and found.problem:
            st.warning("The copies cannot be made: " + found.problem + ". Check the machine "
                       "axis and the file.")
        return
    if confirmed(view, part):
        st.caption("Confirmed." if found.passages > 1 else "Confirmed: a whole ring.")
        return
    label = (f"Confirm {found.passages} passages" if found.passages > 1
             else "Confirm: a whole ring")
    if st.button(label, key=f"machines_part_confirm_{view.index}"):
        part.update(confirmation(view) or {})
        state.bump_generation()
        st.rerun()


def part_card(ctx: state.Context, view: PartView) -> None:
    info = view.info
    heading = view.name or view.path.name
    with st.container(border=True):
        top, drop = st.columns([6, 1])
        details = (f"{info.format.value} ({'binary' if info.binary else 'ASCII'})"
                   + (f", {info.declared_cells:,} cells" if info.declared_cells else "")
                   + f", {len(info.patches)} patches, {len(info.cell_zones)} cell zone(s)"
                   if info.format else "not a mesh file this app reads")
        top.markdown(f"**{heading}**" + (f" (`{view.prefix}`)" if view.prefix else "")
                     + f"  \n`{view.path}` · {_size(info.size)} · {details}")
        if drop.button("Remove", key=f"machines_part_remove_{view.index}"):
            mesh_of(ctx.draft)["parts"].pop(view.index)
            state.bump_generation()
            st.rerun()
        render_issues(info.issues)
        role_view(ctx, view)
        if info.readable:
            units_view(ctx, view)
            passages_view(ctx, view)


def axis_view(ctx: state.Context) -> None:
    st.markdown("**Machine axis** (the parts are copied around it, and the rotating part "
                "turns about it)")
    with apply_form("parts_axis", on_apply=lambda: _zone_axis(ctx)):
        axis_field(ctx.draft, "imported_mesh.axis", "Axis direction")
        vector_field(ctx.draft, "imported_mesh.origin", "A point on the axis", "m")


def _zone_axis(ctx: state.Context) -> None:
    """The rotating zone turns about the machine axis."""
    mesh = mesh_of(ctx.draft)
    for zone in mesh.get("zones") or []:
        zone["axis"], zone["origin"] = mesh.get("axis"), mesh.get("origin")


def rotating_view(ctx: state.Context, views: list[PartView]) -> None:
    """Which part rotates: asked, never assumed (owner's plan); its cell zone
    <prefix> turns about the machine axis."""
    mesh = mesh_of(ctx.draft)
    prefixes = [v.prefix for v in views if v.prefix]
    zones = mesh.get("zones") or []
    current = zones[0].get("cell_zone") if zones else None
    names = {v.prefix: v.name or v.prefix for v in views}
    chosen = st.selectbox("Which part rotates", prefixes,
                          index=prefixes.index(current) if current in prefixes else None,
                          format_func=lambda p: f"{names.get(p, p)} ({p})",
                          placeholder="Choose the rotating part (never assumed)",
                          key="machines_parts_rotating")
    if chosen and chosen != current:
        mesh["zones"] = [{"name": chosen, "cell_zone": chosen, "axis": mesh.get("axis"),
                          "origin": mesh.get("origin")}]
        state.bump_generation()
        st.rerun()
    if chosen:
        st.caption(f"Cell zone `{chosen}` (all of the part's cells) turns about the machine "
                   "axis; its rotation is set under the physics as zone "
                   f"`{chosen}`.")


def read_view(ctx: state.Context, views: list[PartView]) -> None:
    pending = [v for v in views if v.prefix and v.info.readable and not v.converted]
    if not views:
        return
    if not pending:
        st.caption("Every part with a prefix has been read (converted once at scale 1, with "
                   "its surface; a run reuses it).")
        return
    missing = [v.path.name for v in views if not v.prefix]
    st.info(f"{len(pending)} part(s) to read: each is converted once at scale 1 to find its "
            "passage count (a run reuses the conversion)."
            + (f" Choose the part of {', '.join(missing)} first." if missing else ""))
    if st.button("Read the parts", key="machines_parts_read", type="primary",
                 disabled=ctx.active):
        with st.spinner("Converting the parts..."):
            state.add_messages(ctx.service.read_parts(ctx.draft))
        st.rerun()


def render(ctx: state.Context, picker: Any, copies: Any, pairs_view: Any,
           names_used: Any) -> None:
    section_header(ctx, "imported_mesh")
    st.caption("Each file is one part: a whole ring or one passage of it. A run converts "
               "each, uses its patches and cell zones as <prefix>_<name>, copies a passage "
               "around the machine axis and stitches the copies, then merges the parts.")
    axis_view(ctx)
    with st.expander("Add .msh files (Windows drives and WSL)",
                     expanded=not mesh_of(ctx.draft).get("parts")):
        picker(ctx)
    copies(ctx)
    views = ctx.service.part_views(ctx.draft)
    for view in views:
        part_card(ctx, view)
    read_view(ctx, views)
    if views:
        rotating_view(ctx, views)
    inspection = ctx.service.machine_inspection(ctx.draft)
    if inspection is not None:
        pairs_view(ctx, inspection)
        st.markdown("**Names in the mesh** (part patch -> name used)")
        st.dataframe(names_used(ctx.draft, inspection), hide_index=True, width="stretch")
    section_issues(ctx, "imported_mesh")

