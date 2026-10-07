from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from core.config.models import MeshQualityLimits
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from mesh.parser import CheckMeshMetrics

NOT_ASSESSED_NOTE = (
    "This report assesses mesh validity and mesh quality only. A valid, "
    "high-quality mesh does not establish simulation suitability or CFD "
    "accuracy, which also depend on physics, boundary conditions, numerical "
    "schemes, convergence, mesh independence and validation against reference data."
)


class ValidityStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"  # checkMesh output could not be interpreted


class QualityStatus(StrEnum):
    WITHIN_LIMITS = "WITHIN_LIMITS"
    REVIEW = "REVIEW"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class MeshQualityReport:
    status: str  # overall: passed | review | failed
    score: int  # heuristic summary only; see the separate assessments
    validity: ValidityStatus
    quality: QualityStatus
    issues: tuple[Issue, ...]
    metrics: CheckMeshMetrics
    limits: MeshQualityLimits

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "assessment": {
                "mesh_validity": self.validity.value,
                "mesh_quality": self.quality.value,
                "simulation_suitability": "NOT_ASSESSED",
                "cfd_accuracy": "NOT_ASSESSED",
                "note": NOT_ASSESSED_NOTE,
            },
            "limits": self.limits.model_dump(mode="json"),
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

        def issue(
            severity: IssueSeverity, code: str, message: str, action: str,
            category: IssueCategory = IssueCategory.MESH_QUALITY, **details: Any,
        ) -> Issue:
            return Issue(
                category=category,
                severity=severity,
                stage=IssueStage.MESH_VALIDATION,
                code=code,
                message=message,
                suggested_action=action,
                log_reference=log_reference,
                details=details,
            )

        # --- validity: did checkMesh accept the mesh? ----------------------
        if not metrics.recognized:
            validity = ValidityStatus.UNKNOWN
            issues.append(issue(
                IssueSeverity.ERROR,
                "CHECKMESH_UNRECOGNIZED_OUTPUT",
                "checkMesh output was not recognized; mesh validity cannot be determined.",
                "Inspect the raw checkMesh log; the OpenFOAM version may format output "
                "differently.",
                category=IssueCategory.MESHING,
            ))
            score -= 60
        elif not metrics.mesh_ok or metrics.failed_checks:
            validity = ValidityStatus.INVALID
            issues.append(issue(
                IssueSeverity.ERROR,
                "CHECKMESH_FAILURE",
                f"checkMesh reported {metrics.failed_checks} failed mesh check(s).",
                "Inspect the failed checks in the raw checkMesh log.",
                failed_checks=list(metrics.failed_check_messages),
            ))
            score -= 60
        else:
            validity = ValidityStatus.VALID

        if metrics.zero_or_negative_volumes or metrics.negative_volume_cells:
            validity = ValidityStatus.INVALID
            issues.append(issue(
                IssueSeverity.ERROR,
                "NEGATIVE_CELL_VOLUMES",
                "checkMesh detected zero or negative cell volumes"
                + (f" ({metrics.negative_volume_cells} cell(s))."
                   if metrics.negative_volume_cells else "."),
                "Inverted cells are unusable; review snapping and layer settings.",
                negative_volume_cells=metrics.negative_volume_cells,
            ))

        # --- quality: configured acceptance limits -------------------------
        evaluated = False
        if metrics.max_non_orthogonality is not None:
            evaluated = True
            if metrics.max_non_orthogonality > limits.max_non_orthogonality:
                issues.append(issue(
                    IssueSeverity.WARNING,
                    "NON_ORTHOGONALITY_LIMIT_EXCEEDED",
                    f"Maximum non-orthogonality is {metrics.max_non_orthogonality:.2f}; "
                    f"configured limit is {limits.max_non_orthogonality:.2f}.",
                    "Review refinement and snapping settings near the affected region.",
                    severe_faces=metrics.severely_non_orthogonal_faces,
                ))
                score -= 20

        if metrics.max_skewness is not None:
            evaluated = True
            if metrics.max_skewness > limits.max_internal_skewness:
                issues.append(issue(
                    IssueSeverity.WARNING,
                    "SKEWNESS_LIMIT_EXCEEDED",
                    f"Maximum skewness is {metrics.max_skewness:.3f}; configured limit is "
                    f"{limits.max_internal_skewness:.3f}.",
                    "Inspect the raw checkMesh log and problematic regions.",
                ))
                score -= 15

        if metrics.min_volume is not None:
            evaluated = True
            if metrics.min_volume < limits.min_volume:
                issues.append(issue(
                    IssueSeverity.WARNING,
                    "MIN_VOLUME_BELOW_LIMIT",
                    f"Minimum cell volume is {metrics.min_volume:g}; configured limit is "
                    f"{limits.min_volume:g}.",
                    "Review refinement levels and snapping near small features.",
                ))
                score -= 10

        if metrics.cells is None:
            issues.append(issue(
                IssueSeverity.WARNING,
                "UNPARSED_CELL_COUNT",
                "Cell count could not be parsed.",
                "Inspect the raw checkMesh log.",
            ))
            score -= 5

        quality_findings = {
            "NON_ORTHOGONALITY_LIMIT_EXCEEDED", "SKEWNESS_LIMIT_EXCEEDED", "MIN_VOLUME_BELOW_LIMIT",
        }
        quality = (
            QualityStatus.NOT_EVALUATED if not evaluated
            else QualityStatus.REVIEW if any(i.code in quality_findings for i in issues)
            else QualityStatus.WITHIN_LIMITS
        )

        status = (
            "failed" if has_stopping_issue(issues)
            else "review" if issues
            else "passed"
        )

        return MeshQualityReport(
            status=status,
            score=max(0, score),
            validity=validity,
            quality=quality,
            issues=tuple(issues),
            metrics=metrics,
            limits=limits,
        )
