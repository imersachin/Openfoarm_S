"""Interfaces between the parts of a machine of several files (M2b-2;
docs/m2b_assembly_notes.md, evidence 1.3).

An interface joins two sides, each one or more patches of one part (the
runner's inflow is r1_inflow_1 + r1_inflow_2), made in the merged case by
createPatch under its own name, `<name>_<prefix>` per side:

    AMI     cyclicAMI pair (always for a rotating part)
    ACMI    cyclicACMI pair, each side with a wall `<side>_blockage` on the same
            faces for where the other side does not reach (the v2512 tutorial
            mesh/createPatch/TJunctionSwitching_createPatch)
    STITCH  the two sides made one (stitchMesh -perfect): stationary parts only,
            when their faces match one to one

Candidates (suggested, never applied): for each pair of patches of two parts,
the share of each one's area on the other's meridional trace (radius and
position along the machine axis; the turn about the axis does not matter): a
face is on it when its point (the mean of its vertices' radius and position,
exact on a surface of revolution) lies on one of the other's face segments,
within 5 % of its length, so walls meeting at an edge are not; and the full-ring areas
(a passage's area x its passages). Patches that lie mostly on one another's
trace are grouped into the two sides; a pair is suggested when both sides
cover each other at least 80 % and their full-ring areas agree within 2 %.
On the owner's machine (evidence 1.3) the three suggested are exactly E0's
pairs; the next best covers 11.6 %.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.config import InterfaceJoint, MachineParts, MeshInterface
from machines.mesh_folder import parse_ami_weights
from machines.msh_import import surface
from openfoam.dictionary import foam_header

SUGGEST_COVERAGE = 0.80
SUGGEST_AREA = 0.02
GROUP_COVERAGE = 0.5  # a patch lies mostly on another's trace: same side
ON_TRACE = 0.05  # a face's point within this share of a segment's length lies on it
NEAREST = 8  # segments looked at per point (by their middle)
SAMPLE = 400  # faces of a patch looked at first; a pair far below SHOWN_COVERAGE stops there
SHOWN_COVERAGE = 0.05  # candidates below this both ways are not listed
BLOCKAGE = "_blockage"
AMI_TOLERANCE = "0.0001"


def part_of(name: str) -> str:
    """The part prefix of a machine patch name (<prefix>_<name>)."""
    return name.split("_", 1)[0]


def sides(interface: MeshInterface) -> tuple[str, str]:
    """The names of the two patches the interface is made of."""
    return (f"{interface.name}_{part_of(interface.first[0])}",
            f"{interface.name}_{part_of(interface.second[0])}")


def blockage(side: str) -> str:
    return side + BLOCKAGE


def members(mesh: MachineParts) -> set[str]:
    """Every patch of the parts that an interface takes."""
    return {n for i in mesh.interfaces for n in (*i.first, *i.second)}


def coupled(mesh: MachineParts) -> tuple[tuple[str, str], ...]:
    """The cyclicAMI and cyclicACMI pairs the merged mesh has: the chosen AMI
    pairs and each AMI or ACMI interface's two sides."""
    return (*((p.first, p.second) for p in mesh.ami_pairs),
            *(sides(i) for i in mesh.interfaces if i.joint is not InterfaceJoint.STITCH))


def partial(mesh: MachineParts) -> tuple[tuple[str, str], ...]:
    """The cyclicACMI pairs: their sum(weights) falls short of 1 where the sides do
    not overlap, by design (the blockage wall takes those faces)."""
    return tuple(sides(i) for i in mesh.interfaces if i.joint is InterfaceJoint.ACMI)


def weights_notes(pairs: tuple[tuple[str, str], ...], log: str, when: str,
                  every: bool = False) -> list[Issue]:
    """INFO with an ACMI pair's sum(weights) instead of the AMI range check."""
    found = {(w.source, w.target): w for w in parse_ami_weights(log, every)}
    notes = []
    for first, second in pairs:
        w = found.get((first, second)) or found.get((second, first))
        if w is None:
            continue
        notes.append(Issue(
            category=IssueCategory.MESHING, severity=IssueSeverity.INFO,
            stage=IssueStage.MESH_VALIDATION, code="ACMI_WEIGHTS",
            message=f"ACMI sum(weights) of {first} / {second} {when}: {w.minimum:.3g}-"
                    f"{w.maximum:.3g}. Below 1 where the sides do not overlap: the blockage "
                    "walls take those faces.",
            suggested_action="Nothing to do; an AMI pair would need both sides to cover each "
                             "other.", details={"pair": [first, second], "minimum": w.minimum,
                                                "maximum": w.maximum, "when": when}))
    return notes


