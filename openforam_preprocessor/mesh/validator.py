from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.config.models import MeshQualityLimits
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from mesh.parser import CheckMeshMetrics


@dataclass(frozen=True)
class MeshQualityReport:
    status: str
    score: int
    issues: tuple[Issue, ...]
    metrics: CheckMeshMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "issues": [item.as_dict() for item in self.issues],
            "metrics": asdict(self.metrics),
        }


class MeshQualityValidator:
    def assess(
        self,
        metrics: CheckMeshMetrics,
        limits: MeshQualityLimits,
    ) -> MeshQualityReport:
        issues: list[Issue] = []
        score = 100
        log_reference = metrics.raw_log_path or None

        def issue(severity: IssueSeverity, code: str, message: str, action: str) -> Issue:
            return Issue(
                category=IssueCategory.MESH_QUALITY,
                severity=severity,
                stage=IssueStage.MESH_VALIDATION,
                code=code,
                message=message,
                suggested_action=action,
                log_reference=log_reference,
            )

        if not metrics.mesh_ok or metrics.failed_checks:
            issues.append(issue(
                IssueSeverity.ERROR,
                "CHECKMESH_FAILURE",
                f"checkMesh reported {metrics.failed_checks} failed mesh check(s).",
                "Inspect the failed checks in the raw checkMesh log.",
            ))
            score -= 60

        if (
            metrics.max_non_orthogonality is not None
            and metrics.max_non_orthogonality > limits.max_non_orthogonality
        ):
            issues.append(issue(
                IssueSeverity.WARNING,
                "NON_ORTHOGONALITY_LIMIT_EXCEEDED",
                f"Maximum non-orthogonality is {metrics.max_non_orthogonality:.2f}; "
                f"configured limit is {limits.max_non_orthogonality:.2f}.",
                "Review refinement and snapping settings near the affected region.",
            ))
            score -= 20

        if metrics.max_skewness is not None and metrics.max_skewness > limits.max_internal_skewness:
            issues.append(issue(
                IssueSeverity.WARNING,
                "SKEWNESS_REVIEW",
                f"Maximum parsed skewness is {metrics.max_skewness:.3f}.",
                "Inspect the raw checkMesh log and problematic regions.",
            ))
            score -= 15

        if metrics.cells is None:
            issues.append(issue(
                IssueSeverity.WARNING,
                "UNPARSED_CELL_COUNT",
                "Cell count could not be parsed.",
                "Inspect the raw checkMesh log.",
            ))
            score -= 5

        status = (
            "failed" if has_stopping_issue(issues)
            else "review" if issues
            else "passed"
        )

        return MeshQualityReport(
            status=status,
            score=max(0, score),
            issues=tuple(issues),
            metrics=metrics,
        )
