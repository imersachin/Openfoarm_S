"""VAWT project files: saved configuration revisions and the configuration the
meshes on disk belong to (spec sections 5 and 12).

Kept apart from the generic workflow's .preprocessor/project.json, which holds
a different model:

    .preprocessor/vawt/project.json        current revision
    .preprocessor/vawt/history/NNNNNN.json every saved revision
    .preprocessor/vawt/last_meshed.json    run ID, validity, configuration of the
                                           last run that produced meshes
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.artifacts.hashing import sha256_json
from core.config.manager import ConfigurationManager
from vawt.config import VawtProjectConfig

CONFIG_PATH = ".preprocessor/vawt/project.json"
HISTORY_DIR = ".preprocessor/vawt/history"
LAST_MESHED_PATH = ".preprocessor/vawt/last_meshed.json"

_write_json = ConfigurationManager._atomic_json_write  # shared atomic write


@dataclass(frozen=True)
class StoredConfig:
    revision: int
    created_at: str
    sha256: str
    raw: dict[str, Any]  # parse with vawt.validation.parse_config (migrations apply)


@dataclass(frozen=True)
class LastMeshed:
    run_id: str
    finished_at: str
    mesh_status: str  # the mesh report status of that run
    raw: dict[str, Any]


def config_sha256(config: VawtProjectConfig) -> str:
    return sha256_json(config.model_dump(mode="json"))


def _read(path: Path) -> dict[str, Any] | None:
    """The JSON object in path; None if absent. Unreadable or malformed: ValueError."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object.")
    return data


class VawtProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config_path = root / CONFIG_PATH
        self.history_dir = root / HISTORY_DIR

    def exists(self) -> bool:
        return self.config_path.is_file()

    def load(self) -> StoredConfig | None:
        """The current revision, or None if nothing was saved. ValueError if the
        file is unreadable."""
        data = _read(self.config_path)
        if data is None:
            return None
        try:
            return StoredConfig(int(data["revision"]), str(data["created_at"]),
                                str(data["sha256"]), dict(data["config"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{self.config_path} is not a saved configuration: {exc}") from exc

    def save(self, config: VawtProjectConfig) -> StoredConfig:
        previous = self.load()
        stored = StoredConfig(
            revision=1 if previous is None else previous.revision + 1,
            created_at=datetime.now(UTC).isoformat(),
            sha256=config_sha256(config),
            raw=config.model_dump(mode="json"),
        )
        payload = {"revision": stored.revision, "created_at": stored.created_at,
                   "sha256": stored.sha256, "config": stored.raw}
        self.history_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.history_dir / f"{stored.revision:06d}.json", payload)
        _write_json(self.config_path, payload)
        return stored


def write_last_meshed(root: Path, run_id: str, config: VawtProjectConfig,
                      mesh_status: str) -> None:
    path = root / LAST_MESHED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"run_id": run_id, "finished_at": datetime.now(UTC).isoformat(),
                       "mesh_status": mesh_status, "config": config.model_dump(mode="json")})


def read_last_meshed(root: Path) -> LastMeshed | None:
    """None if no run has produced meshes, or the record is unreadable."""
    try:
        data = _read(root / LAST_MESHED_PATH)
        if data is None:
            return None
        return LastMeshed(str(data["run_id"]), str(data["finished_at"]),
                          str(data["mesh_status"]), dict(data["config"]))
    except (KeyError, TypeError, ValueError):
        return None
