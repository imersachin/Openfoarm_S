from pathlib import Path

import pytest

from mesh.parser import CheckMeshParser, PatchSummary

FIXTURES = Path(__file__).parents[1] / "fixtures" / "checkmesh"


def parse(name: str):
    return CheckMeshParser().parse_file(FIXTURES / name)


def test_parse_core_checkmesh_metrics() -> None:
    log = """
    Mesh stats
    points:           12500
    faces:            65000
    cells:            21000
    Mesh non-orthogonality Max: 52.4 average: 8.1
    Mesh OK.
    """

    metrics = CheckMeshParser().parse_text(log)

    assert metrics.points == 12500
    assert metrics.faces == 65000
    assert metrics.cells == 21000
    assert metrics.max_non_orthogonality == 52.4
    assert metrics.average_non_orthogonality == 8.1
    assert metrics.mesh_ok is True
    assert metrics.recognized is True


def test_successful_checkmesh() -> None:
    metrics = parse("ok.log")

    assert (metrics.points, metrics.faces, metrics.cells) == (1782, 4580, 700)
    assert metrics.boundary_patches == 3
    assert metrics.patches == (
        PatchSummary("inlet", 100, 121),
        PatchSummary("outlet", 100, 121),
        PatchSummary("part", 1760, 1802),
    )
    assert metrics.max_non_orthogonality == 42.7
    assert metrics.max_skewness == 1.85
    assert metrics.max_aspect_ratio == 3.2
    assert metrics.min_volume == pytest.approx(1.5e-06)
    assert metrics.max_volume == pytest.approx(0.125)
    assert metrics.failed_checks == 0
    assert metrics.failed_check_messages == ()
    assert metrics.zero_or_negative_volumes is False
    assert metrics.mesh_ok is True


def test_internal_faces_line_does_not_shadow_face_count() -> None:
    metrics = CheckMeshParser().parse_text(
        "Mesh stats\n    internal faces:   10\n    faces:            25\n"
    )
    assert metrics.faces == 25


def test_failed_checks_with_high_non_orthogonality_and_skewness() -> None:
    metrics = parse("failed_checks.log")

    assert metrics.mesh_ok is False
    assert metrics.failed_checks == 2
    assert metrics.max_non_orthogonality == 78.6
    assert metrics.severely_non_orthogonal_faces == 15
    assert metrics.max_skewness == 6.12
    assert metrics.failed_check_messages == (
        "Error in face pyramids: 4 faces are incorrectly oriented.",
        "Max skewness = 6.12, 9 highly skew faces detected which may impair the quality "
        "of the results",
    )
    assert metrics.warning_messages == (
        "Number of severely non-orthogonal (> 70 degrees) faces: 15.",
    )


def test_negative_volume() -> None:
    metrics = parse("negative_volume.log")

    assert metrics.zero_or_negative_volumes is True
    assert metrics.negative_volume_cells == 3
    assert metrics.failed_checks == 1
    assert metrics.min_volume is None  # not reported in this output


def test_unexpected_output_is_not_recognized_and_fields_stay_none() -> None:
    metrics = parse("unexpected.log")

    assert metrics.recognized is False
    assert metrics.mesh_ok is False
    assert metrics.cells is None
    assert metrics.max_non_orthogonality is None
    assert metrics.raw_log_path.endswith("unexpected.log")
