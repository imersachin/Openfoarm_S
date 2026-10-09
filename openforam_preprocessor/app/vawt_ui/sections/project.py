"""Project: name, OpenFOAM profile, environment check, project folder."""

from __future__ import annotations

import streamlit as st

from app.components.widgets import render_issues, text_field
from app.vawt_ui import state
from app.vawt_ui.sections.common import apply_form, section_header, section_issues


def render(ctx: state.Context) -> None:
    section_header(ctx, "project", "Project")
    with apply_form("project"):
        text_field(ctx.draft, "project_name", "Project name",
                   help="Letters, digits, spaces, '_' and '-'.")
    st.markdown("**OpenFOAM profile:** openfoam.com (OpenCFD). The VAWT method is "
                "verified on OpenFOAM v2512.")
    section_issues(ctx, "project")

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
