"""Domains: imported surfaces, the generated cylinder, background grids
(docs/rotating_machinery.md section 5).

Imported surfaces are read in both formats (section 5.3): one ASCII STL with
`solid <name>` regions, or one STL per region named by its file. Every region
is transformed to metres (core GeometryConfig order) and kept as its own
surface, so the case generator can write each part as one ASCII STL with one
region per patch.

A cylinder domain or an imported part is meshed as a background box cut by
snappyHexMesh (section 18, decision 1). Its background box is the surface's
bounding box plus BACKGROUND_MARGIN_CELLS cells on every side (decision E1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from geometry.transformer import GeometryTransform
from machines.config import (
    BoxDomain,
    CylinderDomain,
    ImportedDomain,
    ImportedSurface,
    ImportedZone,
    MachineProjectConfig,
    StlFormat,
    StlSource,
)
from vawt.case_generator import Grid, cells_along
from vawt.config import plane_axes

_BINARY_HEADER = 80
_BINARY_TRIANGLE = 50  # normal, three vertices (12 float32) and a uint16

BACKGROUND_PATCH = "background"  # the background block's patch; empty when meshed
# Two cells, plus half a cell so the surface never lies on a background-cell
# plane (G0 R1 and R2 used the half-cell offset).
BACKGROUND_MARGIN_CELLS = 2.5


# --- STL files ------------------------------------------------------------------------

def is_binary_stl(path: Path) -> bool:
    """Binary STL by size, not by a leading "solid" (spec section 10): a binary
    file is exactly 84 + 50 * n bytes, where n is stored after the header."""
    size = path.stat().st_size
    if size < _BINARY_HEADER + 4:
        return False
    with path.open("rb") as stream:
        stream.seek(_BINARY_HEADER)
        count = int.from_bytes(stream.read(4), "little")
    return size == _BINARY_HEADER + 4 + _BINARY_TRIANGLE * count


def stl_region_names(path: Path) -> tuple[str, ...] | None:
    """The `solid <name>` names of an ASCII STL, in file order (repeats once).

    None when the file cannot be read or is binary (binary STL stores no
    region names).
    """
    try:
        if is_binary_stl(path):
            return None
        names: list[str] = []
        with path.open("rb") as stream:
            for line in stream:
                words = line.split()
                if len(words) >= 2 and words[0] == b"solid":
                    name = words[1].decode("ascii", errors="replace")
                    if name not in names:
                        names.append(name)
        return tuple(names)
    except OSError:
        return None


@dataclass(frozen=True)
class Region:
    """One region of an imported surface, in metres."""

    name: str
    mesh: trimesh.Trimesh

    @property
    def faces(self) -> int:
        return len(self.mesh.faces)

    @property
    def area(self) -> float:
        return float(self.mesh.area)


@dataclass(frozen=True)
class SurfaceFile:
    path: Path
    binary: bool
    regions: tuple[Region, ...]
    error: str | None = None  # the file could not be read


@dataclass(frozen=True)
class SurfaceInfo:
    """An imported domain part or zone surface, as read."""

    owner: str  # configuration path: "domain.parts.0" or "rotating_zones.1"
    name: str
    format: StlFormat
    files: tuple[SurfaceFile, ...]
    open_edges: int  # edges used by one face only, over the union of all files

    @property
    def readable(self) -> bool:
        return all(f.error is None for f in self.files)

    @property
    def closed(self) -> bool:
        return self.readable and self.open_edges == 0

    @property
    def regions(self) -> tuple[Region, ...]:
        return tuple(r for f in self.files for r in f.regions)

    def union(self) -> trimesh.Trimesh:
        mesh = trimesh.util.concatenate([r.mesh for r in self.regions])
        mesh.merge_vertices()
        return mesh


def _transform(source: StlSource) -> GeometryTransform:
    r, t = source.rotation_deg, source.translation
    return GeometryTransform(
        source_units=source.source_units.value, unit_factor=source.source_units.to_metres,
        scale=source.scale, rotation_deg=(r.x, r.y, r.z), translation_m=(t.x, t.y, t.z))


def _mesh(triangles: np.ndarray, transform: GeometryTransform) -> trimesh.Trimesh:
    vertices = transform.apply(triangles.reshape(-1, 3))
    faces = np.arange(len(vertices)).reshape(-1, 3)
    mesh = trimesh.Trimesh(vertices, faces, process=False)
    mesh.merge_vertices()
    return mesh


def _ascii_regions(path: Path) -> list[tuple[str, np.ndarray]]:
    """(solid name, triangles (n, 3, 3)) per `solid` block, in file order."""
    blocks: list[tuple[str, list[list[float]]]] = []
    with path.open("rb") as stream:
        for line in stream:
            words = line.split()
            if not words:
                continue
            if words[0] == b"solid":
                name = words[1].decode("ascii", errors="replace") if len(words) > 1 else ""
                blocks.append((name, []))
            elif words[0] == b"vertex" and blocks:
                blocks[-1][1].append([float(w) for w in words[1:4]])
    return [(name, np.asarray(vertices, dtype=float).reshape(-1, 3, 3))
            for name, vertices in blocks]


def read_surface_file(source: StlSource, file_format: StlFormat) -> SurfaceFile:
    """Read one STL. A binary file, and every file of ONE_FILE_PER_PATCH, gives
    one region named after the file; NAMED_REGIONS ASCII gives its solids."""
    path = source.source_path
    transform = _transform(source)
    try:
        binary = is_binary_stl(path)
        if binary:
            loaded = trimesh.load(path, file_type="stl", force="mesh", process=False)
            if not isinstance(loaded, trimesh.Trimesh):
                raise ValueError("not a triangle surface")
            triangles = np.asarray(loaded.vertices)[np.asarray(loaded.faces)]
            return SurfaceFile(path, True, (Region(path.stem, _mesh(triangles, transform)),))
        blocks = _ascii_regions(path)
    except (OSError, ValueError) as exc:
        return SurfaceFile(path, False, (), error=str(exc))
    if not blocks or not any(len(t) for _, t in blocks):
        return SurfaceFile(path, False, (), error="no triangles found")
    if file_format is StlFormat.ONE_FILE_PER_PATCH:
        triangles = np.concatenate([t for _, t in blocks])
        return SurfaceFile(path, False, (Region(path.stem, _mesh(triangles, transform)),))
    return SurfaceFile(path, False, tuple(Region(name, _mesh(t, transform))
                                          for name, t in blocks))


def open_edge_count(meshes: list[trimesh.Trimesh]) -> int:
    """Edges used by exactly one face in the union (0: closed)."""
    if not meshes:
        return 0
    union = trimesh.util.concatenate(meshes)
    union.merge_vertices()
    _, counts = np.unique(union.edges_sorted, axis=0, return_counts=True)
    return int((counts == 1).sum())


def read_surface(owner: str, name: str, surface: ImportedSurface) -> SurfaceInfo:
    files = tuple(read_surface_file(f, surface.format) for f in surface.files)
    meshes = [r.mesh for f in files for r in f.regions]
    return SurfaceInfo(owner, name, surface.format, files, open_edge_count(meshes))


def read_imported(config: MachineProjectConfig) -> list[SurfaceInfo]:
    """Every imported surface: imported rotating zones, then domain parts."""
    surfaces = [read_surface(f"rotating_zones.{i}", zone.name, zone.shape)
                for i, zone in enumerate(config.rotating_zones)
                if isinstance(zone.shape, ImportedZone)]
    if isinstance(config.domain, ImportedDomain):
        surfaces.extend(read_surface(f"domain.parts.{i}", part.name, part)
                        for i, part in enumerate(config.domain.parts))
    return surfaces


# --- the generated cylinder -----------------------------------------------------------------

def cylinder_surface(domain: CylinderDomain) -> list[tuple[str, trimesh.Trimesh]]:
    """The cylinder domain as regions axis_min, axis_max and side, wound with
    normals pointing out of the fluid, with domain.segments facets around."""
    n = domain.segments
    radius = domain.diameter / 2.0
    u, v = plane_axes(domain.axis)
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)

    def ring(along: float) -> np.ndarray:
        points = np.zeros((n, 3))
        points[:, u.position] = domain.centre_u + radius * np.cos(theta)
        points[:, v.position] = domain.centre_v + radius * np.sin(theta)
        points[:, domain.axis.position] = along
        return points

    def centre(along: float) -> np.ndarray:
        point = np.zeros((1, 3))
        point[0, u.position], point[0, v.position] = domain.centre_u, domain.centre_v
        point[0, domain.axis.position] = along
        return point

    k = np.arange(n)
    nxt = (k + 1) % n
    side = trimesh.Trimesh(np.vstack([ring(domain.axis_min), ring(domain.axis_max)]),
                           np.vstack([np.column_stack([k, nxt, n + nxt]),
                                      np.column_stack([k, n + nxt, n + k])]), process=False)
    low = trimesh.Trimesh(np.vstack([centre(domain.axis_min), ring(domain.axis_min)]),
                          np.column_stack([np.zeros(n, int), 1 + nxt, 1 + k]), process=False)
    high = trimesh.Trimesh(np.vstack([centre(domain.axis_max), ring(domain.axis_max)]),
                           np.column_stack([np.zeros(n, int), 1 + k, 1 + nxt]), process=False)
    regions = [("axis_min", low), ("axis_max", high), ("side", side)]
    # The plane axes of y are (x, z): a mirror image of the z frame, so the
    # winding is set from the closed union's volume.
    union = trimesh.util.concatenate([m for _, m in regions])
    union.merge_vertices()
    if union.volume < 0:
        for _, mesh in regions:
            mesh.invert()
    return regions


# --- background grids --------------------------------------------------------------------

def box_grid(domain: BoxDomain) -> Grid:
    lo, hi = domain.bounds.minimum, domain.bounds.maximum
    minimum, maximum = (lo.x, lo.y, lo.z), (hi.x, hi.y, hi.z)
    cells = tuple(cells_along(b - a, domain.cell_size) for a, b in zip(minimum, maximum,
                                                                       strict=True))
    return Grid(minimum, maximum, (cells[0], cells[1], cells[2]))


def background_grid(bounds_min: np.ndarray, bounds_max: np.ndarray, cell_size: float) -> Grid:
    """The background block around a surface's bounds (decision E1)."""
    margin = BACKGROUND_MARGIN_CELLS * cell_size
    lo = [float(a) - margin for a in bounds_min]
    hi = [float(b) + margin for b in bounds_max]
    cells = [cells_along(b - a, cell_size) for a, b in zip(lo, hi, strict=True)]
    return Grid((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]), (cells[0], cells[1], cells[2]))


def cylinder_grid(domain: CylinderDomain) -> Grid:
    union = trimesh.util.concatenate([m for _, m in cylinder_surface(domain)])
    return background_grid(union.bounds[0], union.bounds[1], domain.cell_size)


def surface_grid(surface: SurfaceInfo, cell_size: float) -> Grid | None:
    if not surface.regions:
        return None
    union = surface.union()
    return background_grid(union.bounds[0], union.bounds[1], cell_size)
