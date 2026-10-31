"""The boundary surface of an imported mesh, made by OpenFOAM tools (M2a).

A large mesh is never read whole in Python (owner, M2a): an imported mesh is
written binary, and what the app needs of its geometry comes from one surface
per case, extracted by OpenFOAM:

    cases/<case>_surface/      an OpenFOAM case whose constant/polyMesh files
                               are links to the case's mesh files (nothing is
                               written through them; v2512), with its own
                               faceZones, made by topoSet:
        zoneSurface_<zone>     each cell zone's outer surface: the faces with
                               exactly one side in the zone (cellToFace
                               outside), its boundary faces included
        boundary.vtp           surfaceMeshExtract: every patch and those
                               faceZones, one region each, the points merged
        regions.json           region index -> name (from the extraction log)
                               and the stat of the mesh files it was made from

From it: patch areas (as OpenFOAM's magSf), boxes and centroids, a zone's
radius about its axis (the largest distance of its outer surface's points; the
largest of a solid's points lies on its boundary) and the rotor walls of a zone
(a patch's faces that are also faces of the zone's surface, matched by their
point labels). Evidence: docs/msh_import_notes.md section 13.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from machines.assembly import Step
from machines.config import MachineProjectConfig, MeshZone
from openfoam.dictionary import OpenFOAMFileWriter, foam_header
from vawt.config import Axis

SURFACE_SUFFIX = "_surface"
ZONE_PREFIX = "zoneSurface_"
SURFACE_FILE = "boundary.vtp"
REGIONS_FILE = "regions.json"
TOPO_SET_DICT = "system/topoSetDict"
CUTS_DICT = "system/cutsDict"
CUTS_RECORD = "cuts.json"
CUTS_DIR = "postProcessing/cuts/constant"  # where the surfaces function object writes
LINKED = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
STAMPED = ("points", "faces", "owner", "boundary")  # regions.json: the mesh it is of
_POLY = Path("constant") / "polyMesh"
_REGION = re.compile(r"^\s*surfZone\s+(\d+)\s*:\s*(\S+)\s*$", re.MULTILINE)
_ARRAY = re.compile(r"<DataArray\s+([^>]*)>\s*([^<]*?)\s*</DataArray>", re.DOTALL)
_ATTRIBUTE = re.compile(r"(\w+)\s*=\s*['\"]([^'\"]*)['\"]")
_TYPES = {"Float32": np.float32, "Float64": np.float64, "Int32": np.int32, "Int64": np.int64,
          "UInt8": np.uint8, "UInt32": np.uint32, "UInt64": np.uint64}
Point = tuple[float, float, float]
Box = tuple[Point, Point]


class SurfaceError(ValueError):
    """A .vtp file this reader cannot read."""


def surface_case(case: str) -> str:
    return case + SURFACE_SUFFIX


def surface_dir(folder: Path) -> Path:
    """cases/<case>_surface/ of the mesh case `folder` (cases/<case>/)."""
    return folder.with_name(folder.name + SURFACE_SUFFIX)


# --- making it --------------------------------------------------------------------------------

def _stamp(folder: Path) -> list[list[Any]]:
    poly = folder / _POLY
    found = []
    for name in STAMPED:
        try:
            stat = (poly / name).stat()
        except OSError:
            continue
        found.append([name, stat.st_size, stat.st_mtime_ns])
    return found


def _topo_set_dict(zones: Sequence[str]) -> str:
    actions = "\n".join(
        f"    {{ name {ZONE_PREFIX}{z}_cells; type cellSet; action new; source zoneToCell; "
        f"zones ({z}); }}\n"
        f"    {{ name {ZONE_PREFIX}{z}; type faceSet; action new; source cellToFace; "
        f"sets ({ZONE_PREFIX}{z}_cells); option outside; }}\n"
        f"    {{ name {ZONE_PREFIX}{z}; type faceZoneSet; action new; source setToFaceZone; "
        f"faceSet {ZONE_PREFIX}{z}; }}" for z in zones)
    return foam_header("topoSetDict") + f"\nactions\n(\n{actions}\n);\n"


def prepare(folder: Path, zones: Sequence[str], files: Mapping[str, str]) -> None:
    """(Re)make the surface case of the mesh case `folder`: links to its mesh
    files, `files` (controlDict, fvSchemes, fvSolution) and, for the cell
    zones, the topoSet dictionary of their outer surfaces."""
    target = surface_dir(folder)
    shutil.rmtree(target, ignore_errors=True)
    poly = target / _POLY
    poly.mkdir(parents=True)
    source = folder / _POLY
    for name in LINKED:
        if (source / name).is_file():
            (poly / name).symlink_to(os.path.relpath(source / name, poly))
    for relative, body in files.items():
        OpenFOAMFileWriter.write_if_changed(target / relative, body)
    if zones:
        OpenFOAMFileWriter.write_if_changed(target / TOPO_SET_DICT, _topo_set_dict(zones))


def steps(case: str, zones: Sequence[str]) -> tuple[Step, ...]:
    """topoSet (when there are cell zones), then surfaceMeshExtract of every
    patch and the zone surfaces, in cases/<case>_surface/."""
    name = surface_case(case)
    root = f"cases/{name}"
    found = []
    if zones:
        found.append(Step(f"{name}_topoSet", "run", root, ("topoSet", "-case", ".")))
    found.append(Step(f"{name}_extract", "run", root, (
        "surfaceMeshExtract", "-case", ".", "-constant",
        *(("-faceZones", f'("{ZONE_PREFIX}.*")') if zones else ()), SURFACE_FILE)))
    return tuple(found)


def index(folder: Path, extract_log: str) -> bool:
    """Write regions.json from surfaceMeshExtract's log ("surfZone N : name")
    and the stat of the mesh it was made from. False when the log names no
    region or the surface file is missing (then no regions.json is left)."""
    target = surface_dir(folder)
    names = {int(i): n for i, n in _REGION.findall(extract_log)}
    path = target / REGIONS_FILE
    if not names or not (target / SURFACE_FILE).is_file():
        path.unlink(missing_ok=True)
        return False
    path.write_text(json.dumps({"regions": [names.get(i, "") for i in range(max(names) + 1)],
                                "mesh": _stamp(folder)}, indent=1) + "\n", encoding="utf-8")
    return True


def outputs(folder: Path) -> list[Path]:
    """The files a step that made the surface leaves (for its cache record)."""
    target = surface_dir(folder)
    return [target / SURFACE_FILE, target / REGIONS_FILE]


# --- reading it -------------------------------------------------------------------------------

def read_vtp(path: Path) -> dict[str, np.ndarray]:
    """The binary (base64, uncompressed) arrays of a VTK XML PolyData file as
    OpenFOAM writes it, by name. SurfaceError for anything else."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise SurfaceError(f"{path.name}: {error}") from error
    head = re.search(r"<VTKFile\s+([^>]*)>", text)
    if head is None:
        raise SurfaceError(f"{path.name} is not a VTK XML file.")
    attributes = dict(_ATTRIBUTE.findall(head[1]))
    if "compressor" in attributes:
        raise SurfaceError(f"{path.name} is compressed; only uncompressed files are read.")
    if attributes.get("byte_order", "LittleEndian") != "LittleEndian":
        raise SurfaceError(f"{path.name} is big-endian.")
    header = _TYPES[attributes.get("header_type", "UInt32")]
    size = np.dtype(header).itemsize
    arrays: dict[str, np.ndarray] = {}
    for match in _ARRAY.finditer(text):
        found = dict(_ATTRIBUTE.findall(match[1]))
        name, kind = found.get("Name", ""), found.get("type", "")
        if kind not in _TYPES:
            raise SurfaceError(f"{path.name}: array {name} has type {kind}.")
        if found.get("format") == "binary":
            raw = base64.b64decode("".join(match[2].split()))
            count = int(np.frombuffer(raw[:size], dtype=header)[0])
            values = np.frombuffer(raw[size:size + count], dtype=_TYPES[kind])
        elif found.get("format") == "ascii":
            values = np.array(match[2].split(), dtype=_TYPES[kind])
        else:
            raise SurfaceError(f"{path.name}: array {name} is {found.get('format')}.")
        components = int(found.get("NumberOfComponents", "1"))
        arrays[name] = values.reshape(-1, components) if components > 1 else values
    return arrays


