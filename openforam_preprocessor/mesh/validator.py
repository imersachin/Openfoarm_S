from __future__ import annotations

from dataclasses import asdict, dataclass

from core.config.models import MeshQualityLimits
from mesh.parser import CheckMeshMetrics


@dataclass(frozen=True)
class MeshFinding:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class MeshQualityReport:
    status: str
    score: int
    findings: tuple[MeshFinding, ...]
    metrics: CheckMeshMetrics

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "score": self.score,
            "findings": [asdict(item) for item in self.findings],
            "metrics": asdict(self.metrics),
        }


class MeshQualityValidator:
    def assess(
        self,
        metrics: CheckMeshMetrics,
        limits: MeshQualityLimits,
    ) -> MeshQualityReport:
        findings: list[MeshFinding] = []
        score = 100

        if not metrics.mesh_ok or metrics.failed_checks:
            findings.append(MeshFinding(
                "error",
                "CHECKMESH_FAILURE",
                f"checkMesh reported {metrics.failed_checks} failed mesh check(s).",
            ))
            score -= 60

        if (
            metrics.max_non_orthogonality is not None
            and metrics.max_non_orthogonality > limits.max_non_orthogonality
        ):
            findings.append(MeshFinding(
                "warning",
                "NON_ORTHOGONALITY_LIMIT_EXCEEDED",
                f"Maximum non-orthogonality is {metrics.max_non_orthogonality:.2f}; "
                f"configured limit is {limits.max_non_orthogonality:.2f}.",
            ))
            score -= 20

        if metrics.max_skewness is not None and metrics.max_skewness > limits.max_internal_skewness:
            findings.append(MeshFinding(
                "warning",
                "SKEWNESS_REVIEW",
                f"Maximum parsed skewness is {metrics.max_skewness:.3f}; "
                "inspect the raw checkMesh log and problematic regions.",
            ))
            score -= 15

        if metrics.cells is None:
            findings.append(MeshFinding(
                "warning",
                "UNPARSED_CELL_COUNT",
                "Cell count could not be parsed; inspect the raw checkMesh log.",
            ))
            score -= 5

        status = (
            "failed" if any(item.severity == "error" for item in findings)
            else "review" if findings
            else "passed"
        )

        return MeshQualityReport(
            status=status,
            score=max(0, score),
            findings=tuple(findings),
            metrics=metrics,
        )
