"""What a .msh file is, read from its content (docs/msh_import_notes.md).

One streaming pass over the file, never the whole file in memory:

- Gmsh: `$MeshFormat` (version, ASCII or binary), `$PhysicalNames` (surfaces
  become patches, volumes cell zones) and, for ASCII, the `$Nodes` box.
- Fluent: sections in brackets. The dimension `(2 3)`, the declared cell
  count `(12 (0 1 <last> 0))`, the zone names and types `(39 ...)` or
  `(45 ...)` (written at the end of the file, E0), the face zones and their
  boundary types from the face section headers `(13 (id first last type
  ...)` (hex; M2a: a zone with no name is the converter's FaceGroup<id>, as
  all 25 of the owner's guide-vane passage), binary sections (`(20xx`,
  `(30xx`) and, for ASCII, the node box.

The extension is never used. Anything else is BLOCKING `MSH_FORMAT_UNKNOWN`:
the format is never guessed. A file that would crash a converter (missing,
empty: `gmshToFoam` exit 134 in E0) is stopped here, before any conversion.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import itertools
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage

Point = tuple[float, float, float]
_ROWS = 200_000  # node rows parsed at a time (bounded memory)
_HDF5 = b"\x89HDF\r\n\x1a\n"
# Gmsh versions gmshToFoam reads directly when ASCII (E0: 2.2 and 4.1).
DIRECT_GMSH_VERSIONS = ("2.2", "4.1")
# Fluent zone types that are not boundary patches after fluent3DMeshToFoam.
FLUENT_CELL_TYPES = frozenset({"fluid", "solid"})
FLUENT_INTERIOR_TYPES = frozenset({"interior"})

# (39 (id type name)()) or (45 ...); some writers add fields after the name
# ("(39 (12 wall walls 1)())", which fluent3DMeshToFoam reads): ignored.
_FLUENT_ZONE_START = re.compile(rb"^\((39|45)\s")
_FLUENT_ZONE = re.compile(rb"^\((39|45)\s*\(\s*([0-9]+)\s+([A-Za-z0-9_.:+-]+)\s+([^\s()]+)"
                          rb"[^()]*\)")
_FLUENT_DIMENSION = re.compile(rb"^\(2 ([0-9]+)\)")
_FLUENT_CELLS = re.compile(rb"^\(12 \(0 ([0-9a-fA-F]+) ([0-9a-fA-F]+) 0")
_FLUENT_NODES = re.compile(rb"^\(10 \(([0-9a-fA-F]+) ([0-9a-fA-F]+) ([0-9a-fA-F]+) "
                           rb"([0-9a-fA-F]+)(?: ([0-9a-fA-F]+))?\)")
_FLUENT_BINARY = re.compile(rb"^\((20|30)(10|12|13|58|59) ")
# A face section header: (13 (zone first last bc-type face-type), (2013 or (3013 binary.
_FLUENT_FACES = re.compile(rb"^\((?:13|2013|3013)\s*\(\s*([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+"
                           rb"[0-9a-fA-F]+\s+([0-9a-fA-F]+)", re.MULTILINE)
# Fluent boundary condition types (hex in the file), as the zone sections name them.
FLUENT_BC = {2: "interior", 3: "wall", 4: "pressure-inlet", 5: "pressure-outlet",
             7: "symmetry", 8: "periodic-shadow", 9: "pressure-far-field",
             10: "velocity-inlet", 12: "periodic", 14: "fan", 20: "mass-flow-inlet",
             24: "interface", 31: "parent", 36: "outflow", 37: "axis"}
UNNAMED_PREFIX = "FaceGroup"  # fluent3DMeshToFoam's name for a face zone with no name (E0)
_GMSH_NAME = re.compile(r'^\s*(\d+)\s+(\d+)\s+"([^"]*)"\s*$')


class MeshFormat(StrEnum):
    GMSH = "GMSH"
    FLUENT = "FLUENT"


@dataclass(frozen=True)
class MeshFileInfo:
    """The file as read. Names are as the converter will write them."""

    path: Path
    size: int
    format: MeshFormat | None
    version: str = ""  # Gmsh "2.2", "4.1"; Fluent ""
    binary: bool = False
    dimension: int | None = None
    patches: tuple[str, ...] = ()  # boundary patches after conversion
    patch_kinds: dict[str, str] = field(default_factory=dict)  # Fluent zone type per patch
    cell_zones: tuple[str, ...] = ()
    declared_cells: int | None = None  # Fluent header
    nodes: int | None = None  # nodes read for the box
    box: tuple[Point, Point] | None = None  # in the file's units
    box_note: str = ""  # why there is no box
    issues: tuple[Issue, ...] = ()

    @property
    def readable(self) -> bool:
        return self.format is not None and not any(i.is_stopping for i in self.issues)


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           explanation: str = "", **details: Any) -> Issue:
    return Issue(category=IssueCategory.INPUT, severity=severity, stage=IssueStage.MESH_IMPORT,
                 code=code, message=message, explanation=explanation,
                 suggested_action=action, details=details)


def _blocking(code: str, message: str, action: str, explanation: str = "",
              **details: Any) -> Issue:
    return _issue(IssueSeverity.BLOCKING, code, message, action, explanation, **details)


def gmsh_available() -> bool:
    """Whether the optional gmsh package (re-saving files as MSH 2.2) is installed."""
    return importlib.util.find_spec("gmsh") is not None


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """The file's content hash, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


