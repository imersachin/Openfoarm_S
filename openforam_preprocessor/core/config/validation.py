from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from core.config.models import ProjectConfig
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage


@dataclass(frozen=True)
class ConfigValidationResult:
    config: ProjectConfig | None
    issues: tuple[Issue, ...]

    @property
    def is_valid(self) -> bool:
        return self.config is not None


def validate_project_config(raw: Any) -> ConfigValidationResult:
    """Validate raw configuration data and report every problem as a structured issue."""
    try:
        return ConfigValidationResult(config=ProjectConfig.model_validate(raw), issues=())
    except ValidationError as exc:
        return ConfigValidationResult(
            config=None,
            issues=tuple(_issue_from_error(error) for error in exc.errors()),
        )


def _issue_from_error(error: Any) -> Issue:
    field_path = ".".join(str(part) for part in error["loc"]) or "<root>"
    return Issue(
        category=IssueCategory.CONFIGURATION,
        severity=IssueSeverity.BLOCKING,
        stage=IssueStage.CONFIGURATION,
        code=f"INVALID_CONFIG_{str(error['type']).upper()}",
        message=f"Invalid configuration value at '{field_path}': {error['msg']}",
        explanation="The project configuration does not satisfy the model constraints.",
        suggested_action=f"Correct '{field_path}' and validate the configuration again.",
        details={"field": field_path, "error_type": str(error["type"])},
    )
