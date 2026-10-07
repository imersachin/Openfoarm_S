from core.config.models import MeshQualityLimits
from core.issues import IssueCategory, IssueSeverity
from mesh.parser import CheckMeshMetrics
from mesh.validator import MeshQualityValidator


def test_clean_mesh_passes_without_issues() -> None:
    metrics = CheckMeshMetrics(cells=100, max_non_orthogonality=30.0, mesh_ok=True)

    report = MeshQualityValidator().assess(metrics, MeshQualityLimits())

    assert report.status == "passed"
    assert report.issues == ()


def test_failed_checks_produce_structured_error_with_log_reference() -> None:
    metrics = CheckMeshMetrics(
        cells=100, failed_checks=2, mesh_ok=False, raw_log_path="logs/04_checkMesh.log"
    )

    report = MeshQualityValidator().assess(metrics, MeshQualityLimits())

    assert report.status == "failed"
    issue = report.issues[0]
    assert issue.code == "CHECKMESH_FAILURE"
    assert issue.category is IssueCategory.MESH_QUALITY
    assert issue.severity is IssueSeverity.ERROR
    assert issue.log_reference == "logs/04_checkMesh.log"


def test_non_orthogonality_over_limit_is_review_not_failure() -> None:
    metrics = CheckMeshMetrics(cells=100, max_non_orthogonality=80.0, mesh_ok=True)

    report = MeshQualityValidator().assess(metrics, MeshQualityLimits())

    assert report.status == "review"
    assert [i.code for i in report.issues] == ["NON_ORTHOGONALITY_LIMIT_EXCEEDED"]
    assert report.issues[0].severity is IssueSeverity.WARNING
