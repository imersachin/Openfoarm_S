"""Project: name, machine type (its profile), OpenFOAM, environment."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from app.machines_ui.sections.common import apply_form, section_header, section_issues
from app.machines_ui.sections.fields import text_field


def render(ctx: state.Context) -> None:
    section_header(ctx, "project")
    with apply_form("project"):
        text_field(ctx.draft, "project_name", "Project name",
                   help="Letters, digits, spaces, '_' and '-'.")
    section_issues(ctx, "project")
    st.markdown(f"**Machine:** {ctx.profile.label} — {ctx.profile.description}")
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
