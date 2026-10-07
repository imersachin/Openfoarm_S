"""End-to-end against a real OpenFOAM installation.

Skipped unless the OpenFOAM executables are on PATH (i.e. the OpenFOAM
environment has been sourced). Run explicitly with:

    pytest -m openfoam tests/integration -v
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import trimesh

from core.config.models import OpenFOAMProfile, ProjectConfig
from core.workflow.dependency_graph import PipelineOperation
from core.workflow.pipeline import MeshPipeline
from visualization.views import mesh_view

TOOLS = ("blockMesh", "snappyHexMesh", "checkMesh", "surfaceFeatureExtract")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(
        any(shutil.which(tool) is None for tool in TOOLS),
        reason="OpenFOAM (openfoam.com) environment not sourced",
    ),
]


def detected_profile() -> OpenFOAMProfile:
    version = os.environ.get("WM_PROJECT_VERSION", "")
    return OpenFOAMProfile.OPENCFD if version.startswith("v") else OpenFOAMProfile.FOUNDATION


def cube_in_box(source: Path, *, extract_features: bool) -> ProjectConfig:
    trimesh.creation.box(extents=(200.0, 200.0, 200.0)).export(source)  # mm
    return ProjectConfig.model_validate({
        "project_name": "Integration",
        "openfoam_profile": detected_profile().value,
        "geometry": {"source_path": str(source), "source_units": "mm", "patch_name": "cube"},
        "mesh": {
            "background": {
                "domain": {"minimum": {"x": -0.5, "y": -0.5, "z": -0.5},
                           "maximum": {"x": 0.5, "y": 0.5, "z": 0.5}},
                "base_cell_size": 0.1,
            },
            "surface": {"minimum_level": 1, "maximum_level": 2,
                        "extract_features": extract_features},
            "location_in_mesh": {"x": 0.41, "y": 0.43, "z": 0.37},
            "max_global_cells": 500_000,
        },
    })


@pytest.mark.parametrize("extract_features", [False, True])
def test_full_pipeline_with_real_openfoam(tmp_path: Path, extract_features: bool) -> None:
    if extract_features and detected_profile() is not OpenFOAMProfile.OPENCFD:
        pytest.skip("feature extraction is implemented for openfoam.com only")
    config = cube_in_box(tmp_path / "cube.stl", extract_features=extract_features)
    case = tmp_path / "case"
    pipeline = MeshPipeline()

    result = pipeline.generate_mesh_sync(case, config)

    assert result.succeeded, [issue.as_dict() for issue in result.issues]
    report = json.loads((case / "reports" / "mesh_quality_report.json").read_text("utf-8"))
    assert report["assessment"]["mesh_validity"] == "VALID", report
    assert report["metrics"]["cells"] > 1000
    patch_names = {patch["name"] for patch in report["metrics"]["patches"]}
    assert "cube" in patch_names
    assert (case / "constant" / "polyMesh" / "points").is_file()
    if extract_features:
        assert (case / "constant" / "triSurface" / "cube.eMesh").is_file()

    view = mesh_view(case)
    assert view.figure is not None, [issue.message for issue in view.issues]

    again = pipeline.generate_mesh_sync(case, config)
    assert again.succeeded
    assert PipelineOperation.GENERATE_MESH in again.reused
