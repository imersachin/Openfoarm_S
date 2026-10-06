from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config.models import ProjectConfig
from core.workflow.dependency_graph import DependencyGraph, PipelineOperation


@dataclass(frozen=True)
class ConfigSnapshot:
    revision: int
    created_at: datetime
    config: ProjectConfig
    sha256: str


@dataclass(frozen=True)
class ChangeSet:
    changed_paths: frozenset[str]
    affected_operations: frozenset[PipelineOperation]

    @property
    def is_empty(self) -> bool:
        return not self.changed_paths


class ConfigurationManager:
    def __init__(self, project_root: Path, graph: DependencyGraph) -> None:
        self.project_root = project_root
        self.graph = graph
        self.config_path = project_root / ".preprocessor" / "project.json"
        self.history_dir = project_root / ".preprocessor" / "history"

    @staticmethod
    def _canonical_json(config: ProjectConfig) -> str:
        return json.dumps(
            config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _digest(cls, config: ProjectConfig) -> str:
        return hashlib.sha256(cls._canonical_json(config).encode()).hexdigest()

    def load_project(self) -> ConfigSnapshot:
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        config = ProjectConfig.model_validate(raw["config"])
        return ConfigSnapshot(
            revision=raw["revision"],
            created_at=datetime.fromisoformat(raw["created_at"]),
            config=config,
            sha256=raw["sha256"],
        )

    def save_project(self, config: ProjectConfig) -> ConfigSnapshot:
        previous = self.load_project() if self.config_path.exists() else None
        revision = 1 if previous is None else previous.revision + 1

        snapshot = ConfigSnapshot(
            revision=revision,
            created_at=datetime.now(UTC),
            config=config,
            sha256=self._digest(config),
        )

        payload = {
            "revision": snapshot.revision,
            "created_at": snapshot.created_at.isoformat(),
            "sha256": snapshot.sha256,
            "config": config.model_dump(mode="json"),
        }

        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json_write(self.config_path, payload)
        self._atomic_json_write(
            self.history_dir / f"{snapshot.revision:06d}.json",
            payload,
        )
        return snapshot

    def detect_changes(
        self,
        old: ProjectConfig,
        new: ProjectConfig,
    ) -> ChangeSet:
        changed = frozenset(
            self._diff_values(old.model_dump(mode="json"), new.model_dump(mode="json"))
        )
        return ChangeSet(
            changed_paths=changed,
            affected_operations=self.graph.operations_for(changed),
        )

    @staticmethod
    def _diff_values(
        old: Any,
        new: Any,
        prefix: str = "",
    ) -> set[str]:
        if type(old) is not type(new):
            return {prefix.rstrip(".")}

        if isinstance(old, dict):
            result: set[str] = set()
            for key in old.keys() | new.keys():
                path = f"{prefix}{key}"
                if key not in old or key not in new:
                    result.add(path)
                else:
                    result |= ConfigurationManager._diff_values(
                        old[key], new[key], f"{path}."
                    )
            return result

        return set() if old == new else {prefix.rstrip(".")}

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
