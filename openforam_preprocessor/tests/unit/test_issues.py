from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue


def make_issue(severity: IssueSeverity) -> Issue:
    return Issue(
        category=IssueCategory.GEOMETRY,
        severity=severity,
        stage=IssueStage.GEOMETRY_VALIDATION,
        code="X",
        message="m",
    )


def test_error_and_blocking_stop_downstream_work() -> None:
    assert has_stopping_issue([make_issue(IssueSeverity.ERROR)])
    assert has_stopping_issue([make_issue(IssueSeverity.BLOCKING)])
    assert not has_stopping_issue([
        make_issue(IssueSeverity.INFO),
        make_issue(IssueSeverity.WARNING),
    ])
    assert not has_stopping_issue([])


def test_issue_serializes_to_plain_values() -> None:
    data = make_issue(IssueSeverity.WARNING).as_dict()

    assert data["category"] == "GEOMETRY"
    assert data["severity"] == "WARNING"
    assert data["stage"] == "GEOMETRY_VALIDATION"
    assert set(data) == {
        "category", "severity", "stage", "code", "message", "explanation",
        "suggested_action", "artifact_reference", "log_reference", "details",
    }


def test_category_and_severity_vocabulary_matches_specification() -> None:
    assert {c.value for c in IssueCategory} == {
        "INPUT", "UNITS", "GEOMETRY", "TRANSFORMATION", "CONFIGURATION", "DEPENDENCY",
        "OPENFOAM_ENVIRONMENT", "EXECUTION", "TIMEOUT_CANCELLATION", "RESOURCE_RISK",
        "MESHING", "MESH_QUALITY", "INTERNAL",
    }
    assert {s.value for s in IssueSeverity} == {"INFO", "WARNING", "ERROR", "BLOCKING"}
