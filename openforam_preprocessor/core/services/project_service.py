"""Application service: the only backend entry point the UI uses.

The UI collects configuration and displays results. Validation, dependency
planning, OpenFOAM sequencing and artifact handling all stay behind this API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config.manager import ConfigurationManager
from core.config.models import (
    BoundaryLayerConfig,
    MeshQualityLimits,
    ProjectConfig,
    SnappyQualityControls,
    SurfaceRefinementConfig,
)
from core.config.validation import validate_project_config
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from core.workflow.dependency_graph import DependencyGraph
from core.workflow.pipeline import GEOMETRY_REPORT, PREFLIGHT_REPORT, MeshPipeline, PipelineResult
from core.workflow.planner import ExecutionPlan, ExecutionPlanner
from mesh.estimator import ResourceAssessment
from mesh.generator import OpenFOAMMeshCaseGenerator

MESH_REPORT = "reports/mesh_quality_report.json"
INPUTS_DIR = "inputs"
MAX_LOG_CHARS = 200_000


@dataclass(frozen=True)
class LoadResult:
    raw: dict[str, Any] | None  # stored configuration, even if invalid
    config: ProjectConfig | None
    revision: int | None
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class SaveResult:
    config: ProjectConfig | None
    revision: int | None
    issues: tuple[Issue, ...]

    @property
    def saved(self) -> bool:
        return self.config is not None


@dataclass(frozen=True)
class LogFile:
    name: str
    size_bytes: int


def new_project_template() -> dict[str, Any]:
    """Draft for a new project. Values without a safe default are left as None."""
    unset = {"x": None, "y": None, "z": None}
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "schema_version": 1,
        "project_name": "",
        "openfoam_profile": "openfoam_com",
        "geometry": {
            "source_path": "",
            "source_units": None,  # never assumed: STL has no reliable units
            "scale": 1.0,
            "rotation_deg": dict(zero),
            "translation": dict(zero),
            "patch_name": "geometry",
        },
        "mesh": {
            "background": {
                "domain": {"minimum": dict(unset), "maximum": dict(unset)},
                "base_cell_size": 0.1,
                "max_cells_per_axis": 1000,
                "expansion_ratio": 1.0,
            },
            "surface": SurfaceRefinementConfig().model_dump(mode="json"),
            "layers": BoundaryLayerConfig().model_dump(mode="json"),
            "quality": MeshQualityLimits().model_dump(mode="json"),
            "snappy_quality": SnappyQualityControls().model_dump(mode="json"),
            "location_in_mesh": dict(unset),
            "max_global_cells": 2_000_000,
            "overwrite_existing_mesh": True,
        },
    }


def _project_issue(code: str, message: str, action: str, **details: Any) -> Issue:
    return Issue(
        category=IssueCategory.CONFIGURATION,
        severity=IssueSeverity.BLOCKING,
        stage=IssueStage.CONFIGURATION,
        code=code,
        message=message,
        suggested_action=action,
        details=details,
    )


class ProjectService:
    def __init__(self, root: Path, pipeline: MeshPipeline | None = None) -> None:
        self.root = root
        self.manager = ConfigurationManager(root, DependencyGraph())
        self.pipeline = pipeline or MeshPipeline()
        self.planner = ExecutionPlanner()

    # --- configuration ------------------------------------------------------

    def exists(self) -> bool:
        return self.manager.config_path.is_file()

    def load(self) -> LoadResult:
        """Load the saved configuration; problems become issues, never exceptions."""
        if not self.exists():
            return LoadResult(None, None, None, ())
        try:
            payload = json.loads(self.manager.config_path.read_text(encoding="utf-8"))
            raw, revision = payload["config"], int(payload["revision"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return LoadResult(None, None, None, (_project_issue(
                "PROJECT_FILE_UNREADABLE",
                f"The project file {self.manager.config_path} could not be read.",
                "Restore the file from .preprocessor/history or create a new project.",
                exception=str(exc),
            ),))
        result = validate_project_config(raw)
        return LoadResult(raw, result.config, revision, result.issues)

    @staticmethod
    def validate(raw: dict[str, Any]):
        return validate_project_config(raw)

    def save(self, raw: dict[str, Any]) -> SaveResult:
        result = validate_project_config(raw)
        if result.config is None:
            return SaveResult(None, None, result.issues)
        snapshot = self.manager.save_project(result.config)
        return SaveResult(snapshot.config, snapshot.revision, ())

    def preview_changes(self, raw: dict[str, Any]) -> ExecutionPlan | None:
        """What re-runs if this draft is applied, and why. None if the draft is invalid."""
        new = validate_project_config(raw).config
        if new is None:
            return None
        saved = self.load().config
        if saved is None:
            return self.planner.plan_full(new)
        return self.planner.plan(self.manager.detect_changes(saved, new), new)

    @staticmethod
    def background_cells(raw: dict[str, Any]) -> tuple[tuple[int, int, int],
                                                        tuple[int, int, int]] | None:
        """(requested, effective) background cells per axis, or None if invalid."""
        config = validate_project_config(raw).config
        if config is None:
            return None
        cells = OpenFOAMMeshCaseGenerator.background_cells(config)
        return cells.requested, cells.effective

    def store_source_file(self, filename: str, data: bytes) -> Path:
        """Copy an uploaded STL into the project; returns its absolute path."""
        name = Path(filename).name
        if Path(name).suffix.lower() != ".stl" or not Path(name).stem:
            raise ValueError("Only .stl files can be added as geometry.")
        target = (self.root / INPUTS_DIR / name).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    # --- operations ---------------------------------------------------------

    def prepare_case(self, config: ProjectConfig) -> PipelineResult:
        return self.pipeline.prepare_case(self.root, config)

    def preflight(
        self, config: ProjectConfig
    ) -> tuple[PipelineResult, ResourceAssessment | None, tuple[Issue, ...]]:
        prepared = self.prepare_case(config)
        if not prepared.succeeded:
            return prepared, None, ()
        assessment, issues = self.pipeline.preflight(self.root, config)
        return prepared, assessment, issues

    def generate_mesh(
        self, config: ProjectConfig, *, force: bool = False,
        allow_high_resource_risk: bool = False,
    ) -> PipelineResult:
        return self.pipeline.generate_mesh_sync(
            self.root, config, force=force, allow_high_resource_risk=allow_high_resource_risk
        )

    # --- results --------------------------------------------------------------

    def _read_json(self, relative: str) -> dict[str, Any] | None:
        try:
            data = json.loads((self.root / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def reports(self) -> dict[str, dict[str, Any] | None]:
        return {
            "geometry": self._read_json(GEOMETRY_REPORT),
            "preflight": self._read_json(PREFLIGHT_REPORT),
            "mesh_quality": self._read_json(MESH_REPORT),
        }

    def logs(self) -> list[LogFile]:
        directory = self.root / "logs"
        if not directory.is_dir():
            return []
        return [
            LogFile(path.name, path.stat().st_size)
            for path in sorted(directory.glob("*.log")) if path.is_file()
        ]

    def read_log(self, name: str, max_chars: int = MAX_LOG_CHARS) -> str:
        """Tail of a log listed by logs(); other names are rejected."""
        if name not in {log.name for log in self.logs()}:
            raise ValueError(f"Unknown log file: {name}")
        text = (self.root / "logs" / name).read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]

    # --- visualization (lazy: plotly is needed only for display) ---------------

    def geometry_view(self, config: ProjectConfig) -> Any:
        from visualization.views import geometry_view

        return geometry_view(self.root, config)

    def mesh_view(self) -> Any:
        from visualization.views import mesh_view

        return mesh_view(self.root)

    def quality_chart(self) -> Any:
        from visualization.views import View, quality_chart

        report = self._read_json(MESH_REPORT)
        return quality_chart(report) if report else View(None)

    def dictionaries(self) -> dict[str, str]:
        """Generated system/ dictionaries, for advanced inspection."""
        directory = self.root / "system"
        if not directory.is_dir():
            return {}
        return {
            path.name: path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(directory.iterdir()) if path.is_file()
        }
