"""M2a commit 4: the resource preflight of each heavy step (E0 models, recalibrated
by measured peaks), one heavy step at a time on the machine, and the check
over one revolution in the background with coarser sweeps on a large mesh.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from machines import heavy_lock
from machines.heavy_lock import HeavyLock
from machines.msh_import import resources, revolution
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT_EXECUTABLES,
    STEP_RESOURCES,
)
from machines.pipeline import MachinePipeline
from machines.service import MachineService, _CheckCache
from mesh.estimator import SystemResources
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from vawt.runtime import RunRegistry

TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES, "moveDynamicMesh")
GB = 1e9


# --- the models ----------------------------------------------------------------------------------

def test_the_models_scale_by_cells_and_cell_types_with_a_margin() -> None:
    hexes = resources.estimate("checkMesh", 23_000_000, hex_share=0.95)
    mixed = resources.estimate("checkMesh", 23_000_000, hex_share=0.5)
    unknown = resources.estimate("checkMesh", 23_000_000)
    assert hexes.ram_bytes == pytest.approx(1.25 * 1450 * 23e6)  # E0: 31.9 GB measured
    assert mixed.ram_bytes == unknown.ram_bytes == pytest.approx(1.25 * 2100 * 23e6)
    convert = resources.estimate("convert", 0, file_bytes=636_596_399)  # SC: 2.47 GB, 7.8 s
    assert convert.ram_bytes == pytest.approx(1.25 * 5.5 * 636_596_399)
    assert convert.seconds == pytest.approx(1.25 * 0.013 * 636.6, rel=1e-3)


def test_a_measured_peak_larger_than_the_model_is_used(tmp_path: Path) -> None:
    resources.record(tmp_path, "abc", "checkMesh", int(40 * GB), 300.0)
    found = resources.estimate("checkMesh", 1_000_000, history=resources.read_history(tmp_path),
                               key="abc")
    assert found.measured and found.ram_bytes == pytest.approx(1.25 * 40 * GB)
    assert "measured on this mesh: 40.0 GB peak" in found.basis
    other = resources.estimate("checkMesh", 1_000_000,
                               history=resources.read_history(tmp_path), key="other")
    assert not other.measured


def test_the_peak_memory_of_a_running_process_is_read() -> None:
    peak = resources.peak_rss(os.getpid())
    assert peak is not None and peak > 1_000_000
    assert resources.peak_rss(2**22 + 12345) is None


# --- the heavy-step lock -----------------------------------------------------------------------

def test_the_lock_says_who_holds_it(tmp_path: Path) -> None:
    lock = HeavyLock(tmp_path / "heavy.lock")
    assert lock.acquire() is None
    lock.describe(tmp_path / "francis", "checkMesh", 3600.0)
    text = HeavyLock(tmp_path / "heavy.lock").holder_text()
    assert text is not None and text.startswith("francis: checkMesh, started ")
    assert "about 60 min left" in text
    lock.release()
    assert HeavyLock(tmp_path / "heavy.lock").holder_text() is None


def test_a_second_heavy_step_waits_and_can_be_cancelled(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heavy_lock, "POLL_S", 0.02)
    first = HeavyLock(tmp_path / "heavy.lock")
    assert first.acquire() is None
    second = HeavyLock(tmp_path / "heavy.lock")
    assert asyncio.run(second.wait(lambda: True)) is False  # cancelled while held

    told: list[str] = []
    threading.Timer(0.2, first.release).start()
    assert asyncio.run(second.wait(lambda: False, told.append)) is True
    second.release()


def test_a_lock_left_by_a_dead_process_is_taken(tmp_path: Path) -> None:
    path = tmp_path / "heavy.lock"
    path.write_text(json.dumps({"pid": 2**22 + 12345, "host": os.uname().nodename}), "utf-8")
    assert HeavyLock(path).acquire() is None


# --- through a run ------------------------------------------------------------------------------

class Setup:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 ram: float | None = None, runner: FakeMshRunner | None = None) -> None:
        monkeypatch.setenv(heavy_lock.HEAVY_LOCK_ENV, str(tmp_path / "heavy.lock"))
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)
        self.runner = runner or FakeMshRunner()
        probe = (PLENTY if ram is None else SystemResources(int(ram), PLENTY.available_disk_bytes,
                                                            PLENTY.cpu_count))
        self.service = MachineService(
            tmp_path / "project", env=self.env, system_probe=lambda _: probe,
            pipeline=MachinePipeline(self.runner, environment=self.env,
                                     system_probe=lambda _: probe),
            check_cache=_CheckCache(), revolutions=RunRegistry())
        self.root = self.service.root

    def data(self) -> dict[str, Any]:
        path = self.service.store_mesh_file("rotating_41.msh",
                                            (M1 / "rotating_41.msh").read_bytes())
        return configs.rotating(path)

    def run(self, data: dict[str, Any] | None = None) -> Any:
        self.service.save(copy.deepcopy(data or self.data()))
        config = self.service.saved_config()
        assert config is not None
        return self.service.pipeline.run_sync(self.root, config)


def test_each_heavy_step_is_estimated_before_the_run(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    assert setup.run().succeeded
    found = json.loads((setup.root / STEP_RESOURCES).read_text("utf-8"))
    assert [s["step"] for s in found["steps"]] == ["convert", "surface", "checkMesh",
                                                   "amiWeights", "views"]
    assert "1.25 margin" in found["note"]


def test_a_step_needing_more_than_the_free_ram_is_blocked(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch, ram=1000.0)  # bytes
    result = setup.run()
    assert not result.succeeded and setup.runner.calls == []
    issue = next(i for i in result.issues if i.code == "STEP_RAM_BLOCKED")
    assert issue.severity.value == "BLOCKING" and "GB free" in issue.message


def test_a_step_needing_most_of_the_free_ram_is_a_warning(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake's file: 3,190 + cells declared; the largest step is checkMesh.
    probe = Setup(tmp_path / "probe", monkeypatch)
    assert probe.run().succeeded
    found = json.loads((probe.root / STEP_RESOURCES).read_text("utf-8"))
    worst = max(s["ram_bytes"] for s in found["steps"])
    setup = Setup(tmp_path / "warn", monkeypatch, ram=worst / 0.8)
    result = setup.run()
    assert result.succeeded
    assert {i.code: i.severity.value for i in result.issues}["STEP_RAM_HIGH"] == "WARNING"


def test_the_run_waits_for_another_heavy_step_and_says_for_whom(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heavy_lock, "POLL_S", 0.02)
    setup = Setup(tmp_path, monkeypatch)
    other = HeavyLock(tmp_path / "heavy.lock")
    assert other.acquire() is None
    other.describe(tmp_path / "other_project", "checkMesh", 120.0)
    seen: list[str] = []
    original = HeavyLock.holder_text

    def watched(self: HeavyLock) -> str | None:
        found = original(self)
        if found:
            seen.append(found)
        return found

    monkeypatch.setattr(HeavyLock, "holder_text", watched)
    threading.Timer(0.3, other.release).start()
    assert setup.run().succeeded
    assert seen and seen[0].startswith("other_project: checkMesh")


def test_measured_peaks_are_kept_for_the_next_preflight(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch, runner=FakeMshRunner(report_pid=True))
    assert setup.run().succeeded
    history = resources.read_history(setup.root)
    (key, steps), = history.items()
    assert {"checkMesh", "surface"} <= set(steps)
    assert steps["checkMesh"]["peak_bytes"] > 1_000_000  # this test process's VmHWM


# --- the revolution check in the background ----------------------------------------------------

def test_coarser_sweeps_are_offered_only_for_a_long_check() -> None:
    assert revolution.sweeps(None) == (36,)
    assert revolution.sweeps(600 / 36) == (36,)
    assert revolution.sweeps(85.0) == (36, 8, 12)  # E0: 85 s a step at 23 M cells


def test_the_check_runs_in_the_background_and_reports(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    data = setup.data()
    assert setup.run(data).succeeded
    assert setup.service.revolution_sweeps()[0][0] == 36
    started = setup.service.start_revolution(8)
    assert started.started, started.issues
    active = setup.service.revolutions.latest(setup.root)
    assert active is not None and active.finished.wait(20)
    assert setup.service.revolution_state() is None
    report = revolution.read_report(setup.root)
    assert report is not None and report["when"] == "over one revolution (8 x 45 deg)"
    assert report["steps"] == 8


def test_a_large_mesh_is_offered_coarser_sweeps(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    assert setup.run().succeeded
    path = setup.root / "reports/mesh_quality_report.json"
    report = json.loads(path.read_text("utf-8"))
    report["metrics"]["cells"] = 23_000_000
    path.write_text(json.dumps(report), "utf-8")
    offered = setup.service.revolution_sweeps()
    assert [n for n, _ in offered] == [36, 8, 12]
    assert offered[0][1] == pytest.approx(36 * 1.25 * 3.7e-6 * 23e6)  # about 64 min


def test_a_running_check_can_be_cancelled(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    runner = FakeMshRunner(hold="moveDynamicMesh", gate=gate)
    setup = Setup(tmp_path, monkeypatch, runner=runner)
    assert setup.run().succeeded
    assert setup.service.start_revolution(36).started
    deadline = time.monotonic() + 10
    while setup.service.revolution_state() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    state = setup.service.revolution_state()
    assert state is not None and state["steps"] == 36
    assert setup.service.cancel_revolution()  # the fake stops when cancelled
    active = setup.service.revolutions.latest(setup.root)
    assert active is not None and active.finished.wait(20)
    gate.set()
    report = revolution.read_report(setup.root)
    assert report is not None
    assert "AMI_REVOLUTION_CANCELLED" in {i["code"] for i in report["issues"]}
