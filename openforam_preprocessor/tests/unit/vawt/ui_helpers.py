"""Helpers for the headless VAWT UI tests (streamlit.testing)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from app.vawt import state
from mesh.estimator import SystemResources
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_vawt import VAWT_TOOLS, FakeVawtRunner
from tests.fixtures.vawt.drafts import preset_draft
from vawt.pipeline import VawtPipeline
from vawt.runtime import RunRegistry
from vawt.service import VawtService, _GeometryCache
from vawt.workspace import read_mounts

APP = Path(__file__).resolve().parents[3] / "app" / "vawt_app.py"
SECTION_KEYS = ("project", "geometry", "rotating_zone", "domain", "refinement", "layers",
                "review", "run", "mesh", "export", "history", "logs")


class UiProject:
    """A project folder plus the service the app gets for it (fake OpenFOAM)."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "projects" / "rotor"
        self.env = {**openfoam_env(tmp_path, "v2512", VAWT_TOOLS),
                    "VAWT_PROJECTS_DIR": str(tmp_path / "projects")}
        self.registry = RunRegistry()
        self.cache = _GeometryCache()
        self.runner: FakeVawtRunner = FakeVawtRunner()
        self.resources: SystemResources = PLENTY
        self.draft = preset_draft(tmp_path)
        monkeypatch.setattr(state, "SERVICE_FACTORY", self.make_service)
        for key, value in self.env.items():
            monkeypatch.setenv(key, value)
        state._service.clear()

    def make_service(self, root: Path) -> VawtService:
        pipeline = VawtPipeline(self.runner, environment=self.env,
                                system_probe=lambda _: self.resources)
        return VawtService(root, pipeline=pipeline, registry=self.registry, env=self.env,
                           mounts=read_mounts("/dev/sdd / ext4 rw 0 0\n"),
                           system_probe=lambda _: self.resources, geometry_cache=self.cache)

    def service(self) -> VawtService:
        return self.make_service(self.root)

    def save(self, raw: dict[str, Any] | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        assert self.service().save(raw or self.draft).saved


def open_app(project: UiProject) -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.text_input(key="vawt_folder").set_value(str(project.root)).run()
    app.button(key="vawt_open_folder").click().run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def go(app: AppTest, section: str) -> AppTest:
    app.button(key=f"nav-{section}").click().run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def texts(app: AppTest) -> str:
    """Every text the page shows (markdown, captions, alerts, labels, values)."""
    parts: list[str] = []
    for kind in ("markdown", "caption", "error", "warning", "info", "success", "subheader",
                 "title", "code", "metric"):
        for element in getattr(app, kind):
            parts.append(str(getattr(element, "value", "")))
            parts.append(str(getattr(element, "label", "")))
    for kind in ("button", "selectbox", "text_input", "number_input", "checkbox"):
        for element in getattr(app, kind):
            parts.append(str(element.label))
            parts.append(json.dumps(getattr(element, "options", []), default=str))
    return "\n".join(parts)


def nav_label(app: AppTest, section: str) -> str:
    return str(app.button(key=f"nav-{section}").label)


@pytest.fixture
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UiProject]:
    project = UiProject(tmp_path, monkeypatch)
    yield project
    project.registry.shutdown(timeout=10)
    state._service.clear()
