from pathlib import Path

import pytest
import trimesh

from core.issues import IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from geometry.validator import TrimeshGeometryValidator


def codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_valid_cube_reports_concrete_geometry(cube_stl: Path) -> None:
    report = TrimeshGeometryValidator().validate(cube_stl)

    assert not has_stopping_issue(report.issues)
    assert report.faces == 12
    assert report.is_watertight is True
    assert report.components == 1
    assert report.extents == pytest.approx((1.0, 1.0, 1.0))
    assert report.bounds_min == pytest.approx((-0.5, -0.5, -0.5))
    assert report.volume == pytest.approx(1.0)


def test_missing_file_is_structured_input_error(tmp_path: Path) -> None:
    report = TrimeshGeometryValidator().validate(tmp_path / "missing.stl")

    assert codes(report) == {"GEOMETRY_SOURCE_MISSING"}
    issue = report.issues[0]
    assert issue.category is IssueCategory.INPUT
    assert issue.severity is IssueSeverity.ERROR
    assert issue.stage is IssueStage.GEOMETRY_IMPORT
    assert issue.suggested_action
    assert report.faces is None


def test_empty_stl_reports_no_faces_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "empty.stl"
    path.write_text("solid empty\nendsolid empty\n", encoding="utf-8")

    report = TrimeshGeometryValidator().validate(path)

    assert codes(report) == {"NO_FACES"}
    assert has_stopping_issue(report.issues)
    assert report.faces == 0


def test_unreadable_file_is_reported_as_stopping_issue(tmp_path: Path) -> None:
    path = tmp_path / "garbage.stl"
    path.write_bytes(b"\x00\x01not an stl at all")

    report = TrimeshGeometryValidator().validate(path)

    # trimesh may read garbage as an empty surface rather than raising; either
    # way meshing must be stopped with a structured issue, not an exception.
    assert has_stopping_issue(report.issues)
    assert report.issues[0].code in {"GEOMETRY_UNREADABLE", "NO_FACES"}


def test_stl_with_separate_triangle_vertices_is_watertight(tmp_path: Path) -> None:
    # Binary STL export stores 3 vertices per triangle (36 for a cube).
    path = tmp_path / "cube_binary.stl"
    trimesh.creation.box(extents=(2.0, 1.0, 0.5)).export(path, file_type="stl")

    report = TrimeshGeometryValidator().validate(path)

    assert report.is_watertight is True
    assert report.vertices == 8
    assert report.extents == pytest.approx((2.0, 1.0, 0.5))
    assert "NOT_WATERTIGHT" not in codes(report)


def test_report_serializes_issues(cube_stl: Path) -> None:
    data = TrimeshGeometryValidator().validate(cube_stl).as_dict()

    assert data["faces"] == 12
    assert isinstance(data["issues"], list)


def test_inward_normals_are_reported_not_repaired(tmp_path: Path) -> None:
    box = trimesh.creation.box()
    box.invert()
    path = tmp_path / "inverted.stl"
    box.export(path)
    original = path.read_bytes()

    report = TrimeshGeometryValidator().validate(path)

    assert "INWARD_NORMALS" in codes(report)
    assert report.volume == pytest.approx(-1.0)
    assert path.read_bytes() == original


def test_outward_normals_are_not_flagged(cube_stl: Path) -> None:
    assert "INWARD_NORMALS" not in codes(TrimeshGeometryValidator().validate(cube_stl))


def test_duplicate_faces_are_reported() -> None:
    box = trimesh.creation.box()
    faces = list(box.faces) + [box.faces[0]]
    mesh = trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False)

    report = TrimeshGeometryValidator().validate_mesh(mesh, Path("dup.stl"))

    assert "DUPLICATE_FACES" in codes(report)
    issue = next(i for i in report.issues if i.code == "DUPLICATE_FACES")
    assert issue.details["count"] == 1


def test_degenerate_faces_are_reported() -> None:
    box = trimesh.creation.box()
    vertices = list(box.vertices) + [[0, 0, 0], [1, 0, 0], [2, 0, 0]]  # collinear
    n = len(box.vertices)
    faces = list(box.faces) + [[n, n + 1, n + 2]]
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    report = TrimeshGeometryValidator().validate_mesh(mesh, Path("degenerate.stl"))

    assert "DEGENERATE_FACES" in codes(report)


def test_non_finite_coordinates_stop_validation() -> None:
    mesh = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [0, float("nan"), 0]], faces=[[0, 1, 2]], process=False
    )

    report = TrimeshGeometryValidator().validate_mesh(mesh, Path("nan.stl"))

    assert codes(report) == {"NON_FINITE_COORDINATES"}
    assert has_stopping_issue(report.issues)


def test_dimension_check_only_when_requested(tmp_path: Path) -> None:
    path = tmp_path / "huge.stl"
    trimesh.creation.box(extents=(5e4, 1, 1)).export(path)
    validator = TrimeshGeometryValidator()

    assert "SUSPICIOUS_DIMENSIONS" not in codes(validator.validate(path))
    report = validator.validate(path, check_dimensions_in_metres=True)
    issue = next(i for i in report.issues if i.code == "SUSPICIOUS_DIMENSIONS")
    assert issue.category is IssueCategory.UNITS
    assert issue.severity is IssueSeverity.WARNING


def test_dimension_limits_are_configurable(cube_stl: Path) -> None:
    from geometry.validator import DimensionLimits

    strict = TrimeshGeometryValidator(DimensionLimits(min_largest_extent_m=2.0))

    report = strict.validate(cube_stl, check_dimensions_in_metres=True)

    assert "SUSPICIOUS_DIMENSIONS" in codes(report)
