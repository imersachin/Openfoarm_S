"""Streamlit presentation layer.

Collects configuration, calls ProjectService, and displays structured state.
It contains no engineering logic: validation, dependency planning, OpenFOAM
sequencing and caching all happen behind ProjectService.

Run from the repository root:  streamlit run app/dashboard.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

# `streamlit run app/dashboard.py` only puts app/ on sys.path.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from app.components.widgets import (  # noqa: E402
    GENERATION,
    bool_field,
    float_field,
    int_field,
    render_issues,
    select_field,
    set_text_value,
    text_field,
    vector_field,
)
from core.config.models import LengthUnit, OpenFOAMProfile  # noqa: E402
from core.services import ProjectService, new_project_template  # noqa: E402
from core.workflow.dependency_graph import PipelineOperation  # noqa: E402

OPERATION_LABELS = {
    PipelineOperation.IMPORT_GEOMETRY: "Import and transform geometry",
    PipelineOperation.VALIDATE_GEOMETRY: "Validate geometry",
    PipelineOperation.GENERATE_BLOCK_MESH_DICT: "Write blockMeshDict",
    PipelineOperation.GENERATE_FEATURE_DICT: "Write feature-extraction dictionary",
    PipelineOperation.GENERATE_SNAPPY_DICT: "Write snappyHexMeshDict",
    PipelineOperation.GENERATE_MESH_QUALITY_DICT: "Write meshQualityDict",
    PipelineOperation.GENERATE_BACKGROUND_MESH: "Background mesh (blockMesh)",
    PipelineOperation.EXTRACT_FEATURES: "Feature extraction",
    PipelineOperation.GENERATE_MESH: "Mesh (snappyHexMesh)",
    PipelineOperation.CHECK_MESH: "checkMesh",
    PipelineOperation.VALIDATE_MESH: "Mesh validation",
}
UNIT_LABELS = {
    "m": "metres (m)", "cm": "centimetres (cm)", "mm": "millimetres (mm)",
    "um": "micrometres (µm)", "in": "inches (in)", "ft": "feet (ft)",
}
PROFILE_LABELS = {
    "openfoam_com": "openfoam.com (ESI, e.g. v2312)",
    "openfoam_foundation": "openfoam.org (Foundation, e.g. 11)",
}
TABS = ["Project", "Geometry", "Transform", "Domain", "Mesh", "Refinement",
        "Boundary Layers", "Validate", "Generate", "Results", "Logs"]


def operation_list(operations: Any) -> str:
    return "\n".join(f"- {OPERATION_LABELS.get(op, op)}" for op in operations)


st.set_page_config(page_title="OpenFOAM Preprocessor", layout="wide")
st.title("OpenFOAM Preprocessor")

# --- project selection and state --------------------------------------------

root_input = st.sidebar.text_input("Project directory", value="./workspace/demo_case")
root = Path(root_input).expanduser().resolve()
service = ProjectService(root)
state = st.session_state

if state.get("root") != str(root):
    loaded = service.load()
    state.root = str(root)
    state.saved_raw = copy.deepcopy(loaded.raw) if loaded.raw is not None else None
    state.draft = copy.deepcopy(loaded.raw) if loaded.raw is not None else new_project_template()
    state.load_issues = loaded.issues
    state.revision = loaded.revision
    state.last_result = None
    state[GENERATION] = state.get(GENERATION, 0) + 1

draft: dict[str, Any] = state.draft
st.sidebar.caption(f"`{root}`")
sidebar_status = st.sidebar.container()  # filled at the end, after the draft is updated

tabs =dict(zip(TABS, st.tabs(TABS), strict=True))

# --- configuration tabs (collect input only) ---------------------------------

with tabs["Project"]:
    text_field(draft, "project_name", "Project name")
    select_field(draft, "openfoam_profile", "OpenFOAM distribution",
                 [p.value for p in OpenFOAMProfile], PROFILE_LABELS,
                 help="Must match the installed OpenFOAM; it selects the commands and "
                 "dictionaries that are generated.")

with tabs["Geometry"]:
    upload = st.file_uploader("Add an STL file to the project", type=["stl"])
    if upload is not None and state.get("uploaded_id") != upload.file_id:
        try:
            stored = service.store_source_file(upload.name, upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            state.uploaded_id = upload.file_id
            set_text_value("geometry.source_path", str(stored))
            st.success(f"Stored as `{stored}`")
    text_field(draft, "geometry.source_path", "Source STL path")
    select_field(draft, "geometry.source_units", "Units the STL was exported in",
                 [u.value for u in LengthUnit], UNIT_LABELS,
                 placeholder="Select units (required — STL files do not store units)")
    text_field(draft, "geometry.patch_name", "Surface patch name",
               help="Name of the geometry patch in the mesh (letters, digits, underscore).")

with tabs["Transform"]:
    st.caption(
        "Applied in this order: unit conversion → scale → rotation → translation. "
        "Scale and rotation act about the origin (0, 0, 0). Rotation is about the fixed "
        "X, then Y, then Z axes. Translation is in metres."
    )
    float_field(draft, "geometry.scale", "Scale factor", min_value=0.0)
    vector_field(draft, "geometry.rotation_deg", "Rotation", "degrees")
    vector_field(draft, "geometry.translation", "Translation", "m")

with tabs["Domain"]:
    st.caption("Background mesh box, in metres (after geometry transformation).")
    vector_field(draft, "mesh.background.domain.minimum", "Domain minimum corner", "m")
    vector_field(draft, "mesh.background.domain.maximum", "Domain maximum corner", "m")
    vector_field(draft, "mesh.location_in_mesh", "Point inside the fluid region", "m",
                 help="snappyHexMesh keeps the region containing this point. It must be "
                 "strictly inside the domain and outside the solid geometry.")

with tabs["Mesh"]:
    float_field(draft, "mesh.background.base_cell_size", "Background cell size (m)",
                min_value=0.0)
    int_field(draft, "mesh.background.max_cells_per_axis", "Max background cells per axis",
              min_value=1)
    float_field(draft, "mesh.background.expansion_ratio", "Background grading ratio",
                min_value=0.0)
    int_field(draft, "mesh.max_global_cells", "Max total cells (snappyHexMesh limit)",
              min_value=1)
    cells = service.background_cells(draft)
    if cells is not None:
        requested, effective = cells
        st.write(f"Background cells: {effective[0]} × {effective[1]} × {effective[2]}")
        if requested != effective:
            st.warning(f"Limited from {requested} by the per-axis maximum.")
    with st.expander("Advanced: snappyHexMesh quality controls (meshQualityDict)"):
        st.caption("Used by snappyHexMesh while meshing. Changing these re-meshes.")
        float_field(draft, "mesh.snappy_quality.max_non_orthogonality", "maxNonOrtho (°)")
        float_field(draft, "mesh.snappy_quality.max_boundary_skewness", "maxBoundarySkewness")
        float_field(draft, "mesh.snappy_quality.max_internal_skewness", "maxInternalSkewness")
        float_field(draft, "mesh.snappy_quality.min_volume", "minVol")
        float_field(draft, "mesh.snappy_quality.min_determinant", "minDeterminant")

with tabs["Refinement"]:
    int_field(draft, "mesh.surface.minimum_level", "Surface refinement: minimum level",
              min_value=0, max_value=10)
    int_field(draft, "mesh.surface.maximum_level", "Surface refinement: maximum level",
              min_value=0, max_value=10)
    float_field(draft, "mesh.surface.feature_angle_deg", "Feature angle (°)")
    bool_field(draft, "mesh.surface.extract_features", "Extract and refine feature edges",
               help="Runs surfaceFeatureExtract (openfoam.com profile only).")
    int_field(draft, "mesh.surface.feature_refinement_level", "Feature refinement level",
              min_value=0, max_value=10, help="Used only when feature extraction is enabled.")

with tabs["Boundary Layers"]:
    bool_field(draft, "mesh.layers.enabled", "Add boundary layers")
    int_field(draft, "mesh.layers.number_of_layers", "Number of layers", min_value=1,
              max_value=20)
    float_field(draft, "mesh.layers.expansion_ratio", "Expansion ratio")
    float_field(draft, "mesh.layers.final_layer_thickness",
                "Final layer thickness (relative)")
    float_field(draft, "mesh.layers.min_thickness", "Minimum total thickness (relative)")

# --- validate and save ----------------------------------------------------------

with tabs["Validate"]:
    st.subheader("Mesh acceptance limits")
    st.caption("Applied to checkMesh results after meshing. Changing them only "
               "re-validates; the mesh is reused.")
    float_field(draft, "mesh.quality.max_non_orthogonality", "Max non-orthogonality (°)")
    float_field(draft, "mesh.quality.max_internal_skewness", "Max skewness")
    float_field(draft, "mesh.quality.min_volume", "Min cell volume")

    st.subheader("Configuration check")
    validation = service.validate(draft)
    if validation.config is None:
        render_issues(validation.issues)
    else:
        st.success("Configuration is valid.")
        plan = service.preview_changes(draft)
        if plan is not None and state.saved_raw is not None:
            if plan.is_empty:
                st.info("Saving these changes requires no re-computation.")
            else:
                st.markdown("Saving these changes makes these steps out of date:\n"
                            + operation_list(plan.operations))

    save_col, discard_col = st.columns(2)
    if save_col.button("Save configuration", type="primary",
                       disabled=validation.config is None):
        save_result = service.save(draft)
        if save_result.saved:
            state.saved_raw = copy.deepcopy(draft)
            state.load_issues = ()
            st.success(f"Saved revision {save_result.revision}.")
        else:
            render_issues(save_result.issues)
    if discard_col.button("Discard unsaved changes"):
        state.draft = copy.deepcopy(state.saved_raw) if state.saved_raw else new_project_template()
        state[GENERATION] += 1
        st.rerun()

# --- operations ---------------------------------------------------------------

with tabs["Generate"]:
    config = service.load().config
    if config is None:
        st.info("Save a valid configuration on the Validate tab first.")
    else:
        if state.saved_raw is not None and draft != state.saved_raw:
            st.warning("These actions use the saved configuration, not unsaved changes.")
        prepare_col, preflight_col = st.columns(2)
        if prepare_col.button("Prepare case (geometry + dictionaries)"):
            state.last_result = ("prepare", service.prepare_case(config), None)
        if preflight_col.button("Check resources"):
            prepared, assessment, issues = service.preflight(config)
            state.last_result = ("preflight", prepared, (assessment, issues))

        force = st.checkbox("Ignore cached results (full re-run)")
        accept_risk = st.checkbox(
            "I accept HIGH RESOURCE RISK for this run",
            help="Required to mesh when the resource check reports HIGH RESOURCE RISK. "
            "BLOCKED always stops.",
        )
        if st.button("Generate mesh", type="primary"):
            with st.spinner("Running OpenFOAM… (see the Logs tab afterwards)"):
                result = service.generate_mesh(
                    config, force=force, allow_high_resource_risk=accept_risk
                )
            state.last_result = ("mesh", result, None)

        if state.last_result is not None:
            kind, result, extra = state.last_result
            (st.success if result.succeeded else st.error)(result.message)
            if kind == "preflight" and extra is not None and extra[0] is not None:
                assessment, issues = extra
                st.metric("Resource status", assessment.status.value)
                st.write(f"Estimated cells: ~{assessment.cells.total:,} (heuristic)")
                render_issues(issues)
            if result.executed or result.reused:
                left, right = st.columns(2)
                left.markdown("**Ran**\n" + operation_list(result.executed))
                right.markdown("**Reused (verified)**\n" + operation_list(result.reused))
            render_issues(result.issues)

        with st.expander("Advanced: generated dictionaries (read-only)"):
            dictionaries = service.dictionaries()
            if not dictionaries:
                st.caption("No dictionaries yet.")
            for name, text in dictionaries.items():
                st.markdown(f"`system/{name}`")
                st.code(text, language=None)

# --- results and logs -----------------------------------------------------------

with tabs["Results"]:
    reports = service.reports()
    if not any(reports.values()):
        st.info("No results yet.")

    geometry = reports["geometry"]
    if geometry:
        st.subheader("Geometry")
        artifact = geometry.get("artifact") or {}
        if artifact.get("extents"):
            st.write("Transformed extents (m): "
                     + " × ".join(f"{v:.6g}" for v in artifact["extents"]))
        with st.expander("Geometry report"):
            st.json(geometry)

    preflight = reports["preflight"]
    if preflight:
        st.subheader("Resource check")
        st.metric("Status", preflight["status"])
        st.caption(preflight.get("disclaimer", ""))
        with st.expander("Resource report"):
            st.json(preflight)

    mesh_report = reports["mesh_quality"]
    if mesh_report:
        st.subheader("Mesh")
        assessment = mesh_report.get("assessment", {})
        columns = st.columns(4)
        for column, (label, key) in zip(columns, [
            ("Mesh validity", "mesh_validity"), ("Mesh quality", "mesh_quality"),
            ("Simulation suitability", "simulation_suitability"),
            ("CFD accuracy", "cfd_accuracy"),
        ], strict=True):
            column.metric(label, assessment.get(key, "—"))
        if assessment.get("note"):
            st.caption(assessment["note"])
        metrics = mesh_report.get("metrics", {})
        st.write({k: metrics.get(k) for k in (
            "cells", "faces", "points", "max_non_orthogonality", "max_skewness",
            "max_aspect_ratio", "min_volume",
        )})
        chart = service.quality_chart()
        if chart.figure is not None:
            st.plotly_chart(chart.figure, use_container_width=True)
        for note in chart.notes:
            st.caption(note)
        with st.expander("Mesh quality report"):
            st.json(mesh_report)

    st.subheader("3D views")
    st.caption("Loaded on request. Views show whether the geometry and mesh are what you "
               "intended; they do not indicate CFD accuracy.")
    view_config = service.load().config
    geometry_col, mesh_col = st.columns(2)
    if geometry_col.button("Show geometry and domain", disabled=view_config is None):
        state.view = "geometry"
    if mesh_col.button("Show mesh and problem locations"):
        state.view = "mesh"
    view = None
    if state.get("view") == "geometry" and view_config is not None:
        view = service.geometry_view(view_config)
    elif state.get("view") == "mesh":
        view = service.mesh_view()
    if view is not None:
        render_issues(view.issues)
        for note in view.notes:
            st.caption(note)
        if view.figure is not None:
            st.plotly_chart(view.figure, use_container_width=True)

with tabs["Logs"]:
    logs = service.logs()
    if not logs:
        st.info("No logs yet.")
    else:
        selected = st.selectbox(
            "Log file", [log.name for log in logs],
            format_func=lambda name: f"{name} "
            f"({next(log.size_bytes for log in logs if log.name == name):,} bytes)",
        )
        if selected is not None:
            st.code(service.read_log(selected), language=None)

# --- sidebar status (rendered last so it reflects this run's edits) ----------------

with sidebar_status:
    loaded_now = service.load()
    if state.saved_raw is None and not state.load_issues:
        st.info("New project: fill in the tabs, then save it on the **Validate** tab.")
    elif loaded_now.config is not None:
        st.success(f"Saved revision {loaded_now.revision}")
    if state.load_issues:
        st.error("The saved project configuration has problems:")
        render_issues(state.load_issues)
    if state.saved_raw is not None and draft != state.saved_raw:
        st.warning("Unsaved changes.")
