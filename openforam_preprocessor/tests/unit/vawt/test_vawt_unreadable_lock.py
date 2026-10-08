"""V4 review finding: a run lock with no readable holder (a process died between
creating and writing it) is reported as such and can be removed after
confirmation, instead of blocking the project as "another process is running"."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.workflow.run_lock import LOCK_PATH
from tests.unit.vawt.test_vawt_service import Setup, codes, finished, wait_for
from vawt.service import UNREADABLE_LOCK_AGE_S, RunView


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    s = Setup(tmp_path)
    s.service().save(s.draft)
    return s


def write_lock(root: Path, text: str, age_s: float) -> Path:
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    then = time.time() - age_s
    os.utime(path, (then, then))
    return path


@pytest.mark.parametrize("text", ["", "{not json", "[1, 2]"])
def test_old_unreadable_lock_is_reported_and_blocks_start(setup: Setup, text: str) -> None:
    write_lock(setup.root, text, UNREADABLE_LOCK_AGE_S + 60)
    service = setup.service()

    status = service.run_status()
    refused = service.start_run()

    assert status.state is RunView.LOCK_UNREADABLE and not status.can_cancel
    assert codes(status.issues) == {"RUN_LOCK_UNREADABLE": "BLOCKING"}
    assert str(setup.root / LOCK_PATH) in status.issues[0].message
    assert not refused.started and "RUN_LOCK_UNREADABLE" in codes(refused.issues)


def test_removing_it_lets_the_next_run_start(setup: Setup) -> None:
    lock = write_lock(setup.root, "", UNREADABLE_LOCK_AGE_S + 60)
    service = setup.service()

    assert service.remove_unreadable_lock()

    assert not lock.exists()
    assert service.start_run().started
    wait_for(finished(service))
    assert service.run_status().state is RunView.SUCCEEDED
    setup.registry.shutdown()


def test_a_lock_being_written_right_now_is_not_unreadable(setup: Setup) -> None:
    lock = write_lock(setup.root, "", 0.0)
    service = setup.service()

    assert service.run_status().state is RunView.RUNNING_ELSEWHERE
    assert not service.remove_unreadable_lock() and lock.exists()


def test_a_readable_lock_is_never_removed(setup: Setup) -> None:
    lock = write_lock(setup.root, '{"pid": 1, "host": "elsewhere"}', 3600)

    assert not setup.service().remove_unreadable_lock() and lock.exists()