# --- the box --------------------------------------------------------------------------------

class _Box:
    def __init__(self) -> None:
        self.low = np.full(3, np.inf)
        self.high = np.full(3, -np.inf)
        self.count = 0

    def add(self, rows: np.ndarray) -> None:
        if len(rows):
            self.low = np.minimum(self.low, rows.min(axis=0))
            self.high = np.maximum(self.high, rows.max(axis=0))
            self.count += len(rows)

    def result(self) -> tuple[Point, Point] | None:
        if not self.count or not (np.isfinite(self.low).all() and np.isfinite(self.high).all()):
            return None
        low, high = self.low.tolist(), self.high.tolist()
        return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def _rows(lines: Iterator[bytes], count: int, first: int, columns: int,
          box: _Box) -> None:
    """Read `count` coordinate rows (columns first..first+columns of each)."""
    while count > 0:
        batch = min(count, _ROWS)
        read = list(itertools.islice(lines, batch))
        if len(read) < batch:  # the file ends inside the section
            raise ValueError(f"the file ends inside a node section ({count - len(read)} "
                             "rows missing)")
        text = b"".join(read)
        rows = np.loadtxt(io.BytesIO(text), usecols=range(first, first + columns), ndmin=2)
        if columns == 2:  # a 2D Fluent mesh: z = 0
            rows = np.column_stack([rows, np.zeros(len(rows))])
        box.add(rows)
        count -= batch


# --- Gmsh -----------------------------------------------------------------------------------

