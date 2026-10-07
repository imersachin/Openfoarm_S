from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from core.config.manager import ConfigurationManager
from core.config.models import ProjectConfig
from core.workflow.dependency_graph import DependencyGraph
from core.workflow.pipeline import MeshPipeline

st.set_page_config(page_title="OpenFOAM Preprocessor", layout="wide")
st.title("OpenFOAM Pre-Processing & Mesh Validation")

project_root = Path(
    st.sidebar.text_input("Project directory", value="./workspace/demo_case")
).resolve()

manager = ConfigurationManager(project_root, DependencyGraph())
pipeline = MeshPipeline()

if not manager.config_path.exists():
    st.info("Create or import a project configuration to begin.")
    st.stop()

snapshot = manager.load_project()
config: ProjectConfig = snapshot.config

st.caption(f"Project: **{config.project_name}** · Revision: **{snapshot.revision}**")
st.divider()

geometry_col, mesh_col = st.columns(2)

with geometry_col:
    st.subheader("Geometry")
    st.code(str(config.geometry.source_path), language=None)
    st.write({
        "patch": config.geometry.patch_name,
        "source_units": config.geometry.source_units.value,
        "scale": config.geometry.scale,
        "rotation_deg": config.geometry.rotation_deg.model_dump(),
        "translation_m": config.geometry.translation.model_dump(),
    })

with mesh_col:
    st.subheader("Mesh")
    st.write({
        "base_cell_size": config.mesh.background.base_cell_size,
        "surface_levels": (
            config.mesh.surface.minimum_level,
            config.mesh.surface.maximum_level,
        ),
        "boundary_layers_enabled": config.mesh.layers.enabled,
        "max_global_cells": config.mesh.max_global_cells,
    })

left, right = st.columns(2)

with left:
    if st.button("Validate geometry and generate dictionaries", type="primary"):
        result = pipeline.prepare_case(project_root, config)
        if result.succeeded:
            st.success(result.message)
        else:
            st.error(result.message)

with right:
    if st.button("Generate mesh and run checkMesh"):
        with st.spinner("Running OpenFOAM meshing pipeline..."):
            # Feature extraction is profile-specific. Keep None until the selected
            # OpenFOAM installation has been detected and verified.
            result = pipeline.generate_mesh_sync(project_root, config, None)
        if result.succeeded:
            st.success(result.message)
        else:
            st.error(result.message)

geometry_report = project_root / "reports" / "geometry_report.json"
mesh_report = project_root / "reports" / "mesh_quality_report.json"

if geometry_report.exists():
    st.subheader("Geometry validation")
    st.json(json.loads(geometry_report.read_text(encoding="utf-8")))

if mesh_report.exists():
    st.subheader("Mesh quality")
    data = json.loads(mesh_report.read_text(encoding="utf-8"))
    st.metric("Quality score", f"{data['score']} / 100", data["status"])
    st.json(data)

with st.expander("Logs"):
    log_dir = project_root / "logs"
    if log_dir.exists():
        selected = st.selectbox("Log file", [item.name for item in sorted(log_dir.glob("*.log"))])
        st.code((log_dir / selected).read_text(encoding="utf-8", errors="replace")[-50_000:])
