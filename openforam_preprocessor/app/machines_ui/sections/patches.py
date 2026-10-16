"""Patches: one row per patch, name, type and source (a body, a generated domain
face or an imported region). Inlet and outlet are chosen here. Missing rows
can be added with a suggested type; a suggestion is applied only by the
button (decision E5: imported regions are never typed automatically)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.machines_ui import state
from app.machines_ui.sections.common import list_at, section_header, section_issues
from app.machines_ui.sections.fields import PATCH_TYPE_LABELS, PATCH_TYPES
from machines.config import BOX_FACES, CYLINDER_FACES, SourceKind
from machines.patches import suggest_region_types

SUGGESTED = "machines_patch_suggestions"  # {name: type}, kept until applied
SOURCE_KINDS = [k.value for k in SourceKind]


def candidates(ctx: state.Context) -> list[dict[str, Any]]:
    """Patches the draft does not list yet: every body, generated domain face and
    imported region (joint regions excluded), with a suggested type."""
    draft = ctx.draft
    listed = {(p.get("source") or {}).get("ref") for p in draft.get("patches") or []}
    joined = {n for j in draft.get("joints") or [] for n in (j.get("first"), j.get("second"))}
    found: list[dict[str, Any]] = []
    for body in draft.get("bodies") or []:
        name = body.get("name")
        if name and name not in listed:
            found.append({"name": name, "kind": "BODY", "ref": name,
                          "suggested": "WALL" if body.get("motion") == "STATIONARY"
                          else "ROTATING_WALL"})
    domain = draft.get("domain") or {}
    faces = BOX_FACES if domain.get("kind") == "BOX" else \
        CYLINDER_FACES if domain.get("kind") == "CYLINDER" else ()
    flow = draft.get("flow_axis")
    for face in faces:
        if face in listed:
            continue
        suggested = {f"{flow}_min": "INLET", f"{flow}_max": "OUTLET", "axis_min": "INLET",
                     "axis_max": "OUTLET"}.get(face, "SLIP")
        found.append({"name": face, "kind": "DOMAIN_FACE", "ref": face, "suggested": suggested})
    regions = [r for names in ctx.service.surface_regions(draft).values() for r in names
               if r not in listed and r not in joined]
    suggestions = suggest_region_types(regions)
    found += [{"name": r, "kind": "REGION", "ref": r,
               "suggested": suggestions[r].value if r in suggestions else None}
              for r in regions]
    return found


def render(ctx: state.Context) -> None:
    section_header(ctx, "patches")
    patches = list_at(ctx.draft, "patches")
    suggested: dict[str, str] = st.session_state.setdefault(SUGGESTED, {})
    rows = [{"name": p.get("name"), "type": p.get("type"),
             "source kind": (p.get("source") or {}).get("kind"),
             "source": (p.get("source") or {}).get("ref"),
             "suggested": suggested.get(str(p.get("name")))} for p in patches]
    generation = st.session_state.get("draft_generation", 0)
    edited = st.data_editor(rows, num_rows="dynamic", width="stretch",
                            key=f"machines_patches_{generation}", column_config={
                                "type": st.column_config.SelectboxColumn(
                                    "type", options=PATCH_TYPES,
                                    help="Inlet, outlet, wall, rotating wall, symmetry or "
                                    "slip; never assumed."),
                                "source kind": st.column_config.SelectboxColumn(
                                    "source kind", options=SOURCE_KINDS),
                                "suggested": st.column_config.TextColumn(
                                    "suggested", disabled=True)})
    st.caption("Types: " + ", ".join(f"{k} = {v}" for k, v in PATCH_TYPE_LABELS.items()))
    apply, add, use = st.columns(3)
    if apply.button("Apply patches", type="primary", key="machines_apply_patches"):
        ctx.draft["patches"] = [
            {"name": r["name"], "type": r.get("type") or None,
             "source": {"kind": r.get("source kind"), "ref": r.get("source")}}
            for r in edited if r.get("name")]
        for patch in ctx.draft["patches"]:
            if patch["type"] is None:
                del patch["type"]  # left for the user: the section shows it as incomplete
        state.bump_generation()
        st.rerun()
    if add.button("Add missing patches", key="machines_add_patches"):
        for row in candidates(ctx):
            patches.append({"name": row["name"],
                            "source": {"kind": row["kind"], "ref": row["ref"]}})
            if row["suggested"]:
                suggested[row["name"]] = row["suggested"]
        state.bump_generation()
        st.rerun()
    if use.button("Use the suggested types", key="machines_use_suggested",
                  disabled=not any(r["suggested"] and not r["type"] for r in rows)):
        for patch in patches:
            if not patch.get("type") and suggested.get(str(patch.get("name"))):
                patch["type"] = suggested[str(patch["name"])]
        state.bump_generation()
        st.rerun()
    st.caption("Suggested types come from the body motion, the domain face or the region "
               "name only. Check each before you use them.")
    section_issues(ctx, "patches")
