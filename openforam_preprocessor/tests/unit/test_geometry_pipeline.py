"""M2: the transformed artifact, not the source, is what downstream meshing consumes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh

from core.workflow.pipeline import MeshPipeline
from tests.helpers import build_config


@pytest.fixture
def mm_cube(tmp_path: Path) -> Path:
    """A 1000 mm cube centred on the origin."""
    path = tmp_path / "source" / "cube_mm.stl"
    path.parent.mkdir()
    trimesh.creation.box(extents=(1000, 1000, 1000)).export(path)
    return path


def artifact(case: Path) -> Path:
    return case / "constant" / "triSurface" / "part.stl"


def test_artifact_has_converted_and_transformed_coordinates(tmp_path: Path, mm_cube: Path) -> None:
    case = tmp_path / "case"
    config = build_config(
        mm_cube,
        source_units="mm",
        geometry={"translation": {"x": 0.5, "y": 0, "z": 0}},
    )

    result = MeshPipeline().prepare_case(case, config)

    assert result.succeeded, result.issues
    bounds = trimesh.load_mesh(artifact(case), process=False).bounds
    assert bounds.tolist() == [[0.0, -0.5, -0.5], [1.0, 0.5, 0.5]]


def test_snappy_references_the_transformed_artifact(tmp_path: Path, mm_cube: Path) -> None:
    case = tmp_path / "case"
    MeshPipeline().prepare_case(case, build_config(mm_cube, source_units="mm"))

    snappy = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert "part.stl" in snappy
    assert "cube_mm.stl" not in snappy
    assert artifact(case).is_file()


def test_source_file_is_never_modified(tmp_path: Path, mm_cube: Path) -> None:
    original = mm_cube.read_bytes()
    config = build_config(mm_cube, source_units="mm", geometry={"scale": 3})

    MeshPipeline().prepare_case(tmp_path / "case", config)

    assert mm_cube.read_bytes() == original


def test_changed_transformation_rewrites_artifact(tmp_path: Path, mm_cube: Path) -> None:
    case = tmp_path / "case"
    pipeline = MeshPipeline()

    pipeline.prepare_case(case, build_config(mm_cube, source_units="mm"))
    first = artifact(case).read_bytes()
    pipeline.prepare_case(case, build_config(mm_cube, source_units="mm"))
    assert artifact(case).read_bytes() == first  # identical inputs -> identical artifact

    rotation = {"rotation_deg": {"x": 0, "y": 0, "z": 45}}
    rotated = build_config(mm_cube, source_units="mm", geometry=rotation)
    pipeline.prepare_case(case, rotated)
    assert artifact(case).read_bytes() != first


def test_report_records_source_transformation_and_artifact(tmp_path: Path, mm_cube: Path) -> None:
    case = tmp_path / "case"
    result = MeshPipeline().prepare_case(case, build_config(mm_cube, source_units="mm"))

    report = json.loads(result.geometry_report_path.read_text(encoding="utf-8"))
    assert report["source"]["extents"] == [1000.0, 1000.0, 1000.0]
    assert report["artifact"]["extents"] == [1.0, 1.0, 1.0]
    assert report["transformation"]["order"] == [
        "unit_conversion", "scale", "rotation", "translation",
    ]
    assert report["transformation"]["source_units"] == "mm"
    assert report["transformation"]["target_units"] == "m"
    assert report["transformation"]["pivot"] == [0.0, 0.0, 0.0]
    assert report["artifact_path"] == str(artifact(case))


def test_implausible_size_warns_about_units(tmp_path: Path, mm_cube: Path) -> None:
    # A 1000-unit cube declared as micrometres is 1 mm: fine. Declared as feet
    # with scale 100 it is ~30 km: almost certainly wrong units.
    config = build_config(mm_cube, source_units="ft", geometry={"scale": 100})

    result = MeshPipeline().prepare_case(tmp_path / "case", config)

    assert result.succeeded  # warning, not a blocker
    assert "SUSPICIOUS_DIMENSIONS" in {i.code for i in result.issues}


def test_invalid_source_geometry_stops_before_writing_artifact(tmp_path: Path) -> None:
    source = tmp_path / "empty.stl"
    source.write_text("solid empty\nendsolid empty\n", encoding="utf-8")
    case = tmp_path / "case"

    result = MeshPipeline().prepare_case(case, build_config(source))

    assert not result.succeeded
    assert [i.code for i in result.issues] == ["NO_FACES"]
    assert not artifact(case).exists()
