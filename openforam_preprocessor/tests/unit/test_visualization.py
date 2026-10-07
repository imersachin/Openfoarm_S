from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

from core.workflow.pipeline import MeshPipeline
from tests.foam_fixtures import write_poly_mesh, write_set
from tests.helpers import build_config
from visualization.foam_reader import (
    FoamReadError,
    read_boundary,
    read_poly_mesh,
    read_set,
)
from visualization.views import geometry_view, mesh_view, quality_chart, sample_indices


def traces(view) -> dict[str, object]:
    return {trace.name: trace for trace in view.figure.data}


# --- reader ---------------------------------------------------------------

def test_reads_ascii_poly_mesh(tmp_path: Path) -> None:
    mesh = read_poly_mesh(write_poly_mesh(tmp_path))

    assert mesh.points.shape == (12, 3)
    assert len(mesh.faces) == 11
    assert mesh.faces[0].tolist() == [1, 4, 10, 7]
    assert mesh.owner.tolist() == [0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    assert [(p.name, p.patch_type, p.n_faces, p.start_face) for p in mesh.patches] == [
        ("inlet", "patch", 1, 1), ("outlet", "patch", 1, 2), ("walls", "wall", 8, 3),
    ]


def test_binary_format_is_rejected_not_guessed(tmp_path: Path) -> None:
    poly = write_poly_mesh(tmp_path, fmt="binary")

    with pytest.raises(FoamReadError, match="binary"):
        read_poly_mesh(poly)


def test_compressed_mesh_is_rejected(tmp_path: Path) -> None:
    poly = write_poly_mesh(tmp_path)
    (poly / "points").rename(poly / "points.gz")

    with pytest.raises(FoamReadError, match="compressed"):
        read_poly_mesh(poly)


def test_count_mismatch_is_rejected(tmp_path: Path) -> None:
    poly = write_poly_mesh(tmp_path)
    text = (poly / "points").read_text("utf-8").replace("12\n(", "13\n(")
    (poly / "points").write_text(text, "utf-8")

    with pytest.raises(FoamReadError, match="expected 13 points"):
        read_poly_mesh(poly)


def test_boundary_without_face_counts_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "boundary"
    path.write_text("1\n(\n    wall\n    {\n        type wall;\n    }\n)\n", "utf-8")

    with pytest.raises(FoamReadError, match="nFaces"):
        read_boundary(path)


def test_reads_sets(tmp_path: Path) -> None:
    path = write_set(write_poly_mesh(tmp_path), "skewFaces", "faceSet", [0, 2])

    assert read_set(path)[0] == "faceSet"
    assert read_set(path)[1].tolist() == [0, 2]


# --- mesh view --------------------------------------------------------------

def test_mesh_view_shows_patches_and_problem_locations(tmp_path: Path) -> None:
    poly = write_poly_mesh(tmp_path)
    write_set(poly, "skewFaces", "faceSet", [0])
    write_set(poly, "zeroVolumeCells", "cellSet", [1])

    view = mesh_view(tmp_path)

    shown = traces(view)
    assert {"inlet (patch, 1 faces)", "outlet (patch, 1 faces)",
            "walls (wall, 8 faces)"} <= set(shown)
    skew = shown["checkMesh: skewFaces (1)"]
    assert (skew.x[0], skew.y[0], skew.z[0]) == (1.0, 0.5, 0.5)  # internal face centre
    cell = shown["checkMesh: zeroVolumeCells (1)"]
    assert 1.0 < cell.x[0] <= 2.0  # inside cell 1
    assert view.issues == ()


def test_mesh_view_triangulates_quads(tmp_path: Path) -> None:
    write_poly_mesh(tmp_path)

    walls = traces(mesh_view(tmp_path))["walls (wall, 8 faces)"]

    assert len(walls.i) == 16  # 8 quads -> 16 triangles


def test_mesh_view_samples_large_boundaries(tmp_path: Path) -> None:
    write_poly_mesh(tmp_path)

    view = mesh_view(tmp_path, max_faces=4)

    assert any("1 in every 3 boundary faces (10 in total)" in note for note in view.notes)
    walls = traces(view)["walls (wall, 8 faces)"]
    assert len(walls.i) < 16


def test_mesh_view_without_valid_mesh_is_an_issue(tmp_path: Path) -> None:
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "points").write_text("not a foam file", "utf-8")

    view = mesh_view(tmp_path)

    assert view.figure is None
    assert view.issues[0].code == "VIEW_MESH_UNAVAILABLE"