def _read_gmsh(path: Path, handle: BinaryIO, size: int) -> MeshFileInfo:
    lines = iter(handle)
    next(lines)  # $MeshFormat
    header = next(lines, b"").decode("latin-1").split()
    end = next(lines, b"")
    if len(header) >= 3 and header[1] != "0" and end.strip() != b"$EndMeshFormat":
        end = next(lines, b"")  # binary: the endianness check, the integer 1
    if len(header) < 3 or end.strip() != b"$EndMeshFormat":
        return MeshFileInfo(path, size, MeshFormat.GMSH, issues=(_blocking(
            "MSH_HEADER_INVALID", f"{path.name}: the Gmsh $MeshFormat header is incomplete.",
            "Export the mesh from Gmsh again (File > Export, .msh)."),))
    version, binary = header[0], header[1] != "0"
    issues: list[Issue] = []
    if not (version.startswith("2.") or version.startswith("4.")):
        issues.append(_blocking(
            "MSH_FORMAT_UNSUPPORTED", f"{path.name} is Gmsh MSH {version}.",
            "Save it from Gmsh as MSH 2.2 or 4.1."))
    names: list[tuple[int, str]] = []
    box = _Box()
    note = ""
    nodes = elements = False
    for raw in lines:
        line = raw.strip()
        if nodes:  # ASCII: after the nodes, only that the elements are complete
            if line == b"$EndElements":
                elements = True
                break
            continue
        if line == b"$PhysicalNames":
            count = int(next(lines))
            for _ in range(count):
                match = _GMSH_NAME.match(next(lines).decode("utf-8", "replace"))
                if match:
                    names.append((int(match[1]), match[3]))
        elif line == b"$Nodes":
            nodes = True
            if binary:  # not read further; gmsh reads it when re-saving
                note = "binary MSH: the box comes from the converted mesh"
                elements = True
                break
            if version.startswith("2."):
                _rows(lines, int(next(lines)), 1, 3, box)
            else:
                blocks = int(next(lines).split()[0])
                for _ in range(blocks):
                    _, _, parametric, count = (int(v) for v in next(lines).split()[:4])
                    for _ in range(count):
                        next(lines)  # node tags
                    _rows(lines, count, 0, 3, box)
                    del parametric  # u v w after x y z: not read
    if not nodes or not elements:
        # A file cut short: gmshToFoam aborts on it (exit 134, review of M1b).
        missing = "$Nodes" if not nodes else "the end of $Elements"
        return MeshFileInfo(path, size, MeshFormat.GMSH, version, binary, issues=(_blocking(
            "MSH_FILE_TRUNCATED", f"{path.name} has no {missing}: the file is incomplete.",
            "Export the mesh from Gmsh again, and copy or upload the whole file."),))
    if not names:
        issues.append(_blocking(
            "GMSH_NO_PHYSICAL_GROUPS",
            f"{path.name} has no physical groups ($PhysicalNames).",
            "In Gmsh, add a Physical Surface for every boundary patch (inlet, outlet, "
            "walls, interfaces) and a Physical Volume for every fluid region, then save "
            "again.",
            "Without them gmshToFoam converts silently to one patch 'patch0' (MSH 2.2) or "
            "fails (MSH 4.1); no patch names can be recovered (E0)."))
    elif not any(dim == 3 for dim, _ in names):
        issues.append(_blocking(
            "GMSH_NO_PHYSICAL_VOLUME", f"{path.name} has no Physical Volume.",
            "In Gmsh, add a Physical Volume for every fluid region (one for the rotating "
            "region), then save again.",
            "Gmsh saves only the elements of physical groups, so the file has no cells."))
    if binary and not gmsh_available():
        issues.append(_blocking(
            "GMSH_BINARY_NEEDS_GMSH",
            f"{path.name} is a binary Gmsh file, which gmshToFoam cannot read, and the gmsh "
            "package that re-saves it is not installed.",
            "Save the mesh from Gmsh as MSH 2.2 or 4.1 ASCII (uncheck 'Save as binary'), or "
            "install the gmsh package (requirements.txt).",
            "gmshToFoam: \"Can only read ascii msh files.\" (E0)."))
    elif (not binary and version not in DIRECT_GMSH_VERSIONS and not gmsh_available()
          and not issues):
        issues.append(_blocking(
            "GMSH_VERSION_NEEDS_GMSH",
            f"{path.name} is MSH {version}; only MSH 2.2 and 4.1 were proven with gmshToFoam, "
            "and the gmsh package that re-saves it as 2.2 is not installed.",
            "Save the mesh from Gmsh as MSH 2.2 or 4.1 ASCII, or install the gmsh package."))
    patches = tuple(n for d, n in names if d == 2)
    zones = tuple(n for d, n in names if d == 3)
    return MeshFileInfo(path, size, MeshFormat.GMSH, version, binary, 3, patches, {}, zones,
                        None, box.count or None, box.result(), note, tuple(issues))


# --- Fluent ---------------------------------------------------------------------------------

