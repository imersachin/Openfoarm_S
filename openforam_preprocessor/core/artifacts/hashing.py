from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MISSING = "MISSING"
_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Streamed content hash (bounded memory). MISSING if the file does not exist."""
    if not path.is_file():
        return MISSING
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def hash_files(root: Path, paths: Iterable[Path]) -> dict[str, str]:
    """{case-relative posix path: sha256} in deterministic order."""
    return {relative(root, path): sha256_file(path) for path in sorted(paths)}


def files_under(directory: Path, *, exclude_dirs: frozenset[str] = frozenset()) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.rglob("*")
        if path.is_file() and not exclude_dirs.intersection(path.relative_to(directory).parts)
    )
