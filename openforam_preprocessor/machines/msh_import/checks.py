"""Checks on a converted .msh mesh (docs/msh_import_notes.md, section 3).

A converter's exit code is not evidence of success (E0: a truncated Fluent
file converts with exit 0 to 538 of 2000 cells). These read the polyMesh it
wrote:

- the cell count against the file's declared count (Fluent header)
- `defaultFaces`: faces in no physical group (gmshToFoam exits 0), with
  their count and bounding box so they can be found in Gmsh
- patches named in the file but missing from the mesh
- the configured rotating cell zones
- after checkMesh: wall baffles ("identical duplicate faces"), which are
  kept as walls (fluent3DMeshToFoam keeps the pair, E0)

The geometry (the box of defaultFaces) comes from the surface the tools
extracted (surface.py): the polyMesh is binary and never read whole (M2a).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.config import MachineProjectConfig
from machines.mesh_folder import poly_mesh_dir
from machines.msh_import import surface
from machines.msh_import.detect import MeshFileInfo, MeshFormat
from visualization.foam_reader import FoamReadError, read_boundary

DEFAULT_FACES = "defaultFaces"  # gmshToFoam's patch for faces in no group (E0)
UNGROUPED = "GMSH_UNGROUPED_FACES"
_COUNTS = re.compile(r"nCells:\s*(\d+)")
_BAFFLES = re.compile(r"identical duplicate faces \(baffle faces\):\s*(\d+)")
# The names are text in a binary cellZones file too (its labels are not; M2a).
_ZONE_NAME = re.compile(r"^\s*(\S+)\s*\n\s*\{\s*type\s+cellZone;", re.MULTILINE)
Point = tuple[float, float, float]


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           explanation: str = "", **details: Any) -> Issue:
    return Issue(category=IssueCategory.MESHING, severity=severity,
                 stage=IssueStage.MESH_IMPORT, code=code, message=message,
                 explanation=explanation, suggested_action=action, details=details)


def mesh_zone_names(folder: Path) -> set[str]:
    """Cell zone names of a mesh, "-" included (Fluent: "fluid-1"), unlike
    machines.mesh_folder.cell_zone_names (word characters only)."""
    path = poly_mesh_dir(folder) / "cellZones"
    if not path.is_file():
        return set()
    return set(_ZONE_NAME.findall(path.read_text(encoding="utf-8", errors="replace")))


def check_zones(folder: Path, zones: Sequence[str], name: str) -> tuple[Issue, ...]:
    """As machines.mesh_folder.check_cell_zones, for imported zone names."""
    present = mesh_zone_names(folder)
    return tuple(_issue(
        IssueSeverity.ERROR, "CELL_ZONE_MISSING", f"The {name} mesh has no cell zone '{zone}'.",
        "Inspect the conversion and createPatch logs.", zone=zone)
        for zone in zones if zone not in present)


def mesh_cells(folder: Path) -> int | None:
    """nCells from the owner file's header note, as OpenFOAM writes it."""
    path = poly_mesh_dir(folder) / "owner"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    match = _COUNTS.search(head)
    return int(match[1]) if match else None


def has_mesh(folder: Path) -> bool:
    """A readable boundary and cells: worth extracting the surface of (else
    check_converted says what is wrong)."""
    try:
        read_boundary(poly_mesh_dir(folder) / "boundary")
    except FoamReadError:
        return False
    return bool(mesh_cells(folder))


def _rounded(point: Point) -> str:
    return "(" + " ".join(f"{v:.6g}" for v in point) + ")"


