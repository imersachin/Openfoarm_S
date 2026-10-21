"""A quick summary of a .msh file for a folder listing (M2a): no node is read.

Fluent: only the lines starting with "(" (section headers, zone names), found
by grep: 0.19 s for the 637 MB casing in WSL, 1.5 s on a Windows drive (E0;
the full inspection reads every node: 2.2 s and 14.4 s). Gmsh: the header
and $PhysicalNames at the start of the file. What it gives: the format, ASCII
or binary, the declared cells, the cell zones, the boundary zones and how
many of those have no name (fluent3DMeshToFoam calls them FaceGroup<id>; E0:
all 25 of the guide-vane passage's). The import itself still reads the file
fully (detect.py).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from machines.msh_import.detect import fluent_face_zones

_HEAD = 1 << 16  # Gmsh: the bytes read for the header and the names
_CELLS = re.compile(rb"^\(12 \(0 ([0-9a-fA-F]+) ([0-9a-fA-F]+) 0", re.MULTILINE)
_ZONE = re.compile(rb"^\((?:39|45)\s*\(\s*([0-9]+)\s+([A-Za-z0-9_.:+-]+)\s+([^\s()]+)",
                   re.MULTILINE)
_BINARY = re.compile(rb"^\((?:20|30)(?:10|12|13) ", re.MULTILINE)
_GMSH_FORMAT = re.compile(rb"\$MeshFormat\s+(\S+)\s+(\d)")
_GMSH_NAMES = re.compile(rb"\$PhysicalNames\s+\d+\s+(.*?)\$EndPhysicalNames", re.DOTALL)


@dataclass(frozen=True)
class Summary:
    path: Path
    size: int
    format: str = ""  # "Fluent", "Gmsh", or "" (not a mesh this app reads)
    binary: bool = False
    declared_cells: int | None = None
    cell_zones: int = 0
    boundary_zones: int = 0
    unnamed: int = 0
    note: str = ""


def _headers(path: Path) -> bytes:
    """The lines of the file starting with "(" (grep; Python if grep fails)."""
    try:
        found = subprocess.run(["grep", "-a", "^(", str(path)], capture_output=True,
                               check=False, timeout=600)
        if found.returncode in (0, 1):
            return found.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    with path.open("rb") as handle:
        return b"".join(line for line in handle if line.startswith(b"("))


def summarize(path: Path) -> Summary:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(_HEAD)
    except OSError as error:
        return Summary(path, 0, note=f"unreadable: {error.strerror or error}")
    start = head.lstrip()
    if start.startswith(b"$MeshFormat"):
        version = _GMSH_FORMAT.search(head)
        names = _GMSH_NAMES.search(head)
        dims = [int(m[1]) for m in re.finditer(rb"^\s*(\d)\s+\d+\s+\"", names[1], re.MULTILINE)
                ] if names else []
        return Summary(path, size, "Gmsh", bool(version and version[2] == b"1"), None,
                       dims.count(3), dims.count(2), 0,
                       f"MSH {version[1].decode()}" if version else "no $MeshFormat")
    if not start.startswith(b"("):
        return Summary(path, size, note="neither a Fluent nor a Gmsh mesh")
    headers = _headers(path)
    cells = _CELLS.search(headers)
    zones = {int(m[1]): (m[2].decode(), m[3].decode()) for m in _ZONE.finditer(headers)}
    faces = fluent_face_zones(headers)
    boundary = {z for z, kind in faces.items() if kind != "interior"}
    cell_zones = [z for z, (kind, _) in zones.items() if kind in ("fluid", "solid")]
    return Summary(path, size, "Fluent", _BINARY.search(headers) is not None,
                   int(cells[2], 16) - int(cells[1], 16) + 1 if cells else None,
                   len(cell_zones), len(boundary), len(boundary - set(zones)))
