"""Project: name, machine type (its profile), mesh source (M1c), OpenFOAM,
environment."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.sections.common import (
    apply_form,
    confirm,
    section_header,
    section_issues,
)
from app.machines_ui.sections.fields import text_field

SOURCES = {"STL": "Build from STL", "MSH": "Import .msh (Fluent or Gmsh)"}
# What a mesh built from STL is made of; an imported mesh replaces all of it.
STL_KEYS = ("bodies", "rotating_zones", "domain", "joints", "wake")


def set_source(draft: dict[str, object], source: str) -> None:
    """Switch the draft's mesh source. The patches of the other source cannot
    be kept (their sources differ); the saved revision changes only on Save."""
    if source == "MSH":
        for key in STL_KEYS:
            draft.pop(key, None)
        draft["rotating_zones"] = []
        draft["imported_mesh"] = {"zones": [], "ami_pairs": []}
    else:
        draft.pop("imported_mesh", None)
        draft.update({"bodies": [], "rotating_zones": [], "joints": []})
    draft["patches"] = []
    state.bump_generation()


def mesh_source(ctx: state.Context) -> None:
    if not ctx.profile.offers_import:
        st.caption("Mesh source: built from STL. To use an imported VAWT mesh, create a "
                   "Custom project.")
        return
    current = "MSH" if ctx.draft.get("imported_mesh") is not None else "STL"
    other = "STL" if current == "MSH" else "MSH"
    st.markdown(f"**Mesh source:** {SOURCES[current]}")
    mesh = ctx.draft.get("imported_mesh")
    filled = any(ctx.draft.get(k) for k in (*STL_KEYS, "patches")) or bool(
        isinstance(mesh, dict) and mesh.get("source_path"))
    if not filled:
        if st.button(f"Use: {SOURCES[other]}", key="machines_mesh_source"):
            set_source(ctx.draft, other)
            st.rerun()
        return
    confirm("mesh_source", f"Switching to '{SOURCES[other]}' removes the "
            + ("bodies, zones, domain" if current == "STL" else "imported mesh settings")
            + " and the patch table from the draft. Revert brings them back until you save.",
            f"Switch to: {SOURCES[other]}", lambda: set_source(ctx.draft, other),
            button_label=f"Use: {SOURCES[other]}")


def render(ctx: state.Context) -> None:
    section_header(ctx, "project")
    with apply_form("project"):
        text_field(ctx.draft, "project_name", "Project name",
                   help="Letters, digits, spaces, '_' and '-'.")
    section_issues(ctx, "project")
    st.markdown(f"**Machine:** {ctx.profile.label} — {ctx.profile.description}")
    mesh_source(ctx)
    if state.saved_raw() is None:
        choices = {p.id: p.label for p in ctx.service.profiles.all()}
        choice = st.selectbox("Change the machine (only before the first save)",
                              list(choices), index=list(choices).index(ctx.profile.id),
                              format_func=choices.__getitem__, key="machines_change_profile")
        if choice != ctx.profile.id and st.button("Change machine", key="machines_change"):
            state.choose_profile(choice)
            st.rerun()
    st.markdown("**OpenFOAM profile:** openfoam.com (OpenCFD). The rotating-machinery "
                "workflow is verified on OpenFOAM v2512.")
    st.markdown("**Environment**")
    environment = ctx.service.environment()
    st.write({
        "Running in WSL": environment.wsl,
        "Distribution": environment.distro or "—",
        "OpenFOAM version": environment.openfoam_version or "not sourced",
        "Projects folder": str(environment.projects_root),
        "This project": str(ctx.service.root),
    })
    render_issues((*environment.issues, *ctx.service.location_issues()))
