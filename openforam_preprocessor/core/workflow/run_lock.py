"""One run per case at a time: concurrent runs would corrupt artifacts."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

import psutil

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage

LOCK_PATH = ".preprocessor/run.lock"
CREATE_TIME_TOLERANCE_S = 1.0


def process_create_time(pid: int) -> float | None:
    """When the process with this PID started (seconds since the epoch), or None
    if it does not exist or cannot be inspected."""
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        return None


class RunLock:
    def __init__(self, case_root: Path) -> None:
        self.path = case_root / LOCK_PATH
        self._held = False

    def acquire(self) -> Issue | None:
        """Take the lock; return a BLOCKING issue if another live run holds it.

        A lock left by a process that no longer exists on this host is stale
        and is replaced. So is one whose PID now belongs to a process started
        at another time (the PID was reused, e.g. after a WSL restart); locks
        written without a start time keep the PID-only rule. A lock from
        another host is never assumed stale.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self._holder()
                if holder is not None and self._is_stale(holder):
                    self.path.unlink(missing_ok=True)
                    continue
                return self._busy_issue(holder)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                           "started_at": datetime.now(UTC).isoformat(),
                           "process_create_time": process_create_time(os.getpid())},
                          stream)
            self._held = True
            return None
        return self._busy_issue(self._holder())

    def holder(self) -> dict[str, object] | None:
        """The lock holder's record, or None if the lock is free or unreadable."""
        return self._holder() if self.path.exists() else None

    def is_held_by_live_run(self) -> bool:
        """Whether a live run holds the lock. A stale lock is not live; an
        unreadable one is treated as live (acquire would refuse it too)."""
        if not self.path.exists():
            return False
        holder = self._holder()
        return holder is None or not self._is_stale(holder)

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _holder(self) -> dict[str, object] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _is_stale(holder: dict[str, object]) -> bool:
        pid = holder.get("pid")
        if holder.get("host") != socket.gethostname() or not isinstance(pid, int):
            return False
        if not psutil.pid_exists(pid):
            return True
        recorded = holder.get("process_create_time")
        if not isinstance(recorded, int | float):
            return False  # older lock: PID-only rule
        current = process_create_time(pid)
        return current is not None and abs(current - recorded) > CREATE_TIME_TOLERANCE_S

    def _busy_issue(self, holder: dict[str, object] | None) -> Issue:
        return Issue(
            category=IssueCategory.EXECUTION,
            severity=IssueSeverity.BLOCKING,
            stage=IssueStage.OPENFOAM_EXECUTION,
            code="RUN_IN_PROGRESS",
            message="Another run is in progress for this case; nothing was started.",
            explanation="Concurrent runs on one case would overwrite each other's artifacts.",
            suggested_action="Wait for the other run to finish. If no run is active (e.g. "
            f"after a crash on another machine), delete {LOCK_PATH}.",
            artifact_reference=str(self.path),
            details={"holder": holder},
        )
