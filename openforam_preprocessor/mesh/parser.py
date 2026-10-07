from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


@dataclass(frozen=True)
class PatchSummary:
    name: str
    faces: int
    points: int


@dataclass(frozen=True)
class CheckMeshMetrics:
    """Structured checkMesh output. None means 'not reported', not 'zero'."""

    cells: int | None = None
    faces: int | None = None
    points: int | None = None
    boundary_patches: int | None = None
    patches: tuple[PatchSummary, ...] = ()
    max_non_orthogonality: float | None = None
    average_non_orthogonality: float | None = None
    severely_non_orthogonal_faces: int | None = None
    max_skewness: float | None = None
    max_aspect_ratio: float | None = None
    min_volume: float | None = None
    max_volume: float | None = None
    negative_volume_cells: int | None = None
    zero_or_negative_volumes: bool = False
    failed_checks: int = 0
    failed_check_messages: tuple[str, ...] = ()
    warning_messages: tuple[str, ...] = ()
    mesh_ok: bool = False
    recognized: bool = False  # output looked like checkMesh at all
    raw_log_path: str = ""


class CheckMeshParser:
    """Tolerant parser: unknown or missing lines leave fields as None."""

    _M = re.IGNORECASE | re.MULTILINE
    _cells = re.compile(r"^\s*cells:\s+(\d+)", _M)
    _faces = re.compile(r"^\s*faces:\s+(\d+)", _M)
    _points = re.compile(r"^\s*points:\s+(\d+)", _M)
    _boundary_patches = re.compile(r"^\s*boundary patches:\s+(\d+)", _M)
    _non_ortho = re.compile(
        rf"Mesh non-orthogonality Max:\s*({_NUM})\s+average:\s*({_NUM})", re.IGNORECASE
    )
    _severe_non_ortho = re.compile(
        r"Number of severely non-orthogonal \([^)]*\) faces:\s*(\d+)", re.IGNORECASE
    )
    _skewness = re.compile(rf"Max skewness\s*=\s*({_NUM})", re.IGNORECASE)
    _aspect = re.compile(rf"Max aspect ratio\s*=\s*({_NUM})", re.IGNORECASE)
    _volumes = re.compile(rf"Min volume\s*=\s*({_NUM})\.?\s+Max volume\s*=\s*({_NUM})", re.I)
    _negative_cells = re.compile(r"Number of negative volume cells:\s*(\d+)", re.IGNORECASE)
    _zero_negative = re.compile(r"Zero or negative cell volume detected", re.IGNORECASE)
    _failed = re.compile(r"Failed\s+(\d+)\s+mesh\s+checks", re.IGNORECASE)
    _ok = re.compile(r"^\s*Mesh\s+OK\.", _M)
    _error_line = re.compile(r"^\s*\*\*\*\s*(.+?)\s*$", re.MULTILINE)
    _warning_line = re.compile(r"^\s*\*(?!\*)\s*(.+?)\s*$", re.MULTILINE)
    _patch_header = re.compile(r"^\s*Patch\s+Faces\s+Points", _M)
    _patch_row = re.compile(r"^\s*(\S+)\s+(\d+)\s+(\d+)\b")
    _recognized = re.compile(r"Mesh stats|Checking geometry|Checking topology", re.IGNORECASE)

    def parse_file(self, path: Path) -> CheckMeshMetrics:
        return self.parse_text(path.read_text(encoding="utf-8", errors="replace"), path)

    def parse_text(self, text: str, path: Path | None = None) -> CheckMeshMetrics:
        def integer(pattern: re.Pattern[str]) -> int | None:
            match = pattern.search(text)
            return int(match.group(1)) if match else None

        def largest(pattern: re.Pattern[str]) -> float | None:
            values = [float(v) for v in pattern.findall(text)]
            return max(values) if values else None

        non_ortho = self._non_ortho.search(text)
        volumes = self._volumes.search(text)
        failed = self._failed.search(text)

        return CheckMeshMetrics(
            cells=integer(self._cells),
            faces=integer(self._faces),
            points=integer(self._points),
            boundary_patches=integer(self._boundary_patches),
            patches=self._parse_patches(text),
            max_non_orthogonality=float(non_ortho.group(1)) if non_ortho else None,
            average_non_orthogonality=float(non_ortho.group(2)) if non_ortho else None,
            severely_non_orthogonal_faces=integer(self._severe_non_ortho),
            max_skewness=largest(self._skewness),
            max_aspect_ratio=largest(self._aspect),
            min_volume=float(volumes.group(1)) if volumes else None,
            max_volume=float(volumes.group(2)) if volumes else None,
            negative_volume_cells=integer(self._negative_cells),
            zero_or_negative_volumes=bool(self._zero_negative.search(text)),
            failed_checks=int(failed.group(1)) if failed else 0,
            failed_check_messages=tuple(self._error_line.findall(text)),
            warning_messages=tuple(self._warning_line.findall(text)),
            mesh_ok=bool(self._ok.search(text)),
            recognized=bool(self._recognized.search(text)),
            raw_log_path=str(path) if path else "",
        )

    def _parse_patches(self, text: str) -> tuple[PatchSummary, ...]:
        header = self._patch_header.search(text)
        if header is None:
            return ()
        patches: list[PatchSummary] = []
        for line in text[header.end():].splitlines()[1:]:
            if not line.strip():
                break
            row = self._patch_row.match(line)
            if row:
                patches.append(PatchSummary(row.group(1), int(row.group(2)), int(row.group(3))))
        return tuple(patches)
