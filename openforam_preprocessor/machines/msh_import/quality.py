"""Two-level checkMesh for an imported mesh (owner, M2a; docs/msh_import_notes.md
section 13.3). STL and VAWT meshes keep the one-level rule.

checkMesh's failed checks are classified line by line:

- BLOCKING: zero or negative cell volumes, open cells, wrongly oriented faces
  (the face-pyramid check), open boundary, face pyramid volume below minVol
- WARNING: skewness, non-orthogonality, aspect ratio, concave cells, and the
  -meshQuality rows face-decomposition tet quality, interpolation weight,
  cell determinant, volume ratio, face twist (owner, after evidence 1)
- anything else is BLOCKING, and so is a failure count the lines do not
  explain (checkMesh's "Failed N mesh checks" against the `***` lines and the
  -meshQuality table, which counts as one failed check and has no `***` line)

A WARNING says where: per cell zone, the cells past the limit, their share
and the worst value, from the cell fields checkMesh writes (-writeFields).
Only the fields whose counts agree with checkMesh's own (evidence 9:
nonOrthoAngle, skewness, faceWeight, aspectRatio) are used; tet quality,
determinant, volume ratio, twist and concavity have no matching field and
are given as checkMesh's face totals. The bad cells and faces are written by
-writeSets vtk under postProcessing/ for ParaView.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BLOCKING, WARNING = "BLOCKING", "WARNING"
# (class, `***` line pattern); the first that matches classifies the line.
_LINES: tuple[tuple[str, str, str], ...] = (
    (BLOCKING, "negative or zero cell volumes", r"zero or negative cell volume"),
    (BLOCKING, "open cells", r"open cells found"),
    (BLOCKING, "wrongly oriented faces", r"incorrectly oriented|wrongly oriented"),
    (BLOCKING, "open boundary", r"boundary openness"),
    # Faces over 90 deg: on neither of the owner's lists, so BLOCKING; named
    # (evidence 1: they come with the face-pyramid errors of an inverted cell).
    (BLOCKING, "faces over 90 deg non-orthogonal", r"non-orthogonality errors"),
    (WARNING, "skewness", r"max skewness"),
    (WARNING, "non-orthogonality", r"severely non-orthogonal"),
    (WARNING, "aspect ratio", r"high aspect ratio"),
    (WARNING, "concave cells", r"concave cells"),
    (WARNING, "cell determinant", r"small determinant"),
    (WARNING, "interpolation weight", r"small interpolation weight"),
)
# (class, -meshQuality table row pattern)
_ROWS: tuple[tuple[str, str, str], ...] = (
    (BLOCKING, "face pyramid volume", r"face pyramid volume"),
    (WARNING, "non-orthogonality", r"non-orthogonality"),
    (WARNING, "tet quality", r"tet quality"),
    (WARNING, "concave cells", r"concavity"),
    (WARNING, "skewness", r"skewness"),
    (WARNING, "interpolation weight", r"interpolation weight"),
    (WARNING, "volume ratio", r"volume ratio"),
    (WARNING, "face twist", r"face twist"),
    (WARNING, "cell determinant", r"determinant"),
)
_FAILED = re.compile(r"Failed\s+(\d+)\s+mesh\s+checks", re.IGNORECASE)
_STARS = re.compile(r"^\s*\*\*\*\s*(.+?)\s*$", re.MULTILINE)
_TABLE = re.compile(r"Checking faces in error\s*:\s*\n(.*?)(?:\n\s*<<|\n\s*\n)", re.DOTALL)
_ROW = re.compile(r"^\s*(.+?)\s*:\s*(\d+)\s*$")

# Per-zone fields (checkMesh -writeFields): class -> (field, "max" or "min").
FIELDS: dict[str, tuple[str, str]] = {
    "non-orthogonality": ("nonOrthoAngle", "max"),
    "skewness": ("skewness", "max"),
    "interpolation weight": ("faceWeight", "min"),
    "aspect ratio": ("aspectRatio", "max"),
}
ASPECT_LIMIT = 1000.0  # checkMesh's own (primitiveMesh::checkCellAspectRatio)
WRITE_FIELDS = "(" + " ".join(f for f, _ in FIELDS.values()) + ")"
NO_ZONE = "(no cell zone)"


@dataclass(frozen=True)
class Finding:
    level: str  # BLOCKING or WARNING
    kind: str  # the class, e.g. "skewness"
    text: str  # checkMesh's line or table row
    count: int | None = None  # faces (a table row) when given


def classify(log: str) -> tuple[Finding, ...]:
    """The failed checks of a checkMesh log, each classified; () for a mesh
    that passed. An unexplained failure count is a BLOCKING "unclassified"."""
    failed = _FAILED.search(log)
    if failed is None:
        return ()
    found: list[Finding] = []
    for line in _STARS.findall(log):
        rule = next(((lvl, kind) for lvl, kind, pattern in _LINES
                     if re.search(pattern, line, re.IGNORECASE)), None)
        level, kind = rule if rule is not None else (BLOCKING, "unclassified")
        found.append(Finding(level, kind, line))
    table = _TABLE.search(log)
    rows = []
    for row in (table[1].splitlines() if table else ()):
        match = _ROW.match(row)
        if match and int(match[2]) > 0:
            rows.append((match[1], int(match[2])))
    for text, count in rows:
        rule = next(((lvl, kind) for lvl, kind, pattern in _ROWS
                     if re.search(pattern, text, re.IGNORECASE)), None)
        level, kind = rule if rule is not None else (BLOCKING, "unclassified")
        found.append(Finding(level, kind, f"{text}: {count}", count))
    explained = len(found) - len(rows) + (1 if rows else 0)
    total = int(failed[1])
    if total != explained:
        found.append(Finding(BLOCKING, "unclassified",
                             f"checkMesh reported {total} failed checks; {explained} are "
                             "explained by its lines"))
    return tuple(found)


# --- per cell zone --------------------------------------------------------------------------

_LIST = re.compile(rb"(\d+)\s*\(")


def _payload(data: bytes, start: int, kind: str, binary: bool) -> tuple[np.ndarray, int]:
    """The list starting at `start` (its count), and where it ends."""
    match = _LIST.match(data, start)
    if match is None:
        raise ValueError("no list")
    count = int(match[1])
    if binary:
        size = np.dtype(kind).itemsize
        end = match.end() + size * count
        return np.frombuffer(data[match.end():end], dtype=kind).copy(), end + 1
    end = data.index(b")", match.end())
    return np.array(data[match.end():end].split(), dtype=kind), end + 1


def _format(data: bytes) -> tuple[bool, str, str]:
    """(binary, label dtype, scalar dtype) from the FoamFile header."""
    head = data[:data.index(b"}", data.index(b"FoamFile")) + 1]
    binary = re.search(rb"format\s+binary", head) is not None
    label = re.search(rb"label=(\d+)", head)
    scalar = re.search(rb"scalar=(\d+)", head)
    return (binary, "<i8" if label and label[1] == b"64" else "<i4",
            "<f4" if scalar and scalar[1] == b"32" else "<f8")


def read_cell_field(path: Path) -> np.ndarray | float | None:
    """A volScalarField's internal values (binary or text); a float when
    uniform; None when unreadable."""
    try:
        data = path.read_bytes()
        binary, _, scalar = _format(data)
        found = re.search(rb"internalField\s+(nonuniform\s+List<scalar>\s*|uniform\s+)", data)
        if found is None:
            return None
        if found[1].startswith(b"uniform"):
            return float(data[found.end():data.index(b";", found.end())])
        return _payload(data, found.end(), scalar, binary)[0]
    except (OSError, ValueError):
        return None


def read_cell_zones(path: Path) -> dict[str, np.ndarray]:
    """Cell zone name -> its cell labels (binary or text cellZones); {} if none."""
    try:
        data = path.read_bytes()
        binary, label, _ = _format(data)
    except (OSError, ValueError):
        return {}
    zones: dict[str, np.ndarray] = {}
    entry = re.compile(rb"\n\s*([^\s{}()]+)\s*\n\s*\{\s*type\s+cellZone;\s*cellLabels\s+"
                       rb"List<label>\s*")
    position = 0
    while (match := entry.search(data, position)) is not None:
        try:
            labels, position = _payload(data, match.end(), label, binary)
        except ValueError:
            uniform = re.match(rb"(\d+)\s*\{\s*(\d+)\s*\}", data[match.end():])
            if uniform is None:
                break
            labels = np.full(int(uniform[1]), int(uniform[2]), dtype=np.int64)
            position = match.end() + uniform.end()
        zones[match[1].decode("utf-8", errors="replace")] = labels
    return zones


@dataclass(frozen=True)
class ZoneQuality:
    zone: str
    cells: int
    bad: int  # cells past the limit
    worst: float

    @property
    def share(self) -> float:
        return 100.0 * self.bad / self.cells if self.cells else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"zone": self.zone, "cells": self.cells, "cells_past_limit": self.bad,
                "percent": round(self.share, 4), "worst": self.worst}


def limits_of(quality_dict: Mapping[str, float]) -> dict[str, tuple[str, float]]:
    """class -> ("above" | "below", limit) from the meshQualityDict values."""
    return {"non-orthogonality": ("above", float(quality_dict["maxNonOrtho"])),
            "skewness": ("above", float(quality_dict["maxInternalSkewness"])),
            "interpolation weight": ("below", float(quality_dict["minFaceWeight"])),
            "aspect ratio": ("above", ASPECT_LIMIT)}


def zone_quality(case: Path, kinds: set[str], limits: Mapping[str, tuple[str, float]],
                 ) -> dict[str, list[ZoneQuality]]:
    """class -> per cell zone (cells in no zone as NO_ZONE) for each class in
    `kinds` with a field; the fields read one at a time from constant/."""
    zones = read_cell_zones(case / "constant" / "polyMesh" / "cellZones")
    found: dict[str, list[ZoneQuality]] = {}
    for kind in sorted(kinds & set(FIELDS)):
        name, worst = FIELDS[kind]
        values = read_cell_field(case / "constant" / name)
        if not isinstance(values, np.ndarray) or not len(values):
            continue
        side, limit = limits[kind]
        past = values > limit if side == "above" else values < limit
        rows = []
        zoned = np.zeros(len(values), dtype=bool)
        for zone, labels in sorted(zones.items()):
            labels = labels[(labels >= 0) & (labels < len(values))]
            zoned[labels] = True
            if len(labels):
                own = values[labels]
                rows.append(ZoneQuality(zone, len(labels), int(past[labels].sum()),
                                        float(own.max() if worst == "max" else own.min())))
        rest = ~zoned
        if rest.any():
            own = values[rest]
            rows.append(ZoneQuality(NO_ZONE, int(rest.sum()), int(past[rest].sum()),
                                    float(own.max() if worst == "max" else own.min())))
        found[kind] = rows
    return found


def remove_outputs(case: Path) -> list[str]:
    """Delete what checkMesh wrote that is not kept: the cell fields, the region
    sets and cellToRegion (312 MB of region sets on the 23 M-cell machine).
    The vtk sets under postProcessing/ stay. Returns the relative paths."""
    removed = []
    for path in [*(case / "constant" / f for f, _ in FIELDS.values()),
                 case / "constant" / "cellToRegion",
                 *sorted((case / "constant" / "polyMesh" / "sets").glob("region[0-9]*"))]:
        if path.is_file():
            path.unlink()
            removed.append(path.relative_to(case).as_posix())
    return removed