# --- geometry view ------------------------------------------------------------

def test_geometry_view_shows_original_transformed_domain_and_location(tmp_path: Path) -> None:
    source = tmp_path / "cube_mm.stl"
    trimesh.creation.box(extents=(1000, 1000, 1000)).export(source)
    config = build_config(source, source_units="mm",
                          geometry={"translation": {"x": 0.5, "y": 0, "z": 0}})
    case = tmp_path / "case"
    assert MeshPipeline().prepare_case(case, config).succeeded

    view = geometry_view(case, config)

    shown = traces(view)
    original = shown["Original (only converted mm → m)"]
    transformed = shown["Transformed (meshed geometry)"]
    assert (min(original.x), max(original.x)) == pytest.approx((-0.5, 0.5))
    assert (min(transformed.x), max(transformed.x)) == pytest.approx((0.0, 1.0))
    domain = shown["Domain (background mesh box)"]
    assert max(x for x in domain.x if x is not None) == 2.0
    location = shown["locationInMesh"]
    assert (location.x[0], location.y[0], location.z[0]) == (1.5, 0.0, 0.0)
    assert view.figure.layout.scene.aspectmode == "data"


def test_geometry_view_before_prepare_reports_missing_artifact(
    tmp_path: Path, cube_stl: Path
) -> None:
    view = geometry_view(tmp_path / "case", build_config(cube_stl))

    assert [i.code for i in view.issues] == ["VIEW_ARTIFACT_UNAVAILABLE"]
    assert "Domain (background mesh box)" in traces(view)


def test_geometry_view_samples_large_surfaces(tmp_path: Path) -> None:
    source = tmp_path / "sphere.stl"
    trimesh.creation.icosphere(subdivisions=3).export(source)  # 1280 faces
    config = build_config(source)

    view = geometry_view(tmp_path / "case", config, max_faces=100)

    assert len(traces(view)["Original (only converted m → m)"].i) == 100
    assert any("showing 100 of 1,280" in note for note in view.notes)


def test_sample_indices_is_deterministic_and_bounded() -> None:
    assert sample_indices(5, 10).tolist() == [0, 1, 2, 3, 4]
    picked = sample_indices(1000, 10)
    assert len(picked) == 10
    assert picked[0] == 0 and picked[-1] == 999
    assert np.array_equal(picked, sample_indices(1000, 10))


# --- quality chart --------------------------------------------------------------

def test_quality_chart_marks_exceeded_limits() -> None:
    report = {
        "metrics": {"max_non_orthogonality": 72.0, "max_skewness": 1.2},
        "limits": {"max_non_orthogonality": 65.0, "max_internal_skewness": 4.0},
    }

    view = quality_chart(report)

    measured = view.figure.data[0]
    assert list(measured.y) == [72.0, 1.2]
    assert measured.marker.color[0] != measured.marker.color[1]
    assert any("does not imply CFD accuracy" in note for note in view.notes)


def test_quality_chart_without_metrics() -> None:
    assert quality_chart({"metrics": {}, "limits": {}}).figure is None


# --- visualization is optional for computation --------------------------------

def test_meshing_pipeline_does_not_import_plotly() -> None:
    root = Path(__file__).parents[2]
    code = (
        "import sys; import core.workflow.pipeline, core.services; "
        "print('plotly' in sys.modules)"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert output == "False"
