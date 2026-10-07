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


class RunLock:
    def __init__(self, case_root: Path) -> None:
        self.path = case_root / LOCK_PATH
        self._held = False

    def acquire(self) -> Issue | None:
        """Take the lock; return a BLOCKING issue if another live run holds it.

        A lock left by a process that no longer exists on this host is stale
        and is replaced. A lock from another host is never assumed stale.
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
                           "started_at": datetime.now(UTC).isoformat()}, stream)
            self._held = True
            return None
        return self._busy_issue(self._holder())

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
        return (
            holder.get("host") == socket.gethostname()
            and isinstance(pid, int)
            and not psutil.pid_exists(pid)
        )

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
