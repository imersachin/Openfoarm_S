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
