"""A machine from several .msh files (M2b; docs/m2b_assembly_notes.md).

    cases/part_<P>_convert/  the file converted at scale 1, with its surface
                             (surface.py): made by "Read the parts" or by the
                             run, reused while its record (scratch.json) is this
                             file and this converter. The passage count is
                             detected on it.
    cases/part_<P>/          the part's ring: scaled to metres, prefixed
                             (createPatch, topoSet), copied about the machine axis
                             by doubling (transformPoints, mergeMeshes) and
                             stitched (stitchMesh -perfect), the emptied periodic
                             patches removed (createPatch); checkMesh proves one
                             region, and the boundary no face left on a periodic
                             patch (E0). ring.json records what it was made from.
    cases/imported/          the rings merged (mergeMeshes): what one imported
                             file would be, so the rest of the run is M2a's.

Prefixes (owner, M2b): every patch and cell zone of a part is used as
`<P>_<name>`, and a cell zone `<P>` holds all of the part's cells (the rotating
zone is a part). The file's own cell zones are kept beside it (cells in two
zones: E0's machine had them and solved). A stitch leaves its faceZone
`<P>_<a><P>_<b>CutFaceZone`: kept, shown as a seam (owner).

Passage count (evidence 1.1): two patches of one part are a periodic pair when
a rotation about the machine axis maps one's face centroids onto the other's;
N = 360 / angle, a whole number or BLOCKING. v2512 checks on a 30 deg sector
about an axis off the origin (transformPoints -centre): 12 copies, stitched,
0 faces left, 1 region, "Mesh OK".
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from machines.assembly import Step
from machines.config import MachineParts, MeshPart
from machines.msh_import.cases import EMPTY_PATCH_DICT
from machines.msh_import.surface import BoundarySurface, Point
from openfoam.dictionary import foam_header

RING_RECORD = "ring.json"
PARTS_REPORT = "reports/parts.json"  # what "Read the parts" found, per part
SEAM_SUFFIX = "CutFaceZone"  # stitchMesh's faceZone of the stitched faces
# A rotation maps a patch onto another when every centroid lands within this
# share of the part's size of one of the other's (evidence 1.1: 1.6e-7 deg spread).
MATCH_TOLERANCE = 1e-5
SAME_PITCH_DEG = 1e-3  # pairs of one part whose angles differ by more: BLOCKING
WHOLE = 1e-3  # 360 / pitch within this of a whole number
# Peak RAM and time of the assembly (copies, stitches, merges), per cell of the
# assembled machine, from E0 (v2512): rings GV 7.9 M 61 s / 7.5 GB, RU 10.8 M
# 136 s / 10.2 GB, the machine merges 133 s / 18.2 GB at 23 M.
ASSEMBLY_RAM_PER_CELL = 950.0
ASSEMBLY_S_PER_CELL = 14.4e-6


def part_case(prefix: str) -> str:
    return f"part_{prefix}"


def convert_case(prefix: str) -> str:
    return f"part_{prefix}_convert"


def prefixed(prefix: str, name: str) -> str:
    return f"{prefix}_{name}"


def axis_point(mesh: MachineParts) -> tuple[Point, Point]:
    """(origin, unit direction) of the machine axis."""
    origin = (mesh.origin.x, mesh.origin.y, mesh.origin.z)
    value = mesh.axis.value
    return origin, (float(value == "x"), float(value == "y"), float(value == "z"))


def in_file_units(point: Point, to_metres: float) -> Point:
    """A point in metres in a file's own units (its conversion is at scale 1)."""
    return (point[0] / to_metres, point[1] / to_metres, point[2] / to_metres)


# --- the passage count -------------------------------------------------------------------------

@dataclass(frozen=True)
class Periodicity:
    """What the rotations about the axis say of one part (on its own surface)."""

    pairs: tuple[tuple[str, str, float], ...]  # (first, second, deg): second = first turned
    pitch_deg: float | None  # the mean angle of the pairs
    spread_deg: float  # the largest minus the smallest angle
    passages: int | None  # 360 / pitch when it is a whole number
    problem: str = ""  # why there is no passage count although pairs were found

    @property
    def whole_ring(self) -> bool:
        return not self.pairs

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(found: Any) -> Periodicity | None:
        try:
            return Periodicity(tuple((str(a), str(b), float(d)) for a, b, d in found["pairs"]),
                               None if found["pitch_deg"] is None else float(found["pitch_deg"]),
                               float(found["spread_deg"]),
                               None if found["passages"] is None else int(found["passages"]),
                               str(found.get("problem", "")))
        except (KeyError, TypeError, ValueError):
            return None