def expected(mesh: MachineParts) -> dict[str, str]:
    """The patches the interfaces leave in the merged mesh, with their types."""
    found: dict[str, str] = {}
    for interface in mesh.interfaces:
        if interface.joint is InterfaceJoint.STITCH:
            continue
        kind = "cyclicACMI" if interface.joint is InterfaceJoint.ACMI else "cyclicAMI"
        for side in sides(interface):
            found[side] = kind
            if interface.joint is InterfaceJoint.ACMI:
                found[blockage(side)] = "wall"
    return found


def create_patch_entries(mesh: MachineParts) -> list[str]:
    """createPatch entries of the interfaces (beside the renamed patches)."""
    entries: list[str] = []
    for interface in mesh.interfaces:
        a, b = sides(interface)
        for side, other, patches in ((a, b, interface.first), (b, a, interface.second)):
            listed = " ".join(patches)
            if interface.joint is InterfaceJoint.STITCH:
                info = "type            patch;"
            else:
                kind = ("cyclicACMI" if interface.joint is InterfaceJoint.ACMI else "cyclicAMI")
                info = (f"type            {kind};\n            neighbourPatch  {other};\n"
                        "            transform       noOrdering;\n"
                        f"            matchTolerance  {AMI_TOLERANCE};")
                if interface.joint is InterfaceJoint.ACMI:
                    info += f"\n            nonOverlapPatch {blockage(side)};"
            entries.append(f"""    {{
        name {side};
        patchInfo
        {{
            {info}
        }}
        constructFrom patches;
        patches ({listed});
    }}""")
            if interface.joint is InterfaceJoint.ACMI:
                entries.append(f"""    {{
        name {blockage(side)};
        patchInfo
        {{
            type            wall;
        }}
        constructFrom patches;
        patches ({listed});
    }}""")
    return entries


def empty_patch_dict() -> str:
    return foam_header("createPatchDict") + "\npointSync false;\n\npatches ();\n"


# --- candidates ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """Two sides that lie on one another about the axis (suggested only)."""

    first: tuple[str, ...]
    second: tuple[str, ...]
    coverage: tuple[float, float]  # first on second's trace, second on first's
    ring_area: tuple[float, float]  # m^2, the whole ring
    ring_faces: tuple[int, int]
    radius: tuple[tuple[float, float], tuple[float, float]]  # m, each side's range
    axial: tuple[tuple[float, float], tuple[float, float]]  # m, along the axis

    @property
    def parts(self) -> tuple[str, str]:
        return part_of(self.first[0]), part_of(self.second[0])

    @property
    def area_ratio(self) -> float:
        a, b = self.ring_area
        return a / b if b else math.inf

    @property
    def suggested(self) -> bool:
        return (min(self.coverage) >= SUGGEST_COVERAGE
                and abs(self.area_ratio - 1) <= SUGGEST_AREA)

    @property
    def partial(self) -> bool:
        """One side reaches beyond the other: cyclicACMI fits (evidence 1.3)."""
        return min(self.coverage) < 0.99

    @property
    def one_to_one(self) -> bool:
        """The same number of faces on the whole ring: a stitch may be possible."""
        return self.ring_faces[0] == self.ring_faces[1]

    def name(self) -> str:
        a, b = self.parts
        return f"{a}_{b}"


@dataclass(frozen=True)
class _Trace:
    """A patch's faces in the meridional plane (radius, position along the
    axis): each face's point (the mean of its vertices' radius and position:
    exact on a surface of revolution, unlike the centroid's radius) and its
    segment (its two vertices furthest apart there)."""

    part: str
    name: str  # the machine name, <prefix>_<patch>
    meridian: np.ndarray  # (n, 2) the faces' points
    segments: np.ndarray  # (n, 2, 2)
    areas: np.ndarray
    ring_area: float  # area x passages
    ring_faces: int
    tree: Any = None  # of the segments' middles, built once (_trace)
    box: tuple[np.ndarray, np.ndarray] | None = None  # of the segments


