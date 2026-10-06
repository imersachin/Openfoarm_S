from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckMeshMetrics:
    cells: int | None = None
    faces: int | None = None
    points: int | None = None
    max_non_orthogonality: float | None = None
    average_non_orthogonality: float | None = None
    max_skewness: float | None = None
    failed_checks: int = 0
    mesh_ok: bool = False
    raw_log_path: str = ""


class CheckMeshParser:
    _cells = re.compile(r"\bcells:\s+(\d+)", re.IGNORECASE)
    _faces = re.compile(r"\bfaces:\s+(\d+)", re.IGNORECASE)
    _points = re.compile(r"\bpoints:\s+(\d+)", re.IGNORECASE)
    _non_ortho = re.compile(
        r"Mesh non-orthogonality Max:\s*([0-9.eE+-]+)\s+average:\s*([0-9.eE+-]+)",
        re.IGNORECASE,
    )
    _skewness = re.compile(
        r"(?:max(?:imum)?\s+(?:face\s+)?skewness|skewness)\D+([0-9.eE+-]+)",
        re.IGNORECASE,
    )
    _failed = re.compile(r"Failed\s+(\d+)\s+mesh\s+checks", re.IGNORECASE)
    _ok = re.compile(r"Mesh\s+OK\.", re.IGNORECASE)

    def parse_file(self, path: Path) -> CheckMeshMetrics:
        return self.parse_text(path.read_text(encoding="utf-8", errors="replace"), path)

    def parse_text(self, text: str, path: Path | None = None) -> CheckMeshMetrics:
        def integer(pattern: re.Pattern[str]) -> int | None:
            match = pattern.search(text)
            return int(match.group(1)) if match else None

        non_ortho = self._non_ortho.search(text)
        skewness_matches = self._skewness.findall(text)
        failed = self._failed.search(text)

        return CheckMeshMetrics(
            cells=integer(self._cells),
            faces=integer(self._faces),
            points=integer(self._points),
            max_non_orthogonality=float(non_ortho.group(1)) if non_ortho else None,
            average_non_orthogonality=float(non_ortho.group(2)) if non_ortho else None,
            max_skewness=max(map(float, skewness_matches)) if skewness_matches else None,
            failed_checks=int(failed.group(1)) if failed else 0,
            mesh_ok=bool(self._ok.search(text)),
            raw_log_path=str(path) if path else "",
        )