def fluent_face_zones(headers: bytes) -> dict[int, str]:
    """Face zone id -> boundary type, from face section headers (zone 0, the
    declaration of all faces, left out)."""
    found: dict[int, str] = {}
    for match in _FLUENT_FACES.finditer(headers):
        zone, kind = int(match[1], 16), int(match[2], 16)
        if zone:
            found[zone] = FLUENT_BC.get(kind, f"type {kind}")
    return found


def _read_fluent(path: Path, handle: BinaryIO, size: int) -> MeshFileInfo:
    dimension: int | None = None
    declared: int | None = None
    binary = False
    zones: dict[int, tuple[str, str]] = {}
    faces: dict[int, str] = {}  # face zone id -> boundary type
    unread: list[str] = []  # zone sections that could not be read
    box = _Box()
    lines = iter(handle)
    for line in lines:
        if not line.startswith(b"("):
            continue
        faces.update(fluent_face_zones(line))
        if _FLUENT_BINARY.match(line):
            binary = True
            continue
        if _FLUENT_ZONE_START.match(line):
            match = _FLUENT_ZONE.match(line)
            if match:
                zones[int(match[2])] = (match[3].decode(), match[4].decode())
            else:
                unread.append(line.strip()[:80].decode("latin-1"))
            continue
        match = _FLUENT_DIMENSION.match(line)
        if match and dimension is None:
            dimension = int(match[1])
            continue
        match = _FLUENT_CELLS.match(line)
        if match and declared is None:
            declared = int(match[2], 16) - int(match[1], 16) + 1
            continue
        match = _FLUENT_NODES.match(line)
        if match and int(match[1], 16) != 0:
            first, last = int(match[2], 16), int(match[3], 16)
            columns = int(match[5] or b"3", 16)
            if not line.rstrip().endswith(b"("):
                next(lines)  # the "(" line
            _rows(lines, last - first + 1, 0, columns, box)
    issues: list[Issue] = []
    if dimension is None or declared is None:
        issues.append(_blocking(
            "MSH_HEADER_INVALID", f"{path.name} has no Fluent dimension or cell count "
            "header ((2 3), (12 (0 ...))).", "Export the mesh from Fluent again as a .msh "
            "file (File > Export > Mesh, or write-mesh).",
            "A file cut short or not a Fluent mesh: converting it could crash or give a "
            "broken mesh with exit code 0 (E0).", dimension=dimension, declared_cells=declared))
    elif unread:
        issues.append(_blocking(
            "FLUENT_ZONE_UNREADABLE", f"{path.name} has zone sections this application "
            f"cannot read: {'; '.join(unread[:3])}.",
            "Send the file to the developers, or export it from Fluent again.",
            "Zone names give the patches and cell zones; none is ever dropped silently.",
            sections=unread))
    elif not zones:
        issues.append(_blocking(
            "FLUENT_NO_ZONES", f"{path.name} has no zone names ((39 ...) or (45 ...)).",
            "Export the mesh from Fluent again; the file may be cut short.",
            "Fluent writes the zone names at the end of the file. A file cut short converts "
            "with exit code 0 to a broken mesh (E0: 538 of 2000 cells)."))
    elif dimension != 3:
        issues.append(_blocking(
            "FLUENT_2D", f"{path.name} is a {dimension}D Fluent mesh; only 3D meshes are "
            "imported.", "Export the 3D mesh.",
            "fluent3DMeshToFoam is the only converter used: fluentMeshToFoam drops wall "
            "baffles silently (E0, owner decision 2).", dimension=dimension))
    if binary:
        issues.append(_issue(
            IssueSeverity.WARNING, "FLUENT_BINARY_UNTESTED",
            f"{path.name} is a binary Fluent mesh. Binary Fluent files have not been tested on "
            "real files.", "Check the conversion log and the patch list; export as ASCII if "
            "the conversion fails."))
    unnamed = {f"{UNNAMED_PREFIX}{z}": k for z, k in sorted(faces.items())
               if z not in zones and k not in FLUENT_INTERIOR_TYPES}
    if unnamed and not any(i.is_stopping for i in issues):
        issues.append(_issue(
            IssueSeverity.INFO, "MSH_UNNAMED_ZONES",
            f"{len(unnamed)} boundary zone(s) of {path.name} have no name; they will be "
            f"called {', '.join(list(unnamed)[:6])}{' ...' if len(unnamed) > 6 else ''}.",
            "Give each a name and a type in the patch table; the types shown are the "
            "file's, which may not be final.",
            "fluent3DMeshToFoam names a face zone without a name FaceGroup<zone id> "
            "(E0: all 25 boundary zones of the guide-vane passage).",
            zones=list(unnamed)))
    patches = tuple(n for _, (k, n) in sorted(zones.items())
                    if k not in FLUENT_CELL_TYPES | FLUENT_INTERIOR_TYPES) + tuple(unnamed)
    kinds = {**{n: k for k, n in zones.values() if n in patches}, **unnamed}
    cells = tuple(n for _, (k, n) in sorted(zones.items()) if k in FLUENT_CELL_TYPES)
    note = ("binary node sections: the box comes from the converted mesh" if binary
            and box.result() is None else "" if box.result() else "no node section was read")
    return MeshFileInfo(path, size, MeshFormat.FLUENT, "", binary, dimension, patches, kinds,
                        cells, declared, box.count or None, box.result(), note,
                        tuple(issues))


