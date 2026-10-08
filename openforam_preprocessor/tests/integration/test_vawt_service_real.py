"""V4 on real OpenFOAM v2512: a background AMI run through VawtService,
cancelled during the rotor's snappyHexMesh, then run again.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_vawt_service_real.py -v
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest

from tests.fakes_vawt import VAWT_TOOLS
from tests.fixtures.vawt.drafts import preset_draft
from vawt.runtime import RunRegistry
from vawt.service import RunView, VawtService
from vawt.status import read_status

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in VAWT_TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


def wait_for(condition: Callable[[], bool], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.1)


def openfoam_processes_in(root: Path) -> list[int]:
    found = []
    for process in psutil.process_iter(["pid", "name", "cwd"]):
        cwd = process.info.get("cwd") or ""
        if process.info.get("name") in VAWT_TOOLS and cwd.startswith(str(root)):
            found.append(process.info["pid"])
    return found


def test_background_run_cancel_and_resume(tmp_path: Path) -> None:
    root = tmp_path / "project"
    registry = RunRegistry()
    service = VawtService(root, registry=registry)
    assert service.save(preset_draft(tmp_path)).saved

    started = service.start_run()
    assert started.started

    def in_rotor_snappy() -> bool:
        command = (read_status(root) or {}).get("command") or {}
        return (command.get("name"), command.get("sub_case")) == ("snappyHexMesh", "rotor") \
            and "pid" in command

    wait_for(in_rotor_snappy, timeout=300)
    pid = (read_status(root) or {})["command"]["pid"]
    assert psutil.pid_exists(pid)
    assert service.run_status().state is RunView.RUNNING

    assert service.cancel_run()
    wait_for(lambda: service.run_status().state is not RunView.RUNNING, timeout=60)

    assert service.run_status().state is RunView.CANCELLED
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert openfoam_processes_in(root) == []

    again = service.start_run()
    assert again.started
    wait_for(lambda: service.run_status().state is not RunView.RUNNING, timeout=600)

    assert service.run_status().state is RunView.SUCCEEDED
    record = service.runs()[0]
    assert "vawt_outer_mesh" in record["reused"]
    assert {"vawt_rotor_mesh", "vawt_assemble", "vawt_check_mesh"} <= set(record["executed"])
    registry.shutdown()
