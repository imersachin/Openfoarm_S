"""V5: spec section 14.2 budgets, asserted on work done (parses, file reads,
commands) with generous time margins; measured times are printed (-s)."""

from __future__ import annotations

import builtins
import io
import time
from pathlib import Path
from typing import Any

import pytest
import trimesh

from app.vawt.sections import common
from tests.fixtures.vawt.rotor import rotor_mesh
from tests.unit.vawt.test_vawt_service import GatedRunner
from tests.unit.vawt.ui_helpers import (
    SECTION_KEYS,
    UiProject,
    go,
    open_app,
)
from vawt.config import Axis
from vawt.service import LOG_TAIL_BYTES, RunView, VawtService


def big_rotor(path: Path, faces: int = 100_000) -> Path:
    mesh = rotor_mesh()
    vertices, triangles = mesh.vertices, mesh.faces
    while len(triangles) < faces:
        vertices, triangles = trimesh.remesh.subdivide(vertices, triangles)
    trimesh.Trimesh(vertices, triangles, process=False).export(path)
    return path


def wait_until(condition: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.005)


def test_apply_on_a_setup_section_with_a_100k_triangle_rotor(ui: UiProject) -> None:
    stl = big_rotor(ui.tmp / "big.stl")
    assert len(trimesh.load(stl).faces) >= 100_000
    base = {"project_name": "Big", "geometry": {"source_path": str(stl), "source_units": "m"}}
    ui.root.mkdir(parents=True)
    draft, _ = ui.service().draft_from_preset(base, Axis.Z, Axis.X)
    assert draft is not None
    ui.save(draft)
    app = go(open_app(ui), "rotating_zone")
    loads = ui.cache.loads

    diameter = next(n for n in app.number_input if n.label == "Diameter (m)")
    diameter.set_value(float(diameter.value) * 1.01)
    clock = time.perf_counter()
    next(b for b in app.button if b.label == "Apply").click().run()
    seconds = time.perf_counter() - clock

    print(f"\nApply (100k triangles), whole page incl. rerun: {seconds * 1000:.0f} ms")
    assert not app.exception
    assert ui.cache.loads == loads  # the STL is not parsed again
    assert seconds < 2.0  # generous; the target is 200 ms of server time


class OpenedFiles:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[str] = []
        for module in (builtins, io):
            real = module.open

            def spy(file: Any, *args: Any, _real: Any = real, **kwargs: Any) -> Any:
                self.paths.append(str(file))
                return _real(file, *args, **kwargs)

            monkeypatch.setattr(module, "open", spy)

    def geometry_or_mesh(self) -> list[str]:
        return [p for p in self.paths if p.lower().endswith(".stl")
                or "/constant/polyMesh/" in p.replace("\\", "/")]


def test_switching_section_reads_no_geometry_or_mesh_file(
        ui: UiProject, monkeypatch: pytest.MonkeyPatch) -> None:
    ui.save()
    app = go(open_app(ui), "run")
    app.button(key="vawt_start").click().run()
    wait_until(lambda: ui.service().run_status().state is RunView.SUCCEEDED)
    app.run()
    for key in SECTION_KEYS:  # first visits may build what each section shows
        go(app, key)

    opened = OpenedFiles(monkeypatch)
    for key in SECTION_KEYS:
        go(app, key)

    assert opened.geometry_or_mesh() == []


def test_upload_to_metrics_parses_the_stl_once(ui: UiProject) -> None:
    app = go(open_app(ui), "geometry")
    # What an upload does (the upload widget is not scriptable headless).
    stored = ui.service().store_source_file(
        "rotor.stl", Path(ui.draft["geometry"]["source_path"]).read_bytes())
    app.session_state["vawt_draft"]["geometry"]["source_path"] = str(stored)
    app.session_state["draft_generation"] += 1
    app.run()
    next(s for s in app.selectbox if s.label == "Units of the STL file").set_value("m")
    next(b for b in app.button if b.label == "Apply").click().run()
    assert "Rotor metrics" in "\n".join(str(m.value) for m in app.markdown)
    go(app, "rotating_zone")
    go(app, "geometry")

    assert ui.cache.loads == 1


def test_start_to_first_status_under_one_second(ui: UiProject) -> None:
    ui.runner = GatedRunner(hold="blockMesh")
    ui.save()
    app = go(open_app(ui), "run")
    service = ui.service()

    clock = time.perf_counter()
    app.button(key="vawt_start").click().run()
    wait_until(lambda: service.run_status().stage is not None, timeout=1.0)
    seconds = time.perf_counter() - clock

    print(f"\nStart to first status: {seconds * 1000:.0f} ms")
    assert seconds < 1.0
    ui.runner.release.set()  # type: ignore[attr-defined]


def test_status_refresh_interval_and_bounded_log_reads(
        ui: UiProject, monkeypatch: pytest.MonkeyPatch) -> None:
    assert 1.0 <= common.REFRESH_S <= 2.0
    ui.save()
    log = ui.root / "logs" / "outer" / "03_snappyHexMesh.log"
    log.parent.mkdir(parents=True)
    log.write_text("x" * 99 + "\n" * 1, encoding="utf-8")
    log.write_text(("y" * 99 + "\n") * 20_000, encoding="utf-8")  # 2 MB
    reads: list[int] = []
    original = VawtService.log_tail

    def spy(self: VawtService, *args: Any, **kwargs: Any) -> Any:
        tail = original(self, *args, **kwargs)
        reads.append(tail.bytes_read)
        return tail

    monkeypatch.setattr(VawtService, "log_tail", spy)

    go(open_app(ui), "run")
    go(open_app(ui), "logs")

    assert reads and max(reads) <= LOG_TAIL_BYTES


def test_run_with_nothing_changed_starts_no_command(ui: UiProject) -> None:
    ui.save()
    app = go(open_app(ui), "run")
    app.button(key="vawt_start").click().run()
    wait_until(lambda: ui.service().run_status().state is RunView.SUCCEEDED)
    app.run()
    commands = len(ui.runner.calls)

    app.button(key="vawt_start").click().run()
    wait_until(lambda: ui.service().run_status().state is RunView.SUCCEEDED
               and ui.service().runs()[0]["run_id"] != ui.service().runs()[-1]["run_id"])

    assert commands > 0 and len(ui.runner.calls) == commands
