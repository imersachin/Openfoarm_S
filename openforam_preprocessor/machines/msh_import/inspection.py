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

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.config import PatchType
from machines.msh_import import surface
from machines.msh_import.detect import UNNAMED_PREFIX, MeshFileInfo, Point
from machines.msh_import.validation import read_mesh_file
from machines.patches import suggest_region_types

BOX_FROM_FILE = "read from the file"
BOX_FROM_CONVERSION = "from the converted mesh"
# Fluent zone types that suggest a patch type (a suggestion only). Not
# pressure-inlet or pressure-outlet: foamMeshToFluent writes every non-wall
# patch as pressure-outlet (E0), and a far field may be either.
_FLUENT_TYPES = {"wall": PatchType.WALL, "velocity-inlet": PatchType.INLET,
                 "mass-flow-inlet": PatchType.INLET, "outflow": PatchType.OUTLET,
                 "symmetry": PatchType.SLIP}
# Words of a patch name that say inlet or outlet (type_notes): "SC-Inlet", "DT_INLET".
_FLOW_WORDS = frozenset({"inlet", "outlet", "inflow", "outflow"})
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
        if re.fullmatch(rf"{UNNAMED_PREFIX}\d+", name):
            continue  # the number is the converter's zone id, not a pair (M2a: GV)
        for a, b in _PAIR_ENDINGS:
            if name.endswith(a) and len(name) > len(a):
                other = name[: -len(a)] + b
                if other in names and not re.search(r"(?i)wall", name):
                    found.append((name, other, f"names ending in '{a}' and '{b}'"))
    return tuple(found)


def type_notes(info: MeshFileInfo) -> tuple[Issue, ...]:
    """INFO when a patch's name says inlet or outlet and the file types it wall
    (owner, M2a: the Francis casing's SC-Inlet and SV-Outlet are walls in the
    file): the file's types are not final."""
    odd = [n for n in info.patches if info.patch_kinds.get(n) == "wall"
           and _FLOW_WORDS & set(re.split(r"[^a-z0-9]+", n.lower()))]
    if not odd:
        return ()
    return (Issue(
        category=IssueCategory.INPUT, severity=IssueSeverity.INFO,
        stage=IssueStage.MESH_IMPORT, code="MSH_TYPE_FROM_FILE",
        message=f"{', '.join(odd)}: the name says inlet or outlet, the file types "
                f"{'it' if len(odd) == 1 else 'them'} wall.",
        suggested_action="Choose each patch's type in the patch table; a type from the "
                         "file is never final (Fluent exports often type every zone wall).",
        details={"patches": odd}),)


def inspect(path: Path, scratch_box: tuple[Point, Point] | None = None) -> MeshInspection:
    """The file as read, with suggestions. `scratch_box`: the box of a scratch
    conversion of this file, used when the file's own box cannot be read."""
    info, digest = read_mesh_file(path)
    box, source = (info.box, BOX_FROM_FILE) if info.box else (
        (scratch_box, BOX_FROM_CONVERSION) if scratch_box else (None, ""))
    if not info.readable:
        return MeshInspection(info, digest, box, source, issues=info.issues)
    return MeshInspection(info, digest, box, source, suggest_types(info), suggest_pairs(info),
                          (*info.issues, *type_notes(info)))


@dataclass(frozen=True)
class PatchGeometry:
    name: str
    faces: int
    area: float  # m^2 (in the mesh's units)
    centroid: Point


def patch_geometry(folder: Path, names: tuple[str, ...]) -> dict[str, PatchGeometry]:
    """Area and area-weighted centroid of patches of a converted mesh, from its
    extracted surface (surface.py): the evidence shown beside a proposed AMI pair."""
    return {name: PatchGeometry(name, shape.faces, shape.area, shape.centroid)
            for name, shape in surface.patch_shapes(folder, names).items()}
