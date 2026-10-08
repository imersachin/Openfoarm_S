"""Run status file (spec section 12.2): what a run is doing, readable by another process.

The pipeline writes .preprocessor/status.json atomically at every stage
boundary. A reader (V4) never sees a partial file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

STATUS_PATH = ".preprocessor/status.json"


class RunState(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def write_status(project_root: Path, *, run_id: str, state: RunState, stage: str,
                 step: int, total: int, started_at: str, message: str = "",
                 command: Mapping[str, Any] | None = None) -> None:
    """command: the OpenFOAM command running now (name, sub_case, log relative to
    the project, and once started its pid and pid_create_time), else None."""
    path = project_root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "run_id": run_id, "state": state.value, "stage": stage, "step": step,
        "total_steps": total, "started_at": started_at,
        "updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid(), "message": message,
        "command": dict(command) if command is not None else None,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def read_status(project_root: Path) -> dict[str, Any] | None:
    """The last status written, or None if there is none or it is unreadable."""
    try:
        data = json.loads((project_root / STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