def _trace(part: str, name: str, meridian: np.ndarray, segments: np.ndarray,
           areas: np.ndarray, ring_area: float, ring_faces: int) -> _Trace:
    from scipy.spatial import cKDTree

    flat = segments.reshape(-1, 2)
    return _Trace(part, name, meridian, segments, areas, ring_area, ring_faces,
                  cKDTree(segments.mean(axis=1)), (flat.min(axis=0), flat.max(axis=0)))


def _apart(a: _Trace, b: _Trace) -> bool:
    """Their meridional boxes do not meet: neither can lie on the other."""
    assert a.box is not None and b.box is not None
    (alo, ahi), (blo, bhi) = a.box, b.box
    gap = ON_TRACE * max(float((ahi - alo).max()), float((bhi - blo).max()))
    return bool(np.any(alo > bhi + gap) or np.any(blo > ahi + gap))


def _sample(a: _Trace) -> _Trace:
    """At most SAMPLE of a's faces, evenly spread: a quick first look."""
    if len(a.meridian) <= SAMPLE:
        return a
    keep = np.linspace(0, len(a.meridian) - 1, SAMPLE).astype(int)
    return _Trace(a.part, a.name, a.meridian[keep], a.segments[keep], a.areas[keep],
                  a.ring_area, a.ring_faces, a.tree, a.box)


def _coverage(a: _Trace, b: _Trace) -> float:
    """The share of a's area whose faces' points lie on one of b's segments
    (within ON_TRACE of its length): coincident sides cover each other, walls
    that only meet at an edge do not."""
    ends = b.segments
    count = min(NEAREST, len(ends))
    _, nearest = b.tree.query(a.meridian, k=count)
    nearest = nearest.reshape(len(a.meridian), count)
    start, end = ends[nearest, 0], ends[nearest, 1]  # (n, k, 2)
    along = end - start
    length = np.linalg.norm(along, axis=2)
    t = np.clip(np.einsum("nkd,nkd->nk", a.meridian[:, None] - start, along)
                / np.maximum(length**2, 1e-300), 0.0, 1.0)
    miss = np.linalg.norm(start + t[..., None] * along - a.meridian[:, None], axis=2)
    covered = (miss <= ON_TRACE * length + 1e-12).any(axis=1)
    return float(a.areas[covered].sum() / a.areas.sum())


def _join(traces: Sequence[_Trace]) -> _Trace:
    return _trace(traces[0].part, "+".join(t.name for t in traces),
                  np.concatenate([t.meridian for t in traces]),
                  np.concatenate([t.segments for t in traces]),
                  np.concatenate([t.areas for t in traces]), sum(t.ring_area for t in traces),
                  sum(t.ring_faces for t in traces))


def _traces(folder: Path, prefix: str, factor: float, passages: int, skip: set[str],
            origin: np.ndarray, axis: np.ndarray) -> list[_Trace]:
    found = surface.load(folder)
    if found is None:
        return []
    relative = found.points * factor - origin
    along = relative @ axis
    plane = np.c_[np.linalg.norm(relative - np.outer(along, axis), axis=1), along]
    traces = []
    for name in found.patches:
        if name in skip:
            continue
        faces = found.faces(name)
        if not len(faces):
            continue
        areas = found.area_vectors(faces)[0] * factor**2
        meridian = np.zeros((len(faces), 2))
        segments = np.zeros((len(faces), 2, 2))
        for where, labels in found._by_size(faces):
            corners = plane[labels]  # (k, size, 2)
            meridian[where] = corners.mean(axis=1)
            gaps = np.linalg.norm(corners[:, :, None] - corners[:, None], axis=3)
            far = gaps.reshape(len(corners), -1).argmax(axis=1)
            i, j = np.divmod(far, corners.shape[1])
            rows = np.arange(len(corners))
            segments[where] = np.stack([corners[rows, i], corners[rows, j]], axis=1)
        traces.append(_trace(prefix, f"{prefix}_{name}", meridian, segments, areas,
                             float(areas.sum()) * passages, len(faces) * passages))
    return traces


Part = tuple[Path, str, float, int, frozenset[str]]  # folder, prefix, factor, passages, periodic


