"""The headless machines UI (streamlit.testing) with a fake OpenFOAM."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from app.machines_ui import preview, state
from machines.operations import MACHINE_EXECUTABLES
from machines.pipeline import MachinePipeline
from machines.profiles import ProfileRegistry, default_registry
from machines.service import MachineService, _CheckCache
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from vawt.runtime import RunRegistry
from vawt.workspace import read_mounts

APP = Path(__file__).resolve().parents[3] / "app" / "machines_app.py"


class MachinesUi:
    """A projects folder plus the service the app gets for a project."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.projects = tmp_path / "projects"
        self.env = {**openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES),
                    "MACHINES_PROJECTS_DIR": str(self.projects)}
        self.registry = RunRegistry()
        self.checks = _CheckCache()
        self.profiles: ProfileRegistry = default_registry()
        monkeypatch.setattr(state, "SERVICE_FACTORY", self.make_service)
        monkeypatch.setattr(state, "REGISTRY_FACTORY", lambda: self.profiles)
        for key, value in self.env.items():
            monkeypatch.setenv(key, value)
        state._service.clear()
        preview._figure.clear()

    def make_service(self, root: Path) -> MachineService:
        pipeline = MachinePipeline(FakeMachineRunner(), environment=self.env,
                                   system_probe=lambda _: PLENTY)
        return MachineService(root, pipeline=pipeline, registry=self.registry, env=self.env,
                              mounts=read_mounts("/dev/sdd / ext4 rw 0 0\n"),
                              system_probe=lambda _: PLENTY, check_cache=self.checks,
                              profiles=self.profiles)

    def service(self, name: str) -> MachineService:
        return self.make_service(self.projects / name)


def open_app(ui: MachinesUi) -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=120)
    app.run()
    ok(app)
    return app


def ok(app: AppTest) -> AppTest:
    assert not app.exception, [e.value for e in app.exception]
    return app


def create(app: AppTest, profile_id: str, name: str = "demo") -> AppTest:
    app.radio(key="machines_landing_machine").set_value(profile_id).run()
    app.text_input(key="machines_new_name").set_value(name).run()
    return ok(app.button(key="machines_create").click().run())


def go(app: AppTest, section: str) -> AppTest:
    return ok(app.button(key=f"nav-{section}").click().run())


def nav(app: AppTest) -> list[str]:
    """The navigation labels without their status marks."""
    return [str(b.label).split("  ", 1)[-1].strip() for b in app.button
            if b.key and str(b.key).startswith("nav-")]


def texts(app: AppTest) -> str:
    parts: list[str] = []
    for kind in ("markdown", "caption", "error", "warning", "info", "success", "subheader",
                 "title"):
        parts += [str(getattr(e, "value", "")) for e in getattr(app, kind)]
    parts += [str(e.label) for e in app.expander]
    return "\n".join(parts)


def charts(app: AppTest) -> list[Any]:
    return list(app.get("plotly_chart"))


def write_profile(directory: Path, data: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{data['id']}.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MachinesUi]:
    project = MachinesUi(tmp_path, monkeypatch)
    yield project
    project.registry.shutdown(timeout=10)
    state._service.clear()
    preview._figure.clear()
