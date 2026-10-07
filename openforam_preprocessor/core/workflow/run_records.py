"""Persistent per-run metadata: what ran, with which inputs and tools, and how it ended."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RUNS_DIR = ".preprocessor/runs"
KEEP_RUNS = 100


def write_run_record(case_root: Path, record: dict[str, Any], keep: int = KEEP_RUNS) -> Path:
    """Write one run record and prune the oldest beyond `keep`."""
    directory = case_root / RUNS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = record["started_at"].replace(":", "").replace("-", "").replace("+", "_")
    path = directory / f"{stamp}_{record['run_id'][:8]}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(path)
    for old in sorted(directory.glob("*.json"))[:-keep]:
        old.unlink(missing_ok=True)
    return path


def list_run_records(case_root: Path) -> list[dict[str, Any]]:
    """Run records, newest first. Unreadable records are skipped."""
    directory = case_root / RUNS_DIR
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), reverse=True) if directory.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            records.append({**data, "record_path": str(path)})
    return records
