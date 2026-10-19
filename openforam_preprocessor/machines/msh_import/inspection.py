"""What the UI shows about a chosen .msh file before it is imported (M1-MSH).

Plain data for MachineService: the detection, the content hash, the box (read
from the file, or "from the converted mesh" of a scratch conversion), and
suggestions the user confirms: patch types from the names (and Fluent zone
types), AMI pairs from Fluent interface zones or paired names. Nothing here
is applied by itself, and nothing feeds a run except through the
configuration the user saves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.issues import Issue
from machines.config import PatchType
from machines.mesh_folder import poly_mesh_dir
from machines.msh_import.detect import MeshFileInfo, Point
from machines.msh_import.validation import read_mesh_file
from machines.patches import suggest_region_types
from visualization.foam_reader import FoamReadError, read_boundary, read_faces, read_points

BOX_FROM_FILE = "read from the file"
BOX_FROM_CONVERSION = "from the converted mesh"
# Fluent zone types that suggest a patch type (a suggestion only). Not
# pressure-inlet or pressure-outlet: foamMeshToFluent writes every non-wall
# patch as pressure-outlet (E0), and a far field may be either.
_FLUENT_TYPES = {"wall": PatchType.WALL, "velocity-inlet": PatchType.INLET,
                 "mass-flow-inlet": PatchType.INLET, "outflow": PatchType.OUTLET,
                 "symmetry": PatchType.SLIP}
# Name pairs that suggest an AMI pair: <base><a> / <base><b>.
_PAIR_ENDINGS = (("_stat", "_rot"), ("_stationary", "_rotating"), ("1", "2"), ("_a", "_b"),
                 ("-stat", "-rot"))


@dataclass(frozen=True)
class MeshInspection:
    info: MeshFileInfo
    sha256: str | None
    box: tuple[Point, Point] | None
    box_source: str  # BOX_FROM_FILE, BOX_FROM_CONVERSION, or "" (no box yet)
    suggested_types: dict[str, PatchType] = field(default_factory=dict)
    suggested_pairs: tuple[tuple[str, str, str], ...] = ()  # first, second, why
    issues: tuple[Issue, ...] = ()


def suggest_types(info: MeshFileInfo) -> dict[str, PatchType]:
    """Patch -> suggested type, by name first, else by Fluent zone type."""
    by_name = suggest_region_types(info.patches)
    by_kind = {n: _FLUENT_TYPES[k] for n, k in info.patch_kinds.items() if k in _FLUENT_TYPES}
    return {n: by_name.get(n) or by_kind[n] for n in info.patches
            if n in by_name or n in by_kind}


def suggest_pairs(info: MeshFileInfo) -> tuple[tuple[str, str, str], ...]:
    """AMI pairs to offer (never applied): exactly two Fluent interface zones, or
    names that differ only by a paired ending (ami_stat / ami_rot, AMI1 / AMI2)."""
    interfaces = [n for n in info.patches if info.patch_kinds.get(n) == "interface"]
    if len(interfaces) == 2:
        return ((interfaces[0], interfaces[1], "the two Fluent interface zones"),)
    found: list[tuple[str, str, str]] = []
    names = set(info.patches)
    for name in info.patches:
        for a, b in _PAIR_ENDINGS:
            if name.endswith(a) and len(name) > len(a):
                other = name[: -len(a)] + b
                if other in names and not re.search(r"(?i)wall", name):
                    found.append((name, other, f"names ending in '{a}' and '{b}'"))
    return tuple(found)


def inspect(path: Path, scratch_box: tuple[Point, Point] | None = None) -> MeshInspection:
    """The file as read, with suggestions. `scratch_box`: the box of a scratch
    conversion of this file, used when the file's own box cannot be read."""
    info, digest = read_mesh_file(path)
    box, source = (info.box, BOX_FROM_FILE) if info.box else (
        (scratch_box, BOX_FROM_CONVERSION) if scratch_box else (None, ""))
    if not info.readable:
        return MeshInspection(info, digest, box, source, issues=info.issues)
    return MeshInspection(info, digest, box, source, suggest_types(info), suggest_pairs(info),
                          info.issues)


@dataclass(frozen=True)
class PatchGeometry:
    name: str
    faces: int
    area: float  # m^2 (in the mesh's units)
    centroid: Point


def patch_geometry(folder: Path, names: tuple[str, ...]) -> dict[str, PatchGeometry]:
    """Area and area-weighted centroid of patches of a converted mesh: the
    evidence shown beside a proposed AMI pair."""
    poly = poly_mesh_dir(folder)
    try:
        patches = {p.name: p for p in read_boundary(poly / "boundary")}
        points = read_points(poly / "points")
        faces = read_faces(poly / "faces")
    except FoamReadError:
        return {}
    found: dict[str, PatchGeometry] = {}
    for name in names:
        patch = patches.get(name)
        if patch is None:
            continue
        total, weighted = 0.0, np.zeros(3)
        for face in faces[patch.start_face:patch.start_face + patch.n_faces]:
            corners = points[face]
            centre = corners.mean(axis=0)
            nxt = np.roll(corners, -1, axis=0)
            areas = 0.5 * np.linalg.norm(np.cross(corners - centre, nxt - centre), axis=1)
            # Each fan triangle's centroid, weighted by its area.
            centroids = (corners + nxt + centre) / 3
            total += float(areas.sum())
            weighted += (centroids * areas[:, None]).sum(axis=0)
        centroid = (weighted / total).tolist() if total else [0.0, 0.0, 0.0]
        found[name] = PatchGeometry(name, patch.n_faces, total,
                                    (centroid[0], centroid[1], centroid[2]))
    return found
