from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.artifacts.hashing import MISSING, hash_files, sha256_file
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage

MANIFEST_PATH = ".preprocessor/artifacts.json"
MANIFEST_VERSION = 1


class CacheStatus(StrEnum):
    HIT = "HIT"
    NO_RECORD = "NO_RECORD"
    INPUTS_CHANGED = "INPUTS_CHANGED"
    OUTPUT_MISSING = "OUTPUT_MISSING"
    OUTPUT_CORRUPTED = "OUTPUT_CORRUPTED"


@dataclass(frozen=True)
class ArtifactRecord:
    """Outputs of one successful operation and the exact inputs that produced them."""

    operation: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    created_at: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "created_at": self.created_at,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRecord:
        return cls(
            operation=str(data["operation"]),
            inputs={str(k): str(v) for k, v in data["inputs"].items()},
            outputs={str(k): str(v) for k, v in data["outputs"].items()},
            created_at=str(data["created_at"]),
            details=dict(data.get("details", {})),
        )


@dataclass(frozen=True)
class CacheCheck:
    status: CacheStatus
    record: ArtifactRecord | None = None
    paths: tuple[str, ...] = ()  # offending inputs/outputs, for diagnosis

    @property
    def reusable(self) -> bool:
        return self.status is CacheStatus.HIT


class ArtifactStore:
    """Verified artifact reuse. A file's existence alone never counts as a cache hit.

    Reuse requires: a recorded successful run, identical input hashes
    (dependency identity), and every recorded output present with an
    identical content hash.
    """

    def __init__(self, case_root: Path) -> None:
        self.case_root = case_root
        self.path = case_root / MANIFEST_PATH
        self.load_issue: Issue | None = None
        self._records = self._load()

    def _load(self) -> dict[str, ArtifactRecord]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != MANIFEST_VERSION:
                raise ValueError(f"unsupported manifest version {data.get('version')!r}")
            return {
                name: ArtifactRecord.from_dict(record)
                for name, record in data["records"].items()
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.load_issue = Issue(
                category=IssueCategory.DEPENDENCY,
                severity=IssueSeverity.WARNING,
                stage=IssueStage.CASE_GENERATION,
                code="ARTIFACT_MANIFEST_UNREADABLE",
                message="The artifact manifest could not be read; no artifacts will be reused.",
                explanation="The manifest is corrupt, truncated, or from an unsupported version.",
                suggested_action="No action required: all steps re-run and the manifest is "
                "rewritten.",
                artifact_reference=MANIFEST_PATH,
                details={"exception_type": type(exc).__name__, "exception": str(exc)},
            )
            return {}

    def _save(self) -> None:
        payload = {
            "version": MANIFEST_VERSION,
            "records": {name: self._records[name].as_dict() for name in sorted(self._records)},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        temporary.replace(self.path)

    def get(self, operation: str) -> ArtifactRecord | None:
        return self._records.get(operation)

    def check(self, operation: str, inputs: dict[str, str]) -> CacheCheck:
        record = self._records.get(operation)
        if record is None:
            return CacheCheck(CacheStatus.NO_RECORD)
        if record.inputs != inputs:
            changed = sorted(
                key for key in record.inputs.keys() | inputs.keys()
                if record.inputs.get(key) != inputs.get(key)
            )
            return CacheCheck(CacheStatus.INPUTS_CHANGED, record, tuple(changed))
        missing, corrupted = [], []
        for relative_path, expected in record.outputs.items():
            actual = sha256_file(self.case_root / relative_path)
            if actual == MISSING:
                missing.append(relative_path)
            elif actual != expected:
                corrupted.append(relative_path)
        if missing:
            return CacheCheck(CacheStatus.OUTPUT_MISSING, record, tuple(missing))
        if corrupted:
            return CacheCheck(CacheStatus.OUTPUT_CORRUPTED, record, tuple(corrupted))
        return CacheCheck(CacheStatus.HIT, record)

    def invalidate(self, *operations: str) -> None:
        """Drop records before re-running, so partial outputs are never reused."""
        if any(self._records.pop(operation, None) is not None for operation in operations):
            self._save()

    def record(
        self,
        operation: str,
        inputs: dict[str, str],
        outputs: Iterable[Path],
        details: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        record = ArtifactRecord(
            operation=operation,
            inputs=dict(inputs),
            outputs=hash_files(self.case_root, outputs),
            created_at=datetime.now(UTC).isoformat(),
            details=details or {},
        )
        self._records[operation] = record
        self._save()
        return record
