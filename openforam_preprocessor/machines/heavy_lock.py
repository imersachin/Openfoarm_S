"""One heavy step at a time on this machine (owner, M2a): an imported mesh's
OpenFOAM steps and the revolution check take up to 32 GB at 23 M cells (E0),
so two at once, even of two projects, would not fit in this machine's 31 GB.

~/.machines/heavy.lock, with the run lock's rules for a stale holder (its
process gone, or its PID reused; core.workflow.run_lock), says who holds it:
the project, the step, when it started and the estimated time left. A run
that finds it held waits, cancellably, and says for whom.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.workflow.run_lock import RunLock

HEAVY_LOCK_ENV = "MACHINES_HEAVY_LOCK"  # another lock file (tests)
POLL_S = 2.0


def heavy_lock_path(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    return Path(env.get(HEAVY_LOCK_ENV) or Path.home() / ".machines" / "heavy.lock")


class HeavyLock(RunLock):
    """RunLock at the machine-wide path, its record extended with what holds it."""

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(Path("/"))
        self.path = path or heavy_lock_path()

    def describe(self, project: Path, step: str, estimate_s: float | None) -> None:
        """Record what holds the lock (while held)."""
        if not self._held:
            return
        try:
            record = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            record = {}
        record.update({"project": str(project), "step": step,
                       "step_started_at": datetime.now(UTC).isoformat(),
                       "estimate_s": estimate_s})
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(self.path)

    def holder_text(self) -> str | None:
        """Who holds the lock, for a person: None when free or stale."""
        if not self.is_held_by_live_run():
            return None
        record = self.holder() or {}
        project = Path(str(record.get("project") or "another project")).name
        step = record.get("step") or "a heavy step"
        started = str(record.get("step_started_at") or record.get("started_at") or "")
        text = f"{project}: {step}"
        try:
            since = datetime.fromisoformat(started)
            elapsed = (datetime.now(UTC) - since).total_seconds()
            text += f", started {since.astimezone().strftime('%H:%M')}"
            estimate = record.get("estimate_s")
            if isinstance(estimate, int | float):
                left = max(float(estimate) - elapsed, 0.0)
                text += f", about {left / 60:.0f} min left" if left >= 60 else \
                    f", about {left:.0f} s left"
        except ValueError:
            pass
        return text

    async def wait(self, cancelled: Callable[[], bool],
                   waiting: Callable[[str], Any] | None = None) -> bool:
        """Take the lock, waiting while another live holder has it; False if
        cancelled first. `waiting` is told who holds it while waiting."""
        told = 0.0
        while True:
            if self.acquire() is None:
                return True
            if cancelled():
                return False
            if waiting is not None and time.monotonic() - told > 10:
                waiting(self.holder_text() or "another heavy step")
                told = time.monotonic()
            await asyncio.sleep(POLL_S)