def check_converted(folder: Path, info: MeshFileInfo, config: MachineProjectConfig,
                    unit: str) -> tuple[Issue, ...]:
    """The converted (and scaled) mesh against the file and the configuration.
    `unit`: the length unit the mesh is in, for the messages."""
    try:
        patches = read_boundary(poly_mesh_dir(folder) / "boundary")
    except FoamReadError as error:
        return (_issue(IssueSeverity.BLOCKING, "MSH_CONVERT_FAILED",
                       f"The converter wrote no readable mesh: {error}",
                       "Read the converter log; export the mesh again."),)
    issues: list[Issue] = []
    cells = mesh_cells(folder)
    if not cells:
        issues.append(_issue(IssueSeverity.BLOCKING, "MSH_CONVERT_FAILED",
                             "The converted mesh has no cells.",
                             "Read the converter log; export the mesh again.", cells=cells))
    elif info.declared_cells is not None and cells != info.declared_cells:
        issues.append(_issue(
            IssueSeverity.BLOCKING, "MSH_CELL_COUNT_MISMATCH",
            f"The converted mesh has {cells} cells; the file declares {info.declared_cells}.",
            "Export the mesh again: the file is probably cut short or damaged.",
            "fluent3DMeshToFoam exits 0 on a file cut short (E0: 538 of 2000 cells).",
            cells=cells, declared=info.declared_cells))
    found = {p.name: p for p in patches}
    default = found.get(DEFAULT_FACES)
    if default is not None and default.n_faces > 0:
        shape = surface.patch_shapes(folder, [DEFAULT_FACES]).get(DEFAULT_FACES)
        box = shape.box if shape is not None else None
        where = (f" between {_rounded(box[0])} and {_rounded(box[1])} {unit}"
                 if box else "")
        issues.append(_issue(
            IssueSeverity.BLOCKING, UNGROUPED,
            f"{default.n_faces} boundary faces{where} are in no physical group; gmshToFoam put "
            f"them in a patch '{DEFAULT_FACES}'.",
            "In Gmsh, add the surfaces in that box to a Physical Surface (a patch), then save "
            "and import again.", "gmshToFoam exits 0 with faces left out of every group (E0).",
            faces=default.n_faces, box=[list(box[0]), list(box[1])] if box else None))
    lost = [name for name in info.patches if name not in found]
    if lost:
        issues.append(_issue(
            IssueSeverity.ERROR, "MSH_PATCH_LOST",
            f"Patches named in the file are not in the converted mesh: {', '.join(lost)}.",
            "Read the converter log; check these zones in the source program.",
            patches=lost))
    zones = mesh_zone_names(folder)
    mesh = config.imported_mesh
    for zone in mesh.zones if mesh else ():
        if zone.cell_zone not in zones:
            issues.append(no_cell_zone(info, zone.cell_zone, sorted(zones)))
    return tuple(issues)


def no_cell_zone(info: MeshFileInfo, name: str | None, found: Sequence[str]) -> Issue:
    """BLOCKING MSH_NO_CELL_ZONE, with the fix for the file's format (owner answer 5)."""
    fix = ("In Gmsh, add a Physical Volume for the rotating region (its own volume, meeting "
           "the rest at the interface surfaces), then save and import again."
           if info.format is MeshFormat.GMSH else
           "In Fluent, make the rotating region its own fluid cell zone (separated from the "
           "rest by interface zones), then export the mesh again.")
    subject = f"no cell zone '{name}'" if name else "no rotating cell zone"
    return _issue(
        IssueSeverity.BLOCKING, "MSH_NO_CELL_ZONE",
        f"The mesh has {subject}; cell zones found: {', '.join(found) or 'none'}.", fix,
        "A rotating machine turns one cell zone of the mesh. No zone is made up from the "
        "mesh's regions (a helper for that is on the later list).",
        zone=name, found=list(found))


def check_baffles(check_mesh_log: str) -> tuple[Issue, ...]:
    """Wall baffles found by checkMesh (kept as two walls; not an interface)."""
    match = _BAFFLES.search(check_mesh_log)
    if not match or int(match[1]) == 0:
        return ()
    count = int(match[1])
    return (_issue(
        IssueSeverity.WARNING, "MSH_BAFFLE_PAIR",
        f"The mesh has {count} baffle faces: two wall patches on the same faces (a Fluent "
        "wall / wall-shadow pair).",
        "Keep them as walls (a thin plate), or, if they are an interface, choose the two "
        "patches as an AMI pair.",
        "fluent3DMeshToFoam keeps both walls; checkMesh reports their faces as identical "
        "duplicate faces (E0). Real Fluent shadow pairs are not tested on real files.",
        baffle_faces=count),)