# --- entry point ----------------------------------------------------------------------------

def inspect_mesh_file(path: Path) -> MeshFileInfo:
    """Detect the format from the content and read what the import needs."""
    if not path.is_file():
        return MeshFileInfo(path, 0, None, issues=(_blocking(
            "MSH_FILE_MISSING", f"The mesh file {path} does not exist.",
            "Choose the file again (upload, or a path this machine can read)."),))
    size = path.stat().st_size
    if size == 0:
        return MeshFileInfo(path, 0, None, issues=(_blocking(
            "MSH_FILE_EMPTY", f"{path.name} is empty.", "Export the mesh again.",
            "An empty file crashes gmshToFoam (exit 134, E0)."),))
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
            handle.seek(0)
            if head.startswith(_HDF5):
                return MeshFileInfo(path, size, None, issues=(_blocking(
                    "MSH_FORMAT_UNKNOWN", f"{path.name} is an HDF5 file (Fluent .cas.h5 or "
                    ".msh.h5), which OpenFOAM does not convert.",
                    "Export the mesh from Fluent as a .msh file (not HDF5)."),))
            start = head.lstrip()
            if start.startswith(b"$MeshFormat"):
                return _read_gmsh(path, handle, size)
            if start.startswith(b"$NOD"):
                return MeshFileInfo(path, size, MeshFormat.GMSH, "1", issues=(_blocking(
                    "MSH_FORMAT_UNSUPPORTED", f"{path.name} is Gmsh MSH 1, which keeps no "
                    "patch names.", "Save it from Gmsh as MSH 2.2 or 4.1 with physical groups.",
                    "gmshToFoam names its patches patch0, patch1, ... (E0)."),))
            if start.startswith(b"("):
                return _read_fluent(path, handle, size)
    except (OSError, ValueError, StopIteration, UnicodeDecodeError) as error:
        reason = str(error) or ("the file ends early" if isinstance(error, StopIteration)
                                else type(error).__name__)
        return MeshFileInfo(path, size, None, issues=(_blocking(
            "MSH_FILE_UNREADABLE", f"{path.name} could not be read: {reason}.",
            "Check that the file is a complete Fluent or Gmsh .msh export."),))
    return MeshFileInfo(path, size, None, issues=(_blocking(
        "MSH_FORMAT_UNKNOWN", f"{path.name} is neither a Gmsh mesh ($MeshFormat) nor a "
        "Fluent mesh (bracket sections).", "Export the mesh as a Fluent or Gmsh .msh file.",
        "The format is read from the content, never from the extension."),))