@dataclass(frozen=True, eq=False)
class BoundarySurface:
    """Polygons (CSR: offsets, connectivity) with a region per face."""

    points: np.ndarray  # (n, 3) float64
    offsets: np.ndarray  # (faces + 1,) int64
    connectivity: np.ndarray  # int64
    region: np.ndarray  # (faces,) int64
    names: tuple[str, ...]  # region index -> name

    @property
    def patches(self) -> tuple[str, ...]:
        return tuple(n for n in self.names if n and not n.startswith(ZONE_PREFIX))

    @property
    def zones(self) -> tuple[str, ...]:
        return tuple(n.removeprefix(ZONE_PREFIX) for n in self.names
                     if n.startswith(ZONE_PREFIX))

    def faces(self, name: str) -> np.ndarray:
        """The face indices of a region (empty if there is none)."""
        ids = [i for i, n in enumerate(self.names) if n == name]
        return np.flatnonzero(np.isin(self.region, ids))

    def zone_faces(self, cell_zone: str) -> np.ndarray:
        return self.faces(ZONE_PREFIX + cell_zone)

    def labels(self, faces: np.ndarray) -> np.ndarray:
        """The point labels of the faces, each once."""
        if not len(faces):
            return np.zeros(0, dtype=np.int64)
        return np.unique(np.concatenate([self.connectivity[self.offsets[f]:self.offsets[f + 1]]
                                         for f in faces]))

    def _by_size(self, faces: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """(positions in `faces`, (k, size) point labels) per polygon size."""
        sizes = self.offsets[faces + 1] - self.offsets[faces]
        groups = []
        for size in np.unique(sizes):
            where = np.flatnonzero(sizes == size)
            starts = self.offsets[faces[where]]
            groups.append((where, self.connectivity[starts[:, None] + np.arange(size)]))
        return groups

    def area_vectors(self, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(area magnitudes, area-weighted centroids) of the faces, as OpenFOAM's
        magSf: the triangles from each edge to the mean of the face's points."""
        areas = np.zeros(len(faces))
        centroids = np.zeros((len(faces), 3))
        for where, labels in self._by_size(faces):
            corners = self.points[labels]  # (k, size, 3)
            centre = corners.mean(axis=1, keepdims=True)
            following = np.roll(corners, -1, axis=1)
            parts = np.cross(corners - centre, following - centre) / 2  # (k, size, 3)
            vector = parts.sum(axis=1)
            areas[where] = np.linalg.norm(vector, axis=1)
            weights = np.linalg.norm(parts, axis=2)  # each fan triangle's area
            tri = (corners + following + centre) / 3
            total = weights.sum(axis=1)
            centroids[where] = np.where(
                total[:, None] > 0, (tri * weights[..., None]).sum(axis=1)
                / np.where(total > 0, total, 1)[:, None], corners.mean(axis=1))
        return areas, centroids

    def vectors(self, faces: np.ndarray) -> np.ndarray:
        """Each face's area vector (Sf): the sum of its fan triangles' vectors."""
        found = np.zeros((len(faces), 3))
        for where, labels in self._by_size(faces):
            corners = self.points[labels]
            centre = corners.mean(axis=1, keepdims=True)
            following = np.roll(corners, -1, axis=1)
            found[where] = (np.cross(corners - centre, following - centre) / 2).sum(axis=1)
        return found

    def keys(self, faces: np.ndarray) -> np.ndarray:
        """A key per face from its sorted point labels (the same face in two
        regions has the same labels: the extraction merges points)."""
        keys = np.zeros(len(faces), dtype=object)
        for where, labels in self._by_size(faces):
            ordered = np.sort(labels, axis=1)
            keys[where] = [row.tobytes() for row in ordered]
        return keys


def _load(path: Path, regions: Path) -> BoundarySurface:
    arrays = read_vtp(path)
    try:
        names = json.loads(regions.read_text("utf-8"))["regions"]
        points = np.asarray(arrays["Points"], dtype=np.float64).reshape(-1, 3)
        connectivity = np.asarray(arrays["connectivity"], dtype=np.int64)
        ends = np.asarray(arrays["offsets"], dtype=np.int64)
        region = np.asarray(arrays.get("region", np.zeros(len(ends))), dtype=np.int64)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SurfaceError(f"{path.name}: {error}") from error
    offsets = np.concatenate([[0], ends])
    if len(region) != len(ends) or (len(ends) and offsets[-1] != len(connectivity)):
        raise SurfaceError(f"{path.name}: its arrays do not agree.")
    return BoundarySurface(points, offsets, connectivity, region, tuple(str(n) for n in names))


@functools.lru_cache(maxsize=8)
def _cached(path: str, regions: str, stamp: tuple[tuple[int, int], ...]) -> BoundarySurface:
    return _load(Path(path), Path(regions))


def load(folder: Path) -> BoundarySurface | None:
    """The surface of the mesh case `folder`, when it was made from the mesh as
    it is now (the stat recorded in regions.json); else None."""
    target = surface_dir(folder)
    path, regions = target / SURFACE_FILE, target / REGIONS_FILE
    try:
        record = json.loads(regions.read_text("utf-8"))
        stamp = tuple((s.st_size, s.st_mtime_ns) for s in (path.stat(), regions.stat()))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("mesh") != _stamp(folder):
        return None
    try:
        return _cached(str(path), str(regions), stamp)
    except SurfaceError:
        return None


# --- what is read from it ---------------------------------------------------------------------

def patch_areas(folder: Path, names: Sequence[str]) -> dict[str, float]:
    """The area of each named patch, in the mesh's units: the sum of its face
    areas ("measured from the mesh"). A patch the mesh lacks, or one with no
    faces, is left out; {} without a current surface."""
    surface = load(folder)
    if surface is None:
        return {}
    found = {}
    for name in names:
        faces = surface.faces(name)
        if len(faces):
            found[name] = float(surface.area_vectors(faces)[0].sum())
    return found


@dataclass(frozen=True)
class PatchShape:
    faces: int
    area: float
    centroid: Point
    box: Box


def patch_shapes(folder: Path, names: Sequence[str]) -> dict[str, PatchShape]:
    """Face count, area, area-weighted centroid and box of the named patches."""
    surface = load(folder)
    if surface is None:
        return {}
    found = {}
    for name in names:
        faces = surface.faces(name)
        if not len(faces):
            continue
        areas, centroids = surface.area_vectors(faces)
        total = float(areas.sum())
        centroid = ((centroids * areas[:, None]).sum(axis=0) / total if total
                    else centroids.mean(axis=0)).tolist()
        used = surface.points[surface.labels(faces)]
        low, high = used.min(axis=0).tolist(), used.max(axis=0).tolist()
        found[name] = PatchShape(len(faces), total, (centroid[0], centroid[1], centroid[2]),
                                 ((low[0], low[1], low[2]), (high[0], high[1], high[2])))
    return found


def mesh_box(folder: Path) -> Box | None:
    """The box of the mesh: of its boundary points (a mesh's extreme points
    lie on its boundary)."""
    surface = load(folder)
    if surface is None or not len(surface.points):
        return None
    used = surface.points[surface.labels(np.flatnonzero(np.isin(
        surface.region, [i for i, n in enumerate(surface.names) if n in surface.patches])))]
    if not len(used):
        return None
    low, high = used.min(axis=0).tolist(), used.max(axis=0).tolist()
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def radial_max(points: np.ndarray, origin: Point, axis: Point) -> float:
    """The largest distance of the points from the axis through origin."""
    direction = np.asarray(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    relative = np.asarray(points, dtype=float) - np.asarray(origin, dtype=float)
    radial = relative - np.outer(relative @ direction, direction)
    return float(np.linalg.norm(radial, axis=1).max()) if len(radial) else 0.0


def zone_points(folder: Path, cell_zone: str) -> np.ndarray | None:
    """The points of a cell zone's outer surface; None without one."""
    surface = load(folder)
    if surface is None:
        return None
    faces = surface.zone_faces(cell_zone)
    return surface.points[surface.labels(faces)] if len(faces) else None


def zone_radius(folder: Path, cell_zone: str, origin: Point, axis: Point) -> float | None:
    """The largest distance of the zone's points from its axis: an upper bound
    of the rotor tip radius, computed from the zone."""
    points = zone_points(folder, cell_zone)
    return None if points is None else radial_max(points, origin, axis)


def axis_vector(axis: Axis) -> Point:
    return (float(axis is Axis.X), float(axis is Axis.Y), float(axis is Axis.Z))


def config_zone_radius(folder: Path, zone: MeshZone) -> float | None:
    """zone_radius of a configured mesh zone, about its axis through its origin."""
    return zone_radius(folder, zone.cell_zone, (zone.origin.x, zone.origin.y, zone.origin.z),
                       axis_vector(zone.axis))


@dataclass(frozen=True)
class PatchRadius:
    """A patch about an axis: its area-weighted mean radius and how much of it
    faces the axis (the area-weighted |n . r|, 1 for a cylinder about it)."""

    radius: float
    facing: float


def patch_radii(folder: Path, names: Sequence[str], origin: Point,
                axis: Point) -> dict[str, PatchRadius]:
    """PatchRadius of each named patch with faces; {} without a current surface."""
    surface = load(folder)
    if surface is None:
        return {}
    direction = np.asarray(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    found = {}
    for name in names:
        faces = surface.faces(name)
        if not len(faces):
            continue
        areas, centroids = surface.area_vectors(faces)
        total = float(areas.sum())
        if total <= 0:
            continue
        relative = centroids - np.asarray(origin, dtype=float)
        radial = relative - np.outer(relative @ direction, direction)
        distance = np.linalg.norm(radial, axis=1)
        unit = radial / np.where(distance > 0, distance, 1)[:, None]
        facing = np.abs((surface.vectors(faces) * unit).sum(axis=1)).sum() / total
        found[name] = PatchRadius(float((distance * areas).sum() / total), float(facing))
    return found


def config_inlet_radii(folder: Path, config: MachineProjectConfig) -> dict[str, PatchRadius]:
    """patch_radii of each configured patch under its own name, about the first
    rotating zone's axis (C1: a swirl inlet's radius)."""
    mesh = config.imported_mesh
    if mesh is None or not mesh.zones:
        return {}
    zone = mesh.zones[0]
    refs = {p.name: p.source.ref for p in config.patches}
    found = patch_radii(folder, sorted(set(refs.values())),
                        (zone.origin.x, zone.origin.y, zone.origin.z), axis_vector(zone.axis))
    return {name: found[ref] for name, ref in refs.items() if ref in found}


def config_areas(folder: Path, config: MachineProjectConfig) -> dict[str, float]:
    """Each configured patch's area under its own name, measured on the patch of
    the imported mesh it comes from (renaming a patch does not change its faces)."""
    refs = {p.name: p.source.ref for p in config.patches}
    found = patch_areas(folder, sorted(set(refs.values())))
    return {name: found[ref] for name, ref in refs.items() if ref in found}


def zone_patch_points(folder: Path, names: Sequence[str], cell_zone: str,
                      ) -> tuple[tuple[str, ...], np.ndarray] | None:
    """The named patches with faces on the zone's cells (a rotor wall of this
    zone, not of another), and the points of those faces; None if there are none."""
    surface = load(folder)
    if surface is None:
        return None
    zone = surface.zone_faces(cell_zone)
    if not len(zone):
        return None
    inside = set(surface.keys(zone))
    used, faces = [], []
    for name in names:
        own = surface.faces(name)
        if not len(own):
            continue
        keep = own[np.fromiter((k in inside for k in surface.keys(own)), dtype=bool,
                               count=len(own))]
        if len(keep):
            used.append(name)
            faces.append(keep)
    if not used:
        return None
    return tuple(used), surface.points[surface.labels(np.concatenate(faces))]


# --- cuts for the 3D pane ---------------------------------------------------------------------

def cuts_dict(planes: Sequence[tuple[str, Point, Point]]) -> str:
    """The surfaces function object writing each plane's cut through the cells
    (cuttingPlane, not interpolated, not triangulated; no field): the cell
    outlines on the plane. planes: (name, point, normal)."""
    def vector(v: Point) -> str:
        return "(" + " ".join(f"{c:.10g}" for c in v) + ")"
    surfaces = "\n".join(
        f"            {name} {{ type cuttingPlane; point {vector(point)}; "
        f"normal {vector(normal)}; interpolate false; triangulate false; }}"
        for name, point, normal in planes)
    return foam_header("cutsDict") + f"""
functions
{{
    cuts
    {{
        type            surfaces;
        libs            (sampling);
        surfaceFormat   vtk;
        formatOptions   {{ vtk {{ format binary; }} }}
        fields          ();
        interpolationScheme cell;
        surfaces
        {{
{surfaces}
        }}
    }}
}}
"""


def write_cuts(folder: Path, planes: Sequence[tuple[str, str, Point, Point]]) -> None:
    """The cuts dictionary of the surface case of `folder` and cuts.json, the
    record of its planes (file name, label, point, normal) that the 3D pane
    reads: the cuts are labelled with the planes they were made on."""
    target = surface_dir(folder)
    OpenFOAMFileWriter.write_if_changed(target / CUTS_DICT, cuts_dict(
        [(name, point, normal) for name, _, point, normal in planes]))
    (target / CUTS_RECORD).write_text(json.dumps(
        [{"file": name, "label": label, "point": list(point), "normal": list(normal)}
         for name, label, point, normal in planes], indent=1) + "\n", encoding="utf-8")


def read_cuts(folder: Path) -> list[tuple[str, Point, Point]]:
    """(label, point, normal) of each cut made, in order; [] without a record."""
    try:
        found = json.loads((surface_dir(folder) / CUTS_RECORD).read_text("utf-8"))
        return [(str(c["label"]), (float(c["point"][0]), float(c["point"][1]),
                                   float(c["point"][2])),
                 (float(c["normal"][0]), float(c["normal"][1]), float(c["normal"][2])))
                for c in found]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return []


def cut_step(case: str) -> Step:
    name = surface_case(case)
    return Step(f"{name}_cuts", "run", f"cases/{name}",
                ("postProcess", "-case", ".", "-constant", "-dict", CUTS_DICT))


def cut_file(folder: Path, name: str) -> Path:
    return surface_dir(folder) / CUTS_DIR / f"{name}.vtp"


def cut_segments(path: Path) -> np.ndarray | None:
    """The edges of the cut's polygons, (n, 2, 3); None if unreadable."""
    try:
        arrays = read_vtp(path)
        points = np.asarray(arrays["Points"], dtype=np.float64).reshape(-1, 3)
        connectivity = np.asarray(arrays["connectivity"], dtype=np.int64)
        ends = np.asarray(arrays["offsets"], dtype=np.int64)
    except (SurfaceError, KeyError):
        return None
    starts = np.concatenate([[0], ends[:-1]])
    pairs = []
    for size in np.unique(ends - starts):
        if size < 2:
            continue
        labels = connectivity[starts[ends - starts == size][:, None] + np.arange(size)]
        pairs.append(np.stack([labels, np.roll(labels, -1, axis=1)], axis=2).reshape(-1, 2))
    if not pairs:
        return np.zeros((0, 2, 3))
    edges = np.sort(np.concatenate(pairs), axis=1)
    edges = np.unique(edges, axis=0)  # an edge shared by two cells' polygons, once
    return points[edges]
