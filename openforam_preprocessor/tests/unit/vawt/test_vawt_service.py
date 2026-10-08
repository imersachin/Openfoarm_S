"""V4: VawtService (spec section 12) with a fake OpenFOAM.

Runs go to a background thread; these tests drive them through the service
the way a UI would: start, poll the status, re-attach from a new service
instance (a refreshed page), cancel, and recover from an interrupted run.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import psutil
import pytest

from core.workflow.run_lock import LOCK_PATH, process_create_time
from openfoam.runner import CommandResult
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_vawt import VAWT_TOOLS, FakeVawtRunner
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import Axis
from vawt.operations import VawtOperation
from vawt.pipeline import VawtPipeline
from vawt.runtime import RunRegistry
from vawt.service import (
    SETUP_SECTIONS,
    RunView,
    SectionState,
    VawtService,
    _GeometryCache,
    section_of_field,
)
from vawt.status import STATUS_PATH, RunState, write_status
from vawt.workspace import read_mounts

Op = VawtOperation
REPO = Path(__file__).resolve().parents[3]
NO_MOUNTS = read_mounts("/dev/sdd / ext4 rw 0 0\n")


class GatedRunner(FakeVawtRunner):
    """Holds the named command until `release` is set or the run is cancelled."""

    def __init__(self, hold: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.hold, self.release = hold, threading.Event()
        self.holding = threading.Event()

    async def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        if argv[0] == self.hold:
            on_start = kwargs.get("on_start")
            if on_start is not None and self.pid is not None:
                on_start(self.pid)  # the held command counts as started
            self.holding.set()
            event = kwargs.get("cancel_event")
            while not self.release.is_set() and not (event is not None and event.is_set()):
                await asyncio.sleep(0.01)
        return await super().run(argv, **kwargs)


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "project"
        self.env = {**openfoam_env(tmp_path, "v2512", VAWT_TOOLS),
                    "VAWT_PROJECTS_DIR": str(tmp_path / "projects")}
        self.registry = RunRegistry()
        self.cache = _GeometryCache()
        self.draft = preset_draft(tmp_path)

    def service(self, runner: FakeVawtRunner | None = None, **kwargs: Any) -> VawtService:
        pipeline = VawtPipeline(runner or FakeVawtRunner(), environment=self.env,
                                system_probe=lambda _: PLENTY)
        return VawtService(self.root, pipeline=pipeline, registry=self.registry, env=self.env,
                           mounts=NO_MOUNTS, system_probe=lambda _: PLENTY,
                           geometry_cache=self.cache, **kwargs)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[Setup]:
    s = Setup(tmp_path)
    yield s
    s.registry.shutdown(timeout=10)


def wait_for(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def finished(service: VawtService) -> Callable[[], bool]:
    return lambda: service.run_status().state not in (RunView.RUNNING,
                                                      RunView.RUNNING_ELSEWHERE)


def run_to_end(service: VawtService, **kwargs: Any) -> Any:
    started = service.start_run(**kwargs)
    assert started.started, started.issues
    wait_for(finished(service))
    return service.run_status()


def codes(issues: Sequence[Any]) -> dict[str, str]:
    return {i.code: i.severity.value for i in issues}


# --- configuration -------------------------------------------------------------------

def test_load_save_round_trip(setup: Setup) -> None:
    service = setup.service()
    assert service.load().raw is None

    saved = service.save(setup.draft)

    assert saved.saved and saved.revision == 1
    loaded = service.load()
    assert loaded.revision == 1 and loaded.raw is not None
    assert loaded.raw["project_name"] == setup.draft["project_name"]


def test_malformed_configuration_is_not_saved(setup: Setup) -> None:
    raw = {**setup.draft, "max_global_cells": -1}

    result = setup.service().save(raw)

    assert not result.saved and result.issues
    assert setup.service().load().raw is None


def test_validation_parses_the_stl_once(setup: Setup) -> None:
    service = setup.service()

    first = service.validate(setup.draft)
    service.validate(setup.draft)
    service.preflight(setup.draft)
    service.section_status(setup.draft)

    assert first.can_run and setup.cache.loads == 1


def test_rotor_metrics_and_preset_draft(setup: Setup) -> None:
    service = setup.service()
    base = {"project_name": "Fixture", "geometry": setup.draft["geometry"]}

    metrics, _ = service.rotor_metrics(base["geometry"], Axis.Z)
    draft, issues = service.draft_from_preset(base, Axis.Z, Axis.X)
    _, refused = service.draft_from_preset(base, Axis.Z, Axis.Z)

    assert metrics is not None and metrics.diameter is not None and metrics.diameter > 0
    assert draft is not None and service.validate(draft).can_run
    assert "PRESET_NOT_APPLICABLE" in codes(refused)


def test_uploaded_stl_goes_to_inputs(setup: Setup) -> None:
    path = setup.service().store_source_file("rotor.stl", b"solid x\nendsolid x\n")

    assert path == setup.root / "inputs" / "rotor.stl" and path.is_file()


def test_environment_report(setup: Setup, tmp_path: Path) -> None:
    env = {**openfoam_env(tmp_path, "v2412", VAWT_TOOLS), "WSL_DISTRO_NAME": "Ubuntu-24.04",
           "VAWT_PROJECTS_DIR": "/mnt/e/projects"}
    mounts = read_mounts("E:\\134 /mnt/e 9p rw,aname=drvfs;path=E:\\ 0 0\n")

    info = VawtService(setup.root, env=env, mounts=mounts,
                       registry=setup.registry).environment()

    assert info.wsl and info.distro == "Ubuntu-24.04" and info.openfoam_version == "v2412"
    assert codes(info.issues) == {"OPENFOAM_VERSION_UNVERIFIED": "WARNING",
                                  "PROJECT_ON_WINDOWS_DRIVE": "WARNING"}


def test_project_on_a_windows_drive_has_a_location_issue() -> None:
    mounts = read_mounts("E:\\134 /mnt/e 9p rw,aname=drvfs;path=E:\\ 0 0\n")
    service = VawtService(Path("/mnt/e/projects/rotor"), mounts=mounts, registry=RunRegistry())

    assert "PROJECT_ON_WINDOWS_DRIVE" in codes(service.location_issues())


# --- section status -------------------------------------------------------------------

def test_every_configuration_field_belongs_to_a_section(setup: Setup) -> None:
    from tests.unit.vawt.test_vawt_operations import full, leaves

    paths = [p for p, _ in leaves(full(setup.tmp))]
    assert [p for p in paths if section_of_field(p) is None
            and p.split(".")[0] not in ("quality", "export")] == []


def test_sections_are_empty_without_a_configuration(setup: Setup) -> None:
    status = setup.service().section_status()

    assert {s: v.state for s, v in status.items()} == dict.fromkeys(
        SETUP_SECTIONS, SectionState.EMPTY)


def test_sections_ready_incomplete_and_error(setup: Setup) -> None:
    service = setup.service()
    ready = service.section_status(setup.draft)
    assert {s: v.state for s, v in ready.items()} == dict.fromkeys(
        SETUP_SECTIONS, SectionState.READY)

    no_units = json.loads(json.dumps(setup.draft))
    del no_units["geometry"]["source_units"]
    assert service.section_status(no_units)["geometry"].state is SectionState.INCOMPLETE

    small = json.loads(json.dumps(setup.draft))
    small["rotating_zone"]["diameter"] = small["rotating_zone"]["diameter"] / 10
    status = service.section_status(small)
    assert status["rotating_zone"].state is SectionState.ERROR
    assert "ROTOR_OUTSIDE_ZONE" in codes(status["rotating_zone"].issues)


def test_sections_become_stale_after_a_mesh(setup: Setup) -> None:
    service = setup.service()
    service.save(setup.draft)
    assert run_to_end(service).state is RunView.SUCCEEDED

    wake = json.loads(json.dumps(setup.draft))
    wake["refinement"]["wake"]["level"] += 1
    limits = json.loads(json.dumps(setup.draft))
    limits.setdefault("quality", {})["max_non_orthogonality"] = 60.0

    status = service.section_status(wake)
    assert status["refinement"].state is SectionState.STALE
    assert status["refinement"].stale_because == ("refinement.wake.level",)
    assert {s for s, v in status.items() if v.state is SectionState.STALE} == {"refinement"}
    assert all(v.state is SectionState.READY
               for v in service.section_status(limits).values())  # validation only


def test_plan_from_scratch_then_by_change(setup: Setup) -> None:
    service = setup.service()
    service.save(setup.draft)
    first = service.plan()
    assert first.from_scratch and Op.ROTOR_MESH in first.operations

    run_to_end(service)
    wake = json.loads(json.dumps(setup.draft))
    wake["refinement"]["wake"]["level"] += 1

    assert not set(service.plan().operations) & {Op.OUTER_MESH, Op.ROTOR_MESH, Op.ASSEMBLE}
    changed = service.plan(wake)
    assert {Op.OUTER_MESH, Op.ASSEMBLE, Op.CHECK_MESH} <= set(changed.operations)
    assert Op.ROTOR_MESH not in changed.operations
    assert changed.reasons[Op.OUTER_MESH] == ("refinement.wake.level",)


def test_preflight_is_arithmetic_on_a_draft(setup: Setup) -> None:
    view = setup.service().preflight(setup.draft)

    assert view.assessment is not None and view.assessment.status.value == "SAFE"
    assert view.estimate is not None and view.estimate.total > 0
    assert not (setup.root / "reports").exists()  # nothing written


# --- background runs -----------------------------------------------------------------

def test_start_returns_at_once_and_status_follows(setup: Setup) -> None:
    runner = GatedRunner(hold="snappyHexMesh", project_root=setup.root, pid=4242)
    service = setup.service(runner)
    service.save(setup.draft)

    clock = time.monotonic()
    started = service.start_run()
    returned = time.monotonic() - clock
    wait_for(lambda: service.run_status().stage == Op.OUTER_MESH.value, timeout=1.0)
    first_status = time.monotonic() - clock

    assert started.started and returned < 1.0 and first_status < 1.0  # spec 14.2
    runner.holding.wait(5)
    status = service.run_status()
    assert status.state is RunView.RUNNING and status.can_cancel
    assert status.run_id == started.run_id and (status.step, status.total_steps) == (4, 8)
    assert status.log == "logs/outer/03_snappyHexMesh.log"
    on_disk = json.loads((setup.root / STATUS_PATH).read_text("utf-8"))
    assert on_disk["command"]["pid"] == 4242 and on_disk["command"]["sub_case"] == "outer"
    runner.release.set()
    wait_for(finished(service))
    assert service.run_status().state is RunView.SUCCEEDED


def test_refresh_or_second_tab_reattaches_and_never_starts_a_second_run(setup: Setup) -> None:
    runner = GatedRunner(hold="snappyHexMesh")
    first = setup.service(runner)
    first.save(setup.draft)
    started = first.start_run()
    runner.holding.wait(5)

    refreshed = setup.service(FakeVawtRunner())  # new page: new service, same process
    status = refreshed.run_status()
    again = refreshed.start_run()

    assert status.state is RunView.RUNNING and status.run_id == started.run_id
    assert not again.started and again.run_id == started.run_id
    assert codes(again.issues) == {"RUN_ALREADY_ACTIVE": "INFO"}
    assert refreshed.cancel_run()  # the refreshed page can cancel it too
    wait_for(finished(refreshed))
    assert refreshed.run_status().state is RunView.CANCELLED
    # The held snappyHexMesh saw the cancel and came back CANCELLED; nothing followed.
    assert runner.calls == [("blockMesh", "outer"), ("snappyHexMesh", "outer")]


def test_cancel_from_another_thread_stops_the_command(setup: Setup) -> None:
    runner = GatedRunner(hold="snappyHexMesh")
    service = setup.service(runner)
    service.save(setup.draft)
    service.start_run()
    runner.holding.wait(5)

    assert service.cancel_run()
    wait_for(finished(service))

    status = service.run_status()
    assert status.state is RunView.CANCELLED and not status.can_cancel
    assert not service.cancel_run()  # nothing left to cancel
    assert "COMMAND_CANCELLED" in codes(status.issues)


def test_finished_run_is_recorded(setup: Setup) -> None:
    service = setup.service()
    service.save(setup.draft)

    status = run_to_end(service)

    assert status.state is RunView.SUCCEEDED
    assert service.reports()["mesh"] is not None
    assert [r["run_id"] for r in service.runs()] == [status.run_id]
    assert service.start_run().started  # a finished run does not block the next


def test_start_needs_a_saved_configuration(setup: Setup) -> None:
    result = setup.service().start_run()

    assert not result.started and "NO_SAVED_CONFIGURATION" in codes(result.issues)


# --- runs held elsewhere, interrupted runs, orphaned commands ------------------------

def write_lock(root: Path, pid: int, create_time: float | None) -> None:
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    holder: dict[str, Any] = {"pid": pid, "host": socket.gethostname(), "started_at": "x"}
    if create_time is not None:
        holder["process_create_time"] = create_time
    path.write_text(json.dumps(holder), encoding="utf-8")


def write_running_status(root: Path, command: dict[str, Any] | None = None) -> None:
    write_status(root, run_id="0123456789abcdef", state=RunState.RUNNING,
                 stage=Op.ROTOR_MESH.value, step=6, total=8, started_at="t", command=command)


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield process
    process.kill()
    process.wait()


def dead_pid() -> int:
    pid = 999_999
    while psutil.pid_exists(pid):
        pid -= 1
    return pid


def test_run_held_by_another_live_process(setup: Setup,
                                          sleeper: subprocess.Popen[bytes]) -> None:
    service = setup.service()
    service.save(setup.draft)
    write_lock(setup.root, sleeper.pid, process_create_time(sleeper.pid))
    write_running_status(setup.root)

    status = service.run_status()
    refused = service.start_run()

    assert status.state is RunView.RUNNING_ELSEWHERE and not status.can_cancel
    assert not refused.started and "RUN_IN_PROGRESS" in codes(refused.issues)
    assert not service.cancel_run()


@pytest.mark.parametrize("lock", ["dead_pid", "reused_pid", "missing"])
def test_interrupted_run_is_reported_and_recovered(setup: Setup, lock: str) -> None:
    service = setup.service()
    service.save(setup.draft)
    if lock == "dead_pid":
        write_lock(setup.root, dead_pid(), None)
    elif lock == "reused_pid":
        started = process_create_time(os.getpid())
        assert started is not None
        write_lock(setup.root, os.getpid(), started - 3600)
    write_running_status(setup.root)

    status = service.run_status()
    assert status.state is RunView.INTERRUPTED and status.stage == Op.ROTOR_MESH.value
    assert codes(status.issues) == {"RUN_INTERRUPTED": "WARNING"}

    after = run_to_end(service)
    assert after.state is RunView.SUCCEEDED
    assert service.runs()[0]["previous_run_interrupted"] == "0123456789abcdef"


def test_orphaned_openfoam_process_blocks_until_terminated(
        setup: Setup, sleeper: subprocess.Popen[bytes]) -> None:
    service = setup.service()
    service.save(setup.draft)
    write_running_status(setup.root, {
        "name": "snappyHexMesh", "sub_case": "rotor", "log": "logs/rotor/03_snappyHexMesh.log",
        "pid": sleeper.pid, "pid_create_time": process_create_time(sleeper.pid)})

    status = service.run_status()
    refused = service.start_run()

    assert status.state is RunView.INTERRUPTED and status.orphan_pid == sleeper.pid
    assert codes(status.issues)["ORPHANED_OPENFOAM_PROCESS"] == "BLOCKING"
    assert not refused.started
    assert service.terminate_orphan()
    assert sleeper.wait(10) is not None
    assert service.run_status().orphan_pid is None and not service.terminate_orphan()


def test_a_reused_pid_is_not_taken_for_an_orphan(setup: Setup) -> None:
    started = process_create_time(os.getpid())
    assert started is not None
    write_running_status(setup.root, {"name": "snappyHexMesh", "pid": os.getpid(),
                                      "pid_create_time": started - 3600})

    assert setup.service().run_status().orphan_pid is None


CRASHING_RUN = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from tests.fakes_vawt import FakeVawtRunner
    from tests.fakes import PLENTY
    from vawt.config import VawtProjectConfig
    from vawt.pipeline import VawtPipeline
    import json

    class Crash(FakeVawtRunner):
        async def run(self, argv, **kwargs):
            if argv[0] == "snappyHexMesh" and kwargs["case_root"].name == "rotor":
                os._exit(3)  # the process dies mid-command: no cleanup at all
            return await super().run(argv, **kwargs)

    root, config, env = Path(sys.argv[1]), json.loads(sys.argv[2]), json.loads(sys.argv[3])
    VawtPipeline(Crash(), environment=env, system_probe=lambda _: PLENTY).run_sync(
        root, VawtProjectConfig.model_validate(config), force=True)
""")


