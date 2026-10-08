"""Background runs (spec section 12.2): one in-process worker thread per project.

No queue, database or server: the registry lives in the app process. Each run
gets its own thread and asyncio event loop. Cancelling from another thread goes
through the loop (an asyncio.Event is not thread-safe). On a normal interpreter
exit every active run is cancelled, so OpenFOAM processes are terminated and the
status file ends as CANCELLED; a hard kill leaves an interrupted run, which the
service reports from the status file and the run lock.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

from vawt.pipeline import VawtRunResult

Work = Callable[[asyncio.Event], Awaitable[VawtRunResult]]

SHUTDOWN_WAIT_S = 20.0  # the runner gives a cancelled command 10 s to terminate


class ActiveRun:
    """One run on its worker thread."""

    def __init__(self, project: Path, run_id: str) -> None:
        self.project, self.run_id = project, run_id
        self.result: VawtRunResult | None = None
        self.error: BaseException | None = None
        self.ready = threading.Event()  # loop and cancel event exist
        self.finished = threading.Event()
        self._guard = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._cancel_requested = False
        self.thread: threading.Thread | None = None

    @property
    def done(self) -> bool:
        return self.finished.is_set()

    def main(self, work: Work) -> None:
        async def body() -> VawtRunResult:
            with self._guard:
                self._loop = asyncio.get_running_loop()
                self._event = asyncio.Event()
                if self._cancel_requested:
                    self._event.set()
            self.ready.set()
            return await work(self._event)

        try:
            self.result = asyncio.run(body())
        except BaseException as error:  # noqa: BLE001 - kept for run_status; the
            self.error = error  # pipeline has already written status and run record
        finally:
            self.ready.set()
            self.finished.set()

    def cancel(self) -> bool:
        """Ask the run to stop. False if it had already finished."""
        with self._guard:
            if self.finished.is_set():
                return False
            self._cancel_requested = True
            if self._loop is not None and self._event is not None:
                try:
                    self._loop.call_soon_threadsafe(self._event.set)
                except RuntimeError:  # the loop closed in the meantime
                    return False
            return True


class RunRegistry:
    """The runs of this process, one per project (keyed by resolved path)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[Path, ActiveRun] = {}

    @staticmethod
    def _key(project: Path) -> Path:
        return project.resolve()

    def latest(self, project: Path) -> ActiveRun | None:
        """The project's most recent run in this process, finished or not."""
        with self._lock:
            return self._runs.get(self._key(project))

    def active(self, project: Path) -> ActiveRun | None:
        run = self.latest(project)
        return run if run is not None and not run.done else None

    def start(self, project: Path, run_id: str, work: Work) -> tuple[ActiveRun, bool]:
        """Start work on a new thread, unless a run of this project is active:
        then return that run and False (never a second run)."""
        key = self._key(project)
        with self._lock:
            current = self._runs.get(key)
            if current is not None and not current.done:
                return current, False
            run = ActiveRun(key, run_id)
            run.thread = threading.Thread(target=run.main, args=(work,), daemon=True,
                                          name=f"vawt-run-{run_id[:8]}")
            self._runs[key] = run
            run.thread.start()
        run.ready.wait(timeout=5.0)
        return run, True

    def cancel(self, project: Path) -> bool:
        run = self.active(project)
        return run.cancel() if run is not None else False

    def shutdown(self, timeout: float = SHUTDOWN_WAIT_S) -> None:
        """Cancel every active run and wait for them to stop."""
        with self._lock:
            runs = [r for r in self._runs.values() if not r.done]
        for run in runs:
            run.cancel()
        for run in runs:
            if run.thread is not None:
                run.thread.join(timeout)


REGISTRY = RunRegistry()
atexit.register(REGISTRY.shutdown)
