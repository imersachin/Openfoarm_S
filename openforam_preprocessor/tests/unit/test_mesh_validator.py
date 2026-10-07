from pathlib import Path

from core.config.models import MeshQualityLimits
from core.issues import IssueCategory, IssueSeverity
from mesh.parser import CheckMeshParser
from mesh.validator import (
    NOT_ASSESSED_NOTE,
    MeshQualityValidator,
    QualityStatus,
    ValidityStatus,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "checkmesh"


def assess(name: str, limits: MeshQualityLimits | None = None):
    metrics = CheckMeshParser().parse_file(FIXTURES / name)
    return MeshQualityValidator().assess(metrics, limits or MeshQualityLimits())


def codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_clean_mesh_is_valid_and_within_limits() -> None:
    report = assess("ok.log")

    assert report.status == "passed"
    assert report.validity is ValidityStatus.VALID
    assert report.quality is QualityStatus.WITHIN_LIMITS
    assert report.issues == ()


def test_failed_checks_make_mesh_invalid_with_evidence() -> None:
    report = assess("failed_checks.log")

    assert report.status == "failed"
    assert report.validity is ValidityStatus.INVALID
    issue = report.issues[0]
    assert issue.code == "CHECKMESH_FAILURE"
    assert issue.severity is IssueSeverity.ERROR
    assert issue.log_reference.endswith("failed_checks.log")
    assert len(issue.details["failed_checks"]) == 2
    # Quality is assessed separately from validity.
    assert report.quality is QualityStatus.REVIEW
    assert {"NON_ORTHOGONALITY_LIMIT_EXCEEDED", "SKEWNESS_LIMIT_EXCEEDED"} <= set(codes(report))


def test_negative_volumes_are_a_validity_error() -> None:
    report = assess("negative_volume.log")

    assert report.validity is ValidityStatus.INVALID
    assert "NEGATIVE_CELL_VOLUMES" in codes(report)
    issue = next(i for i in report.issues if i.code == "NEGATIVE_CELL_VOLUMES")
    assert issue.details["negative_volume_cells"] == 3


def test_valid_mesh_can_still_exceed_quality_limits() -> None:
    # checkMesh says OK (its own 70 degree threshold), but the configured limit is 65.
    report = assess("high_non_ortho_ok.log")

    assert report.validity is ValidityStatus.VALID
    assert report.quality is QualityStatus.REVIEW
    assert report.status == "review"
    assert codes(report) == ["NON_ORTHOGONALITY_LIMIT_EXCEEDED"]
    assert report.issues[0].severity is IssueSeverity.WARNING


def test_same_mesh_passes_with_relaxed_limits() -> None:
    report = assess("high_non_ortho_ok.log", MeshQualityLimits(max_non_orthogonality=70))

    assert report.status == "passed"
    assert report.quality is QualityStatus.WITHIN_LIMITS


def test_min_volume_below_limit_is_reported() -> None:
    report = assess("ok.log", MeshQualityLimits(min_volume=1e-5))

    assert "MIN_VOLUME_BELOW_LIMIT" in codes(report)


def test_unrecognized_output_gives_unknown_validity() -> None:
    report = assess("unexpected.log")

    assert report.validity is ValidityStatus.UNKNOWN
    assert report.quality is QualityStatus.NOT_EVALUATED
    assert report.status == "failed"
    assert report.issues[0].code == "CHECKMESH_UNRECOGNIZED_OUTPUT"
    assert report.issues[0].category is IssueCategory.MESHING


def test_report_keeps_validity_quality_suitability_and_accuracy_separate() -> None:
    data = assess("ok.log").as_dict()

    assessment = data["assessment"]
    assert assessment["mesh_validity"] == "VALID"
    assert assessment["mesh_quality"] == "WITHIN_LIMITS"
    assert assessment["simulation_suitability"] == "NOT_ASSESSED"
    assert assessment["cfd_accuracy"] == "NOT_ASSESSED"
    assert assessment["note"] == NOT_ASSESSED_NOTE
    assert "does not establish" in NOT_ASSESSED_NOTE
    assert data["limits"]["max_non_orthogonality"] == 65.0