def candidates(parts: Sequence[Part], origin: tuple[float, float, float],
               axis: tuple[float, float, float]) -> tuple[Candidate, ...]:
    """The interface candidates between the parts' surfaces, best first."""
    stamps = tuple((str(f), *_stamp(f)) for f, *_ in parts)
    return _candidates(tuple(parts), origin, axis, stamps)


def _stamp(folder: Path) -> tuple[int, int]:
    try:
        stat = (surface.surface_dir(folder) / surface.REGIONS_FILE).stat()
    except OSError:
        return (0, 0)
    return stat.st_size, stat.st_mtime_ns


@functools.lru_cache(maxsize=8)
def _candidates(parts: tuple[Part, ...], origin: tuple[float, float, float],
                axis: tuple[float, float, float], stamps: tuple[tuple[str, int, int], ...],
                ) -> tuple[Candidate, ...]:
    centre = np.asarray(origin, dtype=float)
    direction = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    by_part = [_traces(folder, prefix, factor, passages, set(skip), centre, direction)
               for folder, prefix, factor, passages, skip in parts]
    found: list[Candidate] = []
    for i, left in enumerate(by_part):
        for right in by_part[i + 1:]:
            found += _between(left, right)
    return tuple(sorted(found, key=lambda c: (-min(c.coverage), c.first, c.second)))


def _between(left: Sequence[_Trace], right: Sequence[_Trace]) -> list[Candidate]:
    """Groups of patches of two parts lying on each other (a bipartite graph of
    the patches with an edge where one lies mostly on the other's trace)."""
    edges: dict[tuple[int, int], tuple[float, float]] = {}
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            if _apart(a, b) or max(_coverage(_sample(a), b),
                                   _coverage(_sample(b), a)) < SHOWN_COVERAGE / 2:
                continue
            ab, ba = _coverage(a, b), _coverage(b, a)
            if max(ab, ba) >= SHOWN_COVERAGE:
                edges[(i, j)] = (ab, ba)
    groups: list[tuple[set[int], set[int]]] = []
    for (i, j), (ab, ba) in edges.items():
        if max(ab, ba) < GROUP_COVERAGE:
            continue
        hit = [g for g in groups if i in g[0] or j in g[1]]
        merged = ({i}, {j})
        for g in hit:
            merged[0].update(g[0])
            merged[1].update(g[1])
            groups.remove(g)
        groups.append(merged)
    used = {(i, j) for g in groups for i in g[0] for j in g[1]}
    # Weaker overlaps are listed alone, so a wrong grouping can be seen.
    groups += [({i}, {j}) for (i, j) in edges if (i, j) not in used]
    found = []
    for lefts, rights in groups:
        lefts_ = sorted(lefts, key=lambda i: left[i].name)  # by name, as shown
        rights_ = sorted(rights, key=lambda j: right[j].name)
        a = _join([left[i] for i in lefts_])
        b = _join([right[j] for j in rights_])
        found.append(Candidate(
            tuple(left[i].name for i in lefts_), tuple(right[j].name for j in rights_),
            (_coverage(a, b), _coverage(b, a)), (a.ring_area, b.ring_area),
            (a.ring_faces, b.ring_faces),
            ((float(a.meridian[:, 0].min()), float(a.meridian[:, 0].max())),
             (float(b.meridian[:, 0].min()), float(b.meridian[:, 0].max()))),
            ((float(a.meridian[:, 1].min()), float(a.meridian[:, 1].max())),
             (float(b.meridian[:, 1].min()), float(b.meridian[:, 1].max())))))
    return found


def default_joint(candidate: Candidate, rotating: str | None) -> InterfaceJoint:
    """AMI, or ACMI where one side reaches beyond the other (a suggestion)."""
    return InterfaceJoint.ACMI if candidate.partial else InterfaceJoint.AMI


def joints(candidate: Candidate, rotating: str | None) -> tuple[InterfaceJoint, ...]:
    """The joints offered: a stitch only between stationary parts whose faces
    may match one to one (owner's plan)."""
    offered = [InterfaceJoint.AMI, InterfaceJoint.ACMI]
    if candidate.one_to_one and rotating not in candidate.parts:
        offered.append(InterfaceJoint.STITCH)
    return tuple(offered)

