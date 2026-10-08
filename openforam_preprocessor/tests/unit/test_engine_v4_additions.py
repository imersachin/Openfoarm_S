"""V4 engine additions (defaults keep the existing behaviour): run-lock PID reuse,
runner on_start and live log flushing, verified OpenFOAM versions."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from core.config.models import OpenFOAMProfile
from core.issues import IssueStage
from core.workflow.run_lock import LOCK_PATH, RunLock, process_create_time
from openfoam.commands import MeshingStep
from openfoam.environment import validate_environment
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus
from tests.fakes import openfoam_env

# --- run lock ------------------------------------------------------------------------

def write_lock(root: Path, **holder: Any) -> None:
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(holder), encoding="utf-8")


def test_lock_records_the_process_start_time(tmp_path: Path) -> None:
    lock = RunLock(tmp_path)
    assert lock.acquire() is None
    holder = json.loads((tmp_path / LOCK_PATH).read_text("utf-8"))
    lock.release()

    assert holder["pid"] == os.getpid()
    assert holder["process_create_time"] == process_create_time(os.getpid())


def test_lock_whose_pid_was_reused_is_stale(tmp_path: Path) -> None:
    started = process_create_time(os.getpid())
    assert started is not None
    write_lock(tmp_path, pid=os.getpid(), host=socket.gethostname(),
               process_create_time=started - 3600)  # same PID, another process

    lock = RunLock(tmp_path)
    assert lock.acquire() is None
    lock.release()


def test_lock_of_the_same_live_process_still_blocks(tmp_path: Path) -> None:
    write_lock(tmp_path, pid=os.getpid(), host=socket.gethostname(),
               process_create_time=process_create_time(os.getpid()))

    issue = RunLock(tmp_path).acquire()

    assert issue is not None and issue.code == "RUN_IN_PROGRESS"


def test_lock_without_start_time_keeps_the_pid_only_rule(tmp_path: Path) -> None:
    write_lock(tmp_path, pid=os.getpid(), host=socket.gethostname())

    assert RunLock(tmp_path).acquire() is not None


# --- runner ----------------------------------------------------------------------------

PYTHON = (sys.executable, "-c")


def test_on_start_receives_the_process_id(tmp_path: Path) -> None:
    seen: list[int] = []

    result = asyncio.run(OpenFOAMRunner().run(
        (*PYTHON, "import os; print(os.getpid())"), case_root=tmp_path, log_name="a.log",
        on_start=seen.append))

    assert result.status is RunStatus.SUCCESS
    assert seen == [int(Path(result.log_path).read_text("utf-8").strip())]


def test_a_failing_on_start_does_not_stop_the_command(tmp_path: Path) -> None:
    def broken(_: int) -> None:
        raise OSError("disk full")

    result = asyncio.run(OpenFOAMRunner().run(
        (*PYTHON, "print('ok')"), case_root=tmp_path, log_name="a.log", on_start=broken))

    assert result.status is RunStatus.SUCCESS


def test_log_is_readable_while_the_command_runs(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, CommandResult]:
        log = tmp_path / "logs" / "live.log"
        task = asyncio.ensure_future(OpenFOAMRunner().run(
            (*PYTHON, "import sys, time; print('first line'); sys.stdout.flush(); "
             "time.sleep(3)"), case_root=tmp_path, log_name="live.log"))
        seen = ""
        for _ in range(100):  # up to 2 s; the command runs 3 s
            await asyncio.sleep(0.02)
            seen = log.read_text("utf-8") if log.is_file() else ""
            if seen:
                break
        return seen, await task

    seen, result = asyncio.run(scenario())

    assert seen == "first line\n" and result.status is RunStatus.SUCCESS


class OldStyleRunner(OpenFOAMRunner):
    """A runner subclass written before on_start existed."""

    async def run(self, argv: Sequence[str], *, case_root: Path, log_name: str,
                  **kwargs: Any) -> CommandResult:
        assert "on_start" not in kwargs
        return await super().run(argv, case_root=case_root, log_name=log_name, **kwargs)


def test_run_step_without_on_start_suits_older_runners(tmp_path: Path) -> None:
    step = MeshingStep((*PYTHON, "print('ok')"), "a.log", IssueStage.OPENFOAM_EXECUTION)

    result = asyncio.run(OldStyleRunner().run_step(step, case_root=tmp_path))

    assert result.status is RunStatus.SUCCESS


def test_run_step_passes_on_start(tmp_path: Path) -> None:
    seen: list[int] = []
    step = MeshingStep((*PYTHON, "print('ok')"), "a.log", IssueStage.OPENFOAM_EXECUTION)

    asyncio.run(OpenFOAMRunner().run_step(step, case_root=tmp_path, on_start=seen.append))

    assert len(seen) == 1


# --- environment ---------------------------------------------------------------------

def test_unverified_version_is_a_warning_only_when_asked(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "v2412", ("blockMesh",))

    plain = validate_environment(OpenFOAMProfile.OPENCFD, ["blockMesh"], env)
    checked = validate_environment(OpenFOAMProfile.OPENCFD, ["blockMesh"], env,
                                   verified_versions=("v2512",))
    verified = validate_environment(OpenFOAMProfile.OPENCFD, ["blockMesh"],
                                    openfoam_env(tmp_path, "v2512", ("blockMesh",)),
                                    verified_versions=("v2512",))

    assert plain == ()
    assert [(i.code, i.severity.value) for i in checked] == [
        ("OPENFOAM_VERSION_UNVERIFIED", "WARNING")]
    assert verified == ()
