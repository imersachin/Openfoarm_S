"""V4: the in-process run registry (one worker thread per project)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vawt.runtime import ActiveRun, RunRegistry


async def wait_for_cancel(event: asyncio.Event) -> Any:
    await event.wait()
    return "cancelled"


def test_cancel_before_the_loop_exists_still_reaches_the_run(tmp_path: Path) -> None:
    run = ActiveRun(tmp_path, "r1")
    assert run.cancel()  # requested before the worker started

    run.main(wait_for_cancel)  # returns only because the event was set

    assert run.result == "cancelled" and run.done and not run.cancel()


def test_one_run_per_project_and_cancel_from_another_thread(tmp_path: Path) -> None:
    registry = RunRegistry()
    first, started = registry.start(tmp_path, "r1", wait_for_cancel)
    second, again = registry.start(tmp_path / "." , "r2", wait_for_cancel)

    assert started and not again and second is first
    assert registry.active(tmp_path) is first
    assert registry.cancel(tmp_path)
    assert first.finished.wait(5) and first.result == "cancelled"
    assert registry.active(tmp_path) is None and registry.latest(tmp_path) is first


def test_shutdown_cancels_active_runs(tmp_path: Path) -> None:
    registry = RunRegistry()
    runs = [registry.start(tmp_path / name, name, wait_for_cancel)[0] for name in ("a", "b")]

    registry.shutdown(timeout=5)

    assert all(r.done and r.result == "cancelled" for r in runs)


def test_an_exception_in_the_run_is_kept(tmp_path: Path) -> None:
    async def broken(_: asyncio.Event) -> Any:
        raise RuntimeError("boom")

    run, _ = RunRegistry().start(tmp_path, "r1", broken)

    assert run.finished.wait(5) and isinstance(run.error, RuntimeError)
