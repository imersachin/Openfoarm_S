from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IssueCategory(StrEnum):
    INPUT = "INPUT"
    UNITS = "UNITS"
    GEOMETRY = "GEOMETRY"
    TRANSFORMATION = "TRANSFORMATION"
    CONFIGURATION = "CONFIGURATION"
    DEPENDENCY = "DEPENDENCY"
    OPENFOAM_ENVIRONMENT = "OPENFOAM_ENVIRONMENT"
    EXECUTION = "EXECUTION"
    TIMEOUT_CANCELLATION = "TIMEOUT_CANCELLATION"
    RESOURCE_RISK = "RESOURCE_RISK"
    MESHING = "MESHING"
    MESH_QUALITY = "MESH_QUALITY"
    INTERNAL = "INTERNAL"


class IssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class IssueStage(StrEnum):
    CONFIGURATION = "CONFIGURATION"
    GEOMETRY_IMPORT = "GEOMETRY_IMPORT"
    GEOMETRY_VALIDATION = "GEOMETRY_VALIDATION"
    CASE_GENERATION = "CASE_GENERATION"
    ENVIRONMENT_CHECK = "ENVIRONMENT_CHECK"
    RESOURCE_PREFLIGHT = "RESOURCE_PREFLIGHT"
    BLOCK_MESH = "BLOCK_MESH"
    FEATURE_EXTRACTION = "FEATURE_EXTRACTION"
    SNAPPY_HEX_MESH = "SNAPPY_HEX_MESH"
    CHECK_MESH = "CHECK_MESH"
    MESH_VALIDATION = "MESH_VALIDATION"
    OPENFOAM_EXECUTION = "OPENFOAM_EXECUTION"
    VISUALIZATION = "VISUALIZATION"
    # VAWT workflow (two-mesh assembly)
    ZONE_CREATION = "ZONE_CREATION"  # topoSet
    MERGE_MESHES = "MERGE_MESHES"  # mergeMeshes
    PATCH_CREATION = "PATCH_CREATION"  # createPatch


_STOPPING_SEVERITIES = frozenset({IssueSeverity.ERROR, IssueSeverity.BLOCKING})


@dataclass(frozen=True)
class Issue:
    """A structured, UI-independent finding.

    Answers: what failed (code/message), where (stage), why (explanation),
    how severe (severity), what next (suggested_action), and where the raw
    evidence lives (artifact_reference/log_reference).
    """

    category: IssueCategory
    severity: IssueSeverity
    stage: IssueStage
    code: str
    message: str
    explanation: str = ""
    suggested_action: str = ""
    artifact_reference: str | None = None
    log_reference: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_stopping(self) -> bool:
        return self.severity in _STOPPING_SEVERITIES

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "severity": self.severity.value,
            "stage": self.stage.value,
            "code": self.code,
            "message": self.message,
            "explanation": self.explanation,
            "suggested_action": self.suggested_action,
            "artifact_reference": self.artifact_reference,
            "log_reference": self.log_reference,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Issue:
        return cls(
            category=IssueCategory(data["category"]),
            severity=IssueSeverity(data["severity"]),
            stage=IssueStage(data["stage"]),
            code=data["code"],
            message=data["message"],
            explanation=data.get("explanation", ""),
            suggested_action=data.get("suggested_action", ""),
            artifact_reference=data.get("artifact_reference"),
            log_reference=data.get("log_reference"),
            details=dict(data.get("details", {})),
        )


def has_stopping_issue(issues: Iterable[Issue]) -> bool:
    """True when any issue is ERROR or BLOCKING and downstream work must not start."""
    return any(issue.is_stopping for issue in issues)
