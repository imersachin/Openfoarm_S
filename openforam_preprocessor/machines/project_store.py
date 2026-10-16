"""Machine project files (decisions D1, H4): saved configuration revisions and
the configuration the meshes on disk belong to.

Kept apart from the VAWT and generic project files, as vawt.project_store:

    .preprocessor/machines/project.json        current revision
    .preprocessor/machines/history/NNNNNN.json every saved revision
    .preprocessor/machines/last_meshed.json    run ID, mesh status and configuration of
                                               the last run that produced meshes
    .preprocessor/machines/ui.json             UI settings (G7): the machine profile,
                                               reference values; never a run input
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.artifacts.hashing import sha256_json
from core.config.models import PositiveFloat
from machines.config import MachineProjectConfig
from vawt.project_store import LastMeshed, StoredConfig, _read, _write_json

CONFIG_PATH = ".preprocessor/machines/project.json"
HISTORY_DIR = ".preprocessor/machines/history"
LAST_MESHED_PATH = ".preprocessor/machines/last_meshed.json"
UI_SETTINGS_PATH = ".preprocessor/machines/ui.json"

_STRICT = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class WallReference(BaseModel):
    """Inputs of the y+ estimate only (case_setup.wall_cells)."""

    model_config = _STRICT

    length_m: PositiveFloat | None = None  # a reference length: a chord, say
    speed_m_s: PositiveFloat | None = None  # a reference speed: the blade's relative speed
    target_y_plus: PositiveFloat | None = None


class MachineUiSettings(BaseModel):
    """What the UI keeps for a project beside its configuration. Nothing here
    is a meshing or case-setup input, so none of it is in the configuration."""

    model_config = _STRICT

    profile: str | None = None  # the machine profile's id (machines.profiles)
    # Francis: the rated net head, shown as a reference value only (decision Q2).
    rated_net_head_m: PositiveFloat | None = None
    wall: WallReference = Field(default_factory=WallReference)


def read_ui_settings(root: Path) -> MachineUiSettings:
    """The project's UI settings; defaults if absent or unreadable."""
    try:
        data = _read(root / UI_SETTINGS_PATH)
        return MachineUiSettings.model_validate(data) if data else MachineUiSettings()
    except (ValueError, ValidationError):
        return MachineUiSettings()


def write_ui_settings(root: Path, settings: MachineUiSettings) -> None:
    path = root / UI_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, settings.model_dump(mode="json"))


def config_sha256(config: MachineProjectConfig) -> str:
    return sha256_json(config.model_dump(mode="json"))


class MachineProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config_path = root / CONFIG_PATH
        self.history_dir = root / HISTORY_DIR

    def exists(self) -> bool:
        return self.config_path.is_file()

    def load(self) -> StoredConfig | None:
        """The current revision, or None if nothing was saved. ValueError if the
        file is unreadable. Parse `raw` with machines.validation.parse_config."""
        data = _read(self.config_path)
        if data is None:
            return None
        try:
            return StoredConfig(int(data["revision"]), str(data["created_at"]),
                                str(data["sha256"]), dict(data["config"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{self.config_path} is not a saved configuration: {exc}") from exc

    def save(self, config: MachineProjectConfig) -> StoredConfig:
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


def write_last_meshed(root: Path, run_id: str, config: MachineProjectConfig,
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