def test_crash_mid_run_then_recovery_reruns_only_what_was_interrupted(setup: Setup) -> None:
    service = setup.service()
    service.save(setup.draft)
    assert run_to_end(service).state is RunView.SUCCEEDED
    changed = json.loads(json.dumps(setup.draft))
    changed["refinement"]["blade_max_level"] += 1
    service.save(changed)

    crashed = subprocess.run(
        [sys.executable, "-c", CRASHING_RUN, str(setup.root), json.dumps(changed),
         json.dumps(setup.env)],
        cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=120, check=False)

    assert crashed.returncode == 3
    assert (setup.root / LOCK_PATH).exists()  # left behind by the dead process
    status = service.run_status()
    assert status.state is RunView.INTERRUPTED and status.stage == Op.ROTOR_MESH.value

    recovered = run_to_end(service)
    record = service.runs()[0]
    assert recovered.state is RunView.SUCCEEDED
    assert "vawt_rotor_mesh" in record["executed"]
    assert "vawt_outer_mesh" in record["reused"]  # the crashed run's outer mesh verifies
    assert not (setup.root / LOCK_PATH).exists()


# --- log tail -------------------------------------------------------------------------

def test_log_tail_reads_a_bounded_number_of_bytes(setup: Setup) -> None:
    log = setup.root / "logs" / "rotor" / "03_snappyHexMesh.log"
    log.parent.mkdir(parents=True)
    lines = [f"line {i:06d} " + "x" * 50 for i in range(20_000)]  # ~1.2 MB
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_running_status(setup.root, {"name": "snappyHexMesh", "log": "logs/rotor/"
                                      "03_snappyHexMesh.log"})

    tail = setup.service().log_tail(max_bytes=4096)

    assert tail.path == "logs/rotor/03_snappyHexMesh.log" and tail.truncated
    assert tail.bytes_read == 4096 and tail.size == log.stat().st_size
    assert tail.text.endswith(lines[-1] + "\n")
    assert tail.text.splitlines()[0] in lines  # no partial first line


def test_log_tail_without_a_run_uses_the_newest_log(setup: Setup) -> None:
    service = setup.service()
    assert service.log_tail().path is None
    older = setup.root / "logs" / "outer" / "01_blockMesh.log"
    newer = setup.root / "logs" / "rotor" / "01_blockMesh.log"
    for path, text in ((older, "old\n"), (newer, "new\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        time.sleep(0.02)

    tail = service.log_tail()

    assert tail.path == "logs/rotor/01_blockMesh.log" and tail.text == "new\n"
    assert not tail.truncated


# --- boundaries -----------------------------------------------------------------------

def test_vawt_package_never_imports_streamlit() -> None:
    for path in (REPO / "vawt").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.Import) for alias in node.names}
        imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom)}
        assert "streamlit" not in imported, path.name