def _frame(axis: Point) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The axis and two unit vectors across it (right-handed: e1 x e2 = axis)."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    helper = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = helper - (helper @ a) * a
    e1 /= np.linalg.norm(e1)
    return a, e1, np.cross(a, e1)


@dataclass(frozen=True)
class _Patch:
    name: str
    centroids: np.ndarray  # (n, 3), relative to the origin
    radius: np.ndarray
    theta: np.ndarray


def _turn(points: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues' rotation of the points about the axis through 0."""
    cos, sin = math.cos(angle), math.sin(angle)
    return (points * cos + np.cross(axis, points) * sin
            + np.outer(points @ axis, axis) * (1 - cos))


def _match(a: _Patch, b: _Patch, frame: tuple[np.ndarray, np.ndarray, np.ndarray],
           size: float) -> float | None:
    """The angle (rad) about the axis turning a's centroids onto b's, if one does."""
    from scipy.spatial import cKDTree

    if len(a.centroids) != len(b.centroids) or not len(a.centroids):
        return None
    if abs(float(np.median(a.radius)) - float(np.median(b.radius))) > 1e-3 * size:
        return None
    mean_a, mean_b = np.mean(np.exp(1j * a.theta)), np.mean(np.exp(1j * b.theta))
    if min(abs(mean_a), abs(mean_b)) < 1e-6:  # around the whole axis: no angle of its own
        return None
    delta = float(np.angle(mean_b / mean_a))
    if abs(delta) < 1e-6:  # the same faces twice (a baffle pair), not a rotation
        return None
    axis, e1, e2 = frame
    tree = cKDTree(b.centroids)
    for _ in range(2):  # refine on the matched centroids
        miss, nearest = tree.query(_turn(a.centroids, axis, delta))
        if miss.max() > 1e-3 * size:
            return None
        turned = _turn(a.centroids, axis, delta)
        lag = np.angle(np.exp(1j * (b.theta[nearest]
                                    - np.arctan2(turned @ e2, turned @ e1))))
        delta += float(np.mean(lag))
    miss, _ = tree.query(_turn(a.centroids, axis, delta))
    return delta if miss.max() < MATCH_TOLERANCE * size else None


def detect(surface: BoundarySurface, origin: Point, axis: Point) -> Periodicity:
    """The periodic pairs of a part about the axis through origin, and N."""
    frame = _frame(axis)
    a, e1, e2 = frame
    size = float(np.ptp(surface.points, axis=0).max()) if len(surface.points) else 0.0
    patches = []
    for name in surface.patches:
        faces = surface.faces(name)
        if not len(faces):
            continue
        _, centroids = surface.area_vectors(faces)
        relative = centroids - np.asarray(origin, dtype=float)
        radial = relative - np.outer(relative @ a, a)
        patches.append(_Patch(name, relative, np.linalg.norm(radial, axis=1),
                              np.arctan2(relative @ e2, relative @ e1)))
    found: list[tuple[str, str, float]] = []
    used: set[str] = set()
    for i, first in enumerate(patches):
        if first.name in used:
            continue
        for second in patches[i + 1:]:
            if second.name in used:
                continue
            angle = _match(first, second, frame, size)
            if angle is None:
                continue
            # Ordered so that the second side is the first turned by a positive angle.
            pair = ((first.name, second.name, math.degrees(angle)) if angle > 0
                    else (second.name, first.name, -math.degrees(angle)))
            found.append(pair)
            used |= {first.name, second.name}
            break
    if not found:
        return Periodicity((), None, 0.0, 1)
    angles = [d for *_, d in found]
    pitch = float(np.mean(angles))
    spread = max(angles) - min(angles)
    if spread > SAME_PITCH_DEG:
        return Periodicity(tuple(found), pitch, spread, None,
                           "the pairs turn by different angles: "
                           + ", ".join(f"{p} -> {q} {d:.6f} deg" for p, q, d in found))
    count = 360.0 / pitch
    if round(count) < 2 or abs(count - round(count)) > WHOLE:
        return Periodicity(tuple(found), pitch, spread, None,
                           f"360 / {pitch:.6f} deg = {count:.4f} is not a whole number")
    return Periodicity(tuple(found), pitch, spread, round(count))


# --- the commands of one part --------------------------------------------------------------------

def prefix_patch_dict(prefix: str, patches: Sequence[tuple[str, str]]) -> str:
    """createPatch renaming every patch to <P>_<name>, keeping its type (a
    renamed patch takes its type from the dictionary; v2512)."""
    entries = "\n".join(
        f"    {{ name {prefixed(prefix, name)}; patchInfo {{ type {kind}; }} "
        f"constructFrom patches; patches ({name}); }}" for name, kind in patches)
    return foam_header("createPatchDict") + f"\npointSync false;\n\npatches\n(\n{entries}\n);\n"


def prefix_zone_dict(prefix: str, zones: Sequence[str]) -> str:
    """topoSet: each cell zone again as <P>_<zone>, the old one removed, the
    sets removed; a file without a cell zone gets <P>_cells of all its cells
    (an empty set inverted). The zone <P> is made after the merge
    (cases.part_zones_dict)."""
    actions: list[str] = []
    for zone in zones:
        new = prefixed(prefix, zone)
        actions += [
            f"{{ name {new}; type cellSet; action new; source zoneToCell; zones ({zone}); }}",
            f"{{ name {new}; type cellZoneSet; action new; source setToCellZone; set {new}; }}"]
    actions += [f"{{ name {zone}; type cellZoneSet; action remove; }}" for zone in zones]
    actions += [f"{{ name {prefixed(prefix, z)}; type cellSet; action remove; }}" for z in zones]
    if not zones:
        own = prefixed(prefix, "cells")
        actions += [
            f"{{ name {own}; type cellSet; action new; source labelToCell; value (); }}",
            f"{{ name {own}; type cellSet; action invert; }}",
            f"{{ name {own}; type cellZoneSet; action new; source setToCellZone; set {own}; }}",
            f"{{ name {own}; type cellSet; action remove; }}"]
    body = "\n".join(f"    {a}" for a in actions)
    return foam_header("topoSetDict") + f"\nactions\n(\n{body}\n);\n"


def _vector(v: Point) -> str:
    return "(" + " ".join(f"{c:.17g}" for c in v) + ")"


def _rotate(case: str, label: str, angle: float, origin: Point, axis: Point) -> Step:
    return Step(f"{case}_rotate_{label}", "run", f"cases/{case}", (
        "transformPoints", "-case", ".", "-centre", _vector(origin),
        "-rotate-angle", f"({_vector(axis)} {angle:.17g})"))


def _merge(case: str, label: str, other: str) -> Step:
    return Step(f"{case}_merge_{label}", "run", f"cases/{case}",
                ("mergeMeshes", "-overwrite", ".", f"../{other}"))


def copy_steps(prefix: str, passages: int, origin: Point, axis: Point) -> tuple[Step, ...]:
    """The ring from one passage in cases/<part_case>/ by doubling (E0): blocks
    of 1, 2, 4 ... passages, each the last one merged with a copy of itself
    turned by its own span; then the ring is the largest block, and the smaller
    blocks are added turned to their place. "copy_case": the mesh of `source`
    into `case` (made fresh); "remove": the case deleted."""
    ring = part_case(prefix)
    if passages <= 1:
        return ()
    pitch = 360.0 / passages
    block = [f"{ring}_b{k}" for k in range(passages.bit_length())]
    tmp = f"{ring}_tmp"
    steps = [Step(f"{block[0]}_copy", "copy_case", f"cases/{block[0]}", source=f"cases/{ring}")]
    size, k = 1, 0
    while size * 2 <= passages:
        steps += [Step(f"{block[k + 1]}_copy", "copy_case", f"cases/{block[k + 1]}",
                       source=f"cases/{block[k]}"),
                  Step(f"{tmp}_copy_b{k}", "copy_case", f"cases/{tmp}", source=f"cases/{block[k]}"),
                  _rotate(tmp, f"b{k + 1}", size * pitch, origin, axis),
                  _merge(block[k + 1], f"b{k + 1}", tmp)]
        size, k = size * 2, k + 1
    steps.append(Step(f"{ring}_copy_b{k}", "copy_case", f"cases/{ring}",
                      source=f"cases/{block[k]}"))
    have = size
    for bit in reversed(range(k)):
        if (passages - have) >= 2**bit:
            steps += [Step(f"{tmp}_copy_add{2**bit}", "copy_case", f"cases/{tmp}",
                           source=f"cases/{block[bit]}"),
                      _rotate(tmp, f"add{2**bit}", have * pitch, origin, axis),
                      _merge(ring, f"add{2**bit}", tmp)]
            have += 2**bit
    assert have == passages
    steps += [Step(f"{c}_remove", "remove", f"cases/{c}") for c in (*block[:k + 1], tmp)]
    return tuple(steps)


def stitch_steps(part: MeshPart) -> tuple[Step, ...]:
    ring = part_case(part.prefix)
    return tuple(Step(f"{ring}_stitch_{p.first}", "run", f"cases/{ring}", (
        "stitchMesh", "-case", ".", "-perfect", "-overwrite",
        prefixed(part.prefix, p.first), prefixed(part.prefix, p.second)))
        for p in part.periodic)


def drop_empty_step(prefix: str) -> Step:
    ring = part_case(prefix)
    return Step(f"{ring}_drop_empty", "run", f"cases/{ring}",
                ("createPatch", "-overwrite", "-case", ".", "-dict", EMPTY_PATCH_DICT))


def check_step(prefix: str) -> Step:
    ring = part_case(prefix)
    return Step(f"{ring}_checkMesh", "run", f"cases/{ring}", ("checkMesh", "-case", "."))


def merge_steps(prefixes: Sequence[str], target: str) -> tuple[Step, ...]:
    """The rings merged into cases/<target>/: the first copied, the others added."""
    steps = [Step(f"{target}_copy_{prefixes[0]}", "copy_case", f"cases/{target}",
                  source=f"cases/{part_case(prefixes[0])}")]
    steps += [_merge(target, p, part_case(p)) for p in prefixes[1:]]
    return tuple(steps)


# --- records ------------------------------------------------------------------------------------

def zone_sizes(folder: Path) -> dict[str, int]:
    """Cells of each cell zone of a mesh (names and list sizes are text in a
    binary cellZones file too)."""
    path = folder / "constant" / "polyMesh" / "cellZones"
    try:
        text = path.read_bytes().decode("latin-1")
    except OSError:
        return {}
    return {name: int(count) for name, count in re.findall(
        r"\n\s*(\S+)\s*\n\s*\{\s*type\s+cellZone;\s*cellLabels\s+(?:List<label>\s*)?(\d+)",
        text)}


def ring_inputs(part: MeshPart, mesh: MachineParts, convert: dict[str, Any],
                tool: str) -> dict[str, Any]:
    """What a ring is made from: a ring whose record has these is reused."""
    origin, axis = axis_point(mesh)
    return {"version": 2,  # 2: the zone <P> is made after the merge, not in the ring
            "convert": convert, "units": part.source_units.value,
            "scale": part.source_units.to_metres, "prefix": part.prefix,
            "passages": part.passages,
            "periodic": [[p.first, p.second] for p in part.periodic],
            "origin": list(origin), "axis": list(axis), "tool": tool}


def ring_record(folder: Path) -> dict[str, Any] | None:
    try:
        found = json.loads((folder / RING_RECORD).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def seams(folder: Path) -> list[tuple[str, int]]:
    """(name, faces) of each stitch faceZone of a mesh (the faceZones file's
    names and list sizes are text in a binary file too)."""
    path = folder / "constant" / "polyMesh" / "faceZones"
    try:
        text = path.read_bytes().decode("latin-1")
    except OSError:
        return []
    found = re.findall(r"\n\s*(\S+" + SEAM_SUFFIX + r")\s*\n\s*\{\s*type\s+faceZone;\s*"
                       r"faceLabels\s+(?:List<label>\s*)?(\d+)", text)
    return [(name, int(count)) for name, count in found]


def read_report(root: Path) -> dict[str, Any]:
    try:
        found = json.loads((root / PARTS_REPORT).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def write_report(root: Path, report: dict[str, Any]) -> None:
    path = root / PARTS_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
