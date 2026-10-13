"""Display data for the 3D pane (V6; docs/rotating_machinery.md section 26).

Two scenes, each built once per content hash and stored under previews/:

- setup: bodies, domain, rotating zones, joints, mesh seed points and patches
  by type, from the configuration and its STL files;
- mesh: the boundary by patch, two cuts (along the flow through the rotation
  axis, and across the axis) and the checkMesh problem locations, from an
  ASCII polyMesh.

Arrows (rotation axis and direction, flow direction) are computed from the
configuration when a view is drawn; they read no file.

Display only: nothing here feeds meshing, validation or case setup. Surfaces
above a face cap are thinned deterministically and the scene carries a note
saying so. A stored scene is found by a stat signature of its inputs (sizes,
modification times) recorded in previews/<view>.json, so showing it again
reads only the stored file; when the signature changed, the inputs are hashed
and a scene with the same content hash is reused. No Plotly, no Streamlit.
"""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from case_setup.physics import (
    FlowRateInlet,
    RotationDirection,
    RpmSpeed,
    TipSpeedRatio,
    VelocityInlet,
)
from core.artifacts.hashing import sha256_file, sha256_json
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from geometry.importer import import_stl
from machines.config import (
    BoxDomain,
    CylinderDomain,
    CylinderZone,
    ImportedDomain,
    ImportedZone,
    MachineProjectConfig,
    PatchType,
    RotatingZone,
    SourceKind,
    StlSource,
    box_face,
)
from machines.domains import cylinder_surface, read_imported
from machines.validation import load_bodies
from vawt.config import Axis, plane_axes
from visualization.foam_reader import (
    FoamReadError,
    read_boundary,
    read_faces,
    read_labels,
    read_points,
    read_set,
)

PREVIEW_VERSION = 1  # bump when the stored format or its content changes
PREVIEWS_DIR = "previews"
KEEP_PER_VIEW = 3  # stored scenes kept per view (the newest)
MIN_MARKERS_PER_SET = 100  # a thinned checkMesh set keeps at least this many

# checkMesh -allTopology writes one cellSet per disconnected region. Regions are
# expected (one per separately meshed part, joined by AMI); they are not problems.
REGION_SET = re.compile(r"^region\d+$")
MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary")

# Kinds: a patch type, or one of these roles.
INTERFACE = "INTERFACE"  # AMI pairs and joints
UNASSIGNED = "UNASSIGNED"  # a region or patch with no configured type
ROTATING_BODY = "ROTATING_BODY"  # a body with no patch
STATIONARY_BODY = "STATIONARY_BODY"
SOURCE = "SOURCE"  # a body's STL before its transform (units converted)
ZONE = "ZONE"
DOMAIN = "DOMAIN"
WAKE = "WAKE"
SEED = "SEED"
CUT = "CUT"
PROBLEM = "PROBLEM"

_FOAM_KINDS = {"cyclicAMI": INTERFACE, "wall": PatchType.WALL.value}


@dataclass(frozen=True)
class PreviewLimits:
    """Display caps. Configurable; part of every scene's key."""

    max_faces: int = 100_000  # mesh boundary faces; setup surface triangles
    max_markers: int = 5_000  # checkMesh problem locations
    max_cut_segments: int = 100_000  # cell outlines per cut
    min_faces_per_patch: int = 500  # a thinned patch keeps at least this many

    def __post_init__(self) -> None:
        for name in ("max_faces", "max_markers", "max_cut_segments", "min_faces_per_patch"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"PreviewLimits.{name} must be at least 1.")


# --- scene data -----------------------------------------------------------------------

@dataclass(frozen=True)
class Surface:
    name: str
    kind: str
    vertices: np.ndarray  # (n, 3) float32
    triangles: np.ndarray  # (m, 3) int32
    shown: int  # faces shown
    total: int  # faces in the source


@dataclass(frozen=True)
class Lines:
    """Polylines; rows of NaN separate them."""

    name: str
    kind: str
    points: np.ndarray  # (n, 3) float32
    shown: int = 0
    total: int = 0


@dataclass(frozen=True)
class Markers:
    name: str
    kind: str
    points: np.ndarray  # (n, 3) float32
    labels: tuple[str, ...] = ()
    shown: int = 0
    total: int = 0


@dataclass(frozen=True)
class PatchFrame:
    """Where a patch is and which way it faces (area-weighted, out of the fluid)."""

    name: str
    kind: str
    centroid: tuple[float, float, float]
    normal: tuple[float, float, float]  # unit; zero if undefined
    area: float


@dataclass(frozen=True)
class ZoneFrame:
    """Where a rotating zone is, for its arrows and the cuts."""

    name: str
    axis: Axis
    centre: tuple[float, float, float]
    radius: float
    half_length: float


@dataclass(frozen=True)
class Scene:
    view: str  # "setup" or "mesh"
    key: str  # content hash
    surfaces: tuple[Surface, ...] = ()
    lines: tuple[Lines, ...] = ()
    markers: tuple[Markers, ...] = ()
    patches: tuple[PatchFrame, ...] = ()
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    notes: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = field(default_factory=tuple)
    zones: tuple[ZoneFrame, ...] = ()  # setup scene: imported zones need their surface

    def patch(self, name: str) -> PatchFrame | None:
        return next((p for p in self.patches if p.name == name), None)


@dataclass(frozen=True)
class Arrow:
    """A rotation axis, a rotation direction or a flow direction, for display."""

    name: str
    kind: str  # "axis", "rotation" or "flow"
    path: np.ndarray  # (n, 3) polyline
    head: tuple[float, float, float] | None  # arrow tip; None: no head (undefined)
    head_direction: tuple[float, float, float] | None
    label: str
    defined: bool = True


@dataclass(frozen=True)
class Plane:
    name: str
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]  # unit


def _issue(code: str, message: str, action: str, category: IssueCategory,
           severity: IssueSeverity = IssueSeverity.INFO, **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=IssueStage.VISUALIZATION,
                 code=code, message=message, suggested_action=action, details=details)


# --- thinning ---------------------------------------------------------------------------

def sample_indices(count: int, cap: int) -> np.ndarray:
    """Deterministic evenly spaced subset (all indices when count <= cap)."""
    if count <= cap:
        return np.arange(count)
    return np.unique(np.linspace(0, count - 1, cap).astype(np.int64))


def allocate(counts: Sequence[int], cap: int, minimum: int) -> list[int]:
    """How many of each item to show: everything when the total fits, else a
    share proportional to size, never fewer than min(count, minimum), so a
    small patch never disappears. The minimums may take the total past `cap`."""
    total = sum(counts)
    if total <= cap:
        return list(counts)
    floors = [min(c, minimum) for c in counts]
    spare = max(cap - sum(floors), 0)
    rest = [c - f for c, f in zip(counts, floors, strict=True)]
    rest_total = sum(rest)
    return [f + (min(r, math.floor(spare * r / rest_total)) if rest_total else 0)
            for f, r in zip(floors, rest, strict=True)]


def _thin_note(what: str, shown: int, total: int, cap: int) -> str:
    return f"Showing {shown:,} of {total:,} {what} (display limit {cap:,})."


# --- geometry helpers ---------------------------------------------------------------------

def unit(axis: Axis) -> np.ndarray:
    vector = np.zeros(3)
    vector[axis.position] = 1.0
    return vector


def _normalised(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    return vector / length if length > 0 else np.zeros(3)


def _frame(name: str, kind: str, mesh: trimesh.Trimesh) -> PatchFrame:
    areas = np.asarray(mesh.area_faces, dtype=float)
    area = float(areas.sum())
    if area <= 0:
        return PatchFrame(name, kind, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0)
    centroid = (np.asarray(mesh.triangles_center) * areas[:, None]).sum(0) / area
    normal = _normalised((np.asarray(mesh.face_normals) * areas[:, None]).sum(0))
    return PatchFrame(name, kind, _t(centroid), _t(normal), area)


def _t(vector: Iterable[float]) -> tuple[float, float, float]:
    x, y, z = (float(v) + 0.0 for v in vector)  # + 0.0: no negative zero
    return (x, y, z)


def _surface(name: str, kind: str, mesh: trimesh.Trimesh, keep: int) -> Surface:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    chosen = faces[sample_indices(len(faces), keep)]
    used, inverse = np.unique(chosen, return_inverse=True)
    vertices = np.asarray(mesh.vertices, dtype=float)[used]
    return Surface(name, kind, vertices.astype(np.float32),
                   inverse.reshape(-1, 3).astype(np.int32), len(chosen), len(faces))


def _polylines(paths: Iterable[np.ndarray]) -> np.ndarray:
    parts: list[np.ndarray] = []
    for path in paths:
        if parts:
            parts.append(np.full((1, 3), np.nan))
        parts.append(np.asarray(path, dtype=float))
    return np.vstack(parts).astype(np.float32) if parts else np.zeros((0, 3), np.float32)


def box_corners(minimum: Sequence[float], maximum: Sequence[float]) -> np.ndarray:
    lo, hi = np.asarray(minimum, float), np.asarray(maximum, float)
    return np.array([[(hi if (i >> a) & 1 else lo)[a] for a in range(3)] for i in range(8)])


def box_edges(minimum: Sequence[float], maximum: Sequence[float]) -> np.ndarray:
    corners = box_corners(minimum, maximum)
    edges = [(i, i | (1 << a)) for i in range(8) for a in range(3) if not (i >> a) & 1]
    return _polylines(corners[[a, b]] for a, b in edges)


def box_face_mesh(minimum: Sequence[float], maximum: Sequence[float],
                  face: str) -> trimesh.Trimesh:
    """One face of a box as two triangles, normal pointing out of the box."""
    axis = Axis(face[0])
    high = face.endswith("max")
    corners = box_corners(minimum, maximum)
    on = [i for i in range(8) if bool((i >> axis.position) & 1) == high]
    u, v = (a.position for a in plane_axes(axis))
    on.sort(key=lambda i: (i >> u & 1, i >> v & 1))  # (0,0) (0,1) (1,0) (1,1)
    a, b, c, d = on
    triangles = np.array([[a, c, d], [a, d, b]])
    first = np.cross(corners[c] - corners[a], corners[d] - corners[a])
    if first[axis.position] * (1 if high else -1) < 0:
        triangles = triangles[:, ::-1]
    return trimesh.Trimesh(corners, triangles, process=False)


def _ring(centre: np.ndarray, axis: Axis, radius: float, along: float,
          segments: int = 96) -> np.ndarray:
    u, v = plane_axes(axis)
    theta = np.linspace(0.0, 2.0 * np.pi, segments + 1)
    points = np.zeros((segments + 1, 3))
    points[:, u.position] = centre[u.position] + radius * np.cos(theta)
    points[:, v.position] = centre[v.position] + radius * np.sin(theta)
    points[:, axis.position] = along
    return points


def cylinder_wireframe(axis: Axis, centre_u: float, centre_v: float, axis_min: float,
                       axis_max: float, radius: float, hole: float = 0.0,
                       generators: int = 8) -> np.ndarray:
    centre = np.zeros(3)
    u, v = plane_axes(axis)
    centre[u.position], centre[v.position] = centre_u, centre_v
    paths = []
    for r in (radius, hole) if hole > 0 else (radius,):
        paths += [_ring(centre, axis, r, axis_min), _ring(centre, axis, r, axis_max)]
        for theta in np.linspace(0.0, 2.0 * np.pi, generators, endpoint=False):
            point = centre.copy()
            point[u.position] += r * math.cos(theta)
            point[v.position] += r * math.sin(theta)
            low, high = point.copy(), point.copy()
            low[axis.position], high[axis.position] = axis_min, axis_max
            paths.append(np.vstack([low, high]))
    return _polylines(paths)


# --- zones ----------------------------------------------------------------------------------

def zone_frame(zone: RotatingZone, surface_points: np.ndarray | None = None) -> ZoneFrame:
    """A cylinder zone from its shape; an imported zone from its origin and,
    when given, its surface points (radius and length about the axis)."""
    axis = zone.axis
    shape = zone.shape
    if isinstance(shape, CylinderZone):
        centre = np.zeros(3)
        u, v = plane_axes(axis)
        centre[u.position], centre[v.position] = shape.centre_u, shape.centre_v
        centre[axis.position] = (shape.axis_min + shape.axis_max) / 2.0
        return ZoneFrame(zone.name, axis, _t(centre), shape.diameter / 2.0,
                         (shape.axis_max - shape.axis_min) / 2.0)
    assert isinstance(shape, ImportedZone)
    origin = np.array([shape.origin.x, shape.origin.y, shape.origin.z])
    if surface_points is None or not len(surface_points):
        return ZoneFrame(zone.name, axis, _t(origin), 0.0, 0.0)
    e = unit(axis)
    relative = surface_points - origin
    along = relative @ e
    radial = np.linalg.norm(relative - np.outer(along, e), axis=1)
    centre = origin + e * (along.min() + along.max()) / 2.0
    return ZoneFrame(zone.name, axis, _t(centre), float(radial.max()),
                     float(along.max() - along.min()) / 2.0)


# --- the setup scene -------------------------------------------------------------------------

_SETUP_FIELDS = ("machine", "flow_axis", "bodies", "rotating_zones", "domain", "patches",
                 "joints", "wake")


def stl_paths(config: MachineProjectConfig) -> list[Path]:
    paths = [Path(b.source.source_path) for b in config.bodies]
    for zone in config.rotating_zones:
        if isinstance(zone.shape, ImportedZone):
            paths += [Path(f.source_path) for f in zone.shape.files]
    if isinstance(config.domain, ImportedDomain):
        paths += [Path(f.source_path) for part in config.domain.parts for f in part.files]
    return sorted(set(paths))


def _untransformed(source: StlSource) -> trimesh.Trimesh | None:
    """The uploaded STL only converted to metres, when a transform moves it
    (to check the transform by eye); None without a transform or if unreadable."""
    moved = (source.scale != 1.0
             or any(getattr(source.rotation_deg, a) for a in "xyz")
             or any(getattr(source.translation, a) for a in "xyz"))
    if not moved:
        return None
    imported = import_stl(source.source_path)
    if imported.mesh is None:
        return None
    mesh = imported.mesh.copy()
    mesh.vertices = np.asarray(mesh.vertices, dtype=float) * source.source_units.to_metres
    return mesh


def _setup_inputs(config: MachineProjectConfig, limits: PreviewLimits) -> dict[str, Any]:
    dumped = config.model_dump(mode="json")
    return {"version": PREVIEW_VERSION, "view": "setup",
            "config": {k: dumped.get(k) for k in _SETUP_FIELDS},
            "limits": limits.__dict__}


def setup_signature(config: MachineProjectConfig, limits: PreviewLimits) -> str:
    """Cheap identity: the configuration and the STL files' stat (no file read)."""
    return sha256_json({**_setup_inputs(config, limits),
                        "files": [_stat(p) for p in stl_paths(config)]})


def setup_hash(config: MachineProjectConfig, limits: PreviewLimits) -> str:
    return sha256_json({**_setup_inputs(config, limits),
                        "files": [[str(p), sha256_file(p)] for p in stl_paths(config)]})


def _stat(path: Path) -> list[Any]:
    try:
        info = path.stat()
    except OSError:
        return [str(path), None, None]
    return [str(path), info.st_size, info.st_mtime_ns]


def build_setup_scene(config: MachineProjectConfig, limits: PreviewLimits | None = None,
                      key: str = "") -> Scene:
    """Bodies, domain, zones, joints, seed points and patches by type."""
    limits = limits or PreviewLimits()
    issues: list[Issue] = []
    notes: list[str] = []
    meshes: list[tuple[str, str, trimesh.Trimesh]] = []  # (trace name, kind, surface)
    frames: list[PatchFrame] = []
    lines: list[Lines] = []
    by_ref = {(p.source.kind, p.source.ref): p for p in config.patches}

    # Geometry findings belong to the setup sections; here only "not shown".
    bodies, _ = load_bodies(config)
    for body in config.bodies:
        mesh = bodies.get(body.name)
        if mesh is None:
            issues.append(_issue(
                "VIEW_BODY_UNAVAILABLE", f"Body {body.name} could not be shown: its STL "
                "could not be read.", "Check the body's file in the setup sections.",
                IssueCategory.GEOMETRY, IssueSeverity.WARNING, body=body.name))
            continue
        source = _untransformed(body.source)
        if source is not None:
            meshes.append((f"body {body.name} before its transform (only converted "
                           f"{body.source.source_units.value} → m)", SOURCE, source))
        patch = by_ref.get((SourceKind.BODY, body.name))
        motion = body.motion.value.lower()
        if patch is not None:
            meshes.append((f"{patch.name}: body {body.name} ({motion})", patch.type.value, mesh))
            frames.append(_frame(patch.name, patch.type.value, mesh))
        else:
            kind = STATIONARY_BODY if body.motion.value == "STATIONARY" else ROTATING_BODY
            meshes.append((f"body {body.name} ({motion}, no patch)", kind, mesh))

    domain = config.domain
    if isinstance(domain, BoxDomain):
        lo = (domain.bounds.minimum.x, domain.bounds.minimum.y, domain.bounds.minimum.z)
        hi = (domain.bounds.maximum.x, domain.bounds.maximum.y, domain.bounds.maximum.z)
        lines.append(Lines("domain box", DOMAIN, box_edges(lo, hi)))
        for axis in Axis:
            for high in (False, True):
                face = box_face(axis, high)
                patch = by_ref.get((SourceKind.DOMAIN_FACE, face))
                if patch is not None:
                    mesh = box_face_mesh(lo, hi, face)
                    meshes.append((f"{patch.name}: domain face {face}", patch.type.value, mesh))
                    frames.append(_frame(patch.name, patch.type.value, mesh))
    elif isinstance(domain, CylinderDomain):
        lines.append(Lines("domain cylinder", DOMAIN, cylinder_wireframe(
            domain.axis, domain.centre_u, domain.centre_v, domain.axis_min, domain.axis_max,
            domain.diameter / 2.0)))
        for face, mesh in cylinder_surface(domain):
            patch = by_ref.get((SourceKind.DOMAIN_FACE, face))
            if patch is not None:
                meshes.append((f"{patch.name}: domain face {face}", patch.type.value, mesh))
                frames.append(_frame(patch.name, patch.type.value, mesh))

    joint_of = {}
    for joint in config.joints:
        joint_of[joint.first] = f"joint {joint.first} / {joint.second}"
        joint_of[joint.second] = f"joint {joint.first} / {joint.second}"
    zone_points: dict[str, np.ndarray] = {}
    for surface in read_imported(config):
        for f in surface.files:
            if f.error is not None:
                issues.append(_issue(
                    "VIEW_SURFACE_UNREADABLE", f"{surface.name}: {f.path.name} could not be "
                    f"shown: {f.error}", "Check the file in the setup sections.",
                    IssueCategory.GEOMETRY, IssueSeverity.WARNING, file=str(f.path)))
        if surface.owner.startswith("rotating_zones.") and surface.regions:
            zone_points[surface.name] = np.vstack(
                [np.asarray(r.mesh.vertices) for r in surface.regions])
        for region in surface.regions:
            patch = by_ref.get((SourceKind.REGION, region.name))
            if region.name in joint_of:
                meshes.append((f"{joint_of[region.name]}: {region.name} ({surface.name})",
                               INTERFACE, region.mesh))
            elif patch is not None:
                meshes.append((f"{patch.name}: region of {surface.name}", patch.type.value,
                               region.mesh))
                frames.append(_frame(patch.name, patch.type.value, region.mesh))
            else:
                meshes.append((f"{region.name}: region of {surface.name} (no patch)",
                               UNASSIGNED, region.mesh))

    for zone in config.rotating_zones:
        shape = zone.shape
        if isinstance(shape, CylinderZone):
            lines.append(Lines(f"rotating zone {zone.name}", ZONE, cylinder_wireframe(
                zone.axis, shape.centre_u, shape.centre_v, shape.axis_min, shape.axis_max,
                shape.diameter / 2.0, (shape.hole_diameter or 0.0) / 2.0)))
    if config.wake is not None:
        box = config.wake.box
        lines.append(Lines("wake refinement box", WAKE, box_edges(
            (box.minimum.x, box.minimum.y, box.minimum.z),
            (box.maximum.x, box.maximum.y, box.maximum.z))))

    seeds = mesh_points(config)
    markers = [Markers("mesh seed points", SEED,
                       np.array([p for _, p in seeds], dtype=np.float32).reshape(-1, 3),
                       tuple(label for label, _ in seeds), len(seeds), len(seeds))]

    counts = [len(m.faces) for _, _, m in meshes]
    keep = allocate(counts, limits.max_faces, limits.min_faces_per_patch)
    surfaces = tuple(_surface(name, kind, mesh, k)
                     for (name, kind, mesh), k in zip(meshes, keep, strict=True))
    shown, total = sum(s.shown for s in surfaces), sum(counts)
    if shown < total:
        notes.append(_thin_note("surface triangles", shown, total, limits.max_faces))

    zones = tuple(zone_frame(z, zone_points.get(z.name)) for z in config.rotating_zones)
    every = [s.vertices for s in surfaces] + [ln.points for ln in lines] + [markers[0].points]
    return Scene("setup", key, surfaces, tuple(lines), tuple(markers), tuple(frames),
                 _bounds(every), tuple(notes), tuple(issues), zones)


def zone_frames_of(scene: Scene, config: MachineProjectConfig) -> list[ZoneFrame]:
    """The zone frames stored with a setup scene, else computed from the configuration
    (an imported zone then has no radius: its surface was not read)."""
    stored = {f.name: f for f in scene.zones}
    return [stored.get(z.name) or zone_frame(z) for z in config.rotating_zones]


def mesh_points(config: MachineProjectConfig) -> list[tuple[str, tuple[float, float, float]]]:
    """Every location_in_mesh, labelled with what it seeds."""
    points = [(f"zone {z.name}", (z.location_in_mesh.x, z.location_in_mesh.y,
                                  z.location_in_mesh.z)) for z in config.rotating_zones]
    domain = config.domain
    if isinstance(domain, BoxDomain | CylinderDomain):
        p = domain.location_in_mesh
        points.append(("domain", (p.x, p.y, p.z)))
    elif isinstance(domain, ImportedDomain):
        points += [(f"part {part.name}", (part.location_in_mesh.x, part.location_in_mesh.y,
                                          part.location_in_mesh.z)) for part in domain.parts]
    return points


def _bounds(arrays: Iterable[np.ndarray]) -> tuple[tuple[float, float, float],
                                                   tuple[float, float, float]] | None:
    stacked = [a[np.all(np.isfinite(a), axis=1)] for a in arrays if len(a)]
    stacked = [a for a in stacked if len(a)]
    if not stacked:
        return None
    every = np.vstack(stacked)
    return _t(every.min(0)), _t(every.max(0))


# --- arrows ------------------------------------------------------------------------------------

def _scale(scene: Scene) -> float:
    if scene.bounds is None:
        return 1.0
    lo, hi = np.asarray(scene.bounds[0]), np.asarray(scene.bounds[1])
    return float(np.linalg.norm(hi - lo)) or 1.0


def _basis(axis_vector: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(e_u, e_v) with (e_u, e_v, axis) right-handed: angle grows counter-clockwise
    seen from the axis tip, i.e. positive rotation by the right-hand rule."""
    helper = np.eye(3)[int(np.argmin(np.abs(axis_vector)))]
    e_u = _normalised(np.cross(helper, axis_vector))
    return e_u, np.cross(axis_vector, e_u)


def _rotation_text(config: MachineProjectConfig, zone: str) -> tuple[RotationDirection | None,
                                                                      str]:
    physics = config.physics
    rotation = physics.rotation.get(zone) if physics is not None else None
    if rotation is None:
        return None, "rotation not set"
    speed = rotation.speed
    if isinstance(speed, RpmSpeed):
        amount = f"{speed.rpm:g} rpm"
    else:
        assert isinstance(speed, TipSpeedRatio)
        amount = f"tip-speed ratio {speed.tip_speed_ratio:g}"
    if rotation.direction is None:
        return None, f"{amount}, direction not set"
    return rotation.direction, amount


def rotation_arrows(config: MachineProjectConfig, scene: Scene) -> list[Arrow]:
    """For each zone: the axis (an arrow along +axis through the zone) and the
    rotation direction (an arc about the axis, right-hand rule). An unset
    direction is drawn as an arc without a head, labelled so; never assumed."""
    arrows: list[Arrow] = []
    size = _scale(scene)
    for frame in zone_frames_of(scene, config):
        e = unit(frame.axis)
        c = np.asarray(frame.centre)
        radius = frame.radius or 0.05 * size
        half = frame.half_length or 0.05 * size
        reach = half + 0.6 * radius
        tail, tip = c - e * reach, c + e * reach
        arrows.append(Arrow(f"axis of {frame.name}", "axis", np.vstack([tail, tip]), _t(tip),
                            _t(e), f"{frame.name}: rotation axis +{frame.axis.value}"))
        direction, text = _rotation_text(config, frame.name)
        e_u, e_v = _basis(e)
        sign = -1.0 if direction is RotationDirection.NEGATIVE else 1.0
        theta = sign * np.linspace(0.0, 1.5 * np.pi, 64)
        centre = c + e * (half + 0.3 * radius)
        r = 0.7 * radius
        path = centre + r * (np.outer(np.cos(theta), e_u) + np.outer(np.sin(theta), e_v))
        if direction is None:
            arrows.append(Arrow(f"rotation of {frame.name}", "rotation", path, None, None,
                                f"{frame.name}: {text}", defined=False))
            continue
        # The path's derivative at its end, along the direction of travel.
        tangent = _normalised(sign * r * (-np.sin(theta[-1]) * e_u + np.cos(theta[-1]) * e_v))
        seen = "counter-clockwise" if direction is RotationDirection.POSITIVE else "clockwise"
        arrows.append(Arrow(
            f"rotation of {frame.name}", "rotation", path, _t(path[-1]), _t(tangent),
            f"{frame.name}: {direction.value.lower()} about +{frame.axis.value} ({seen} "
            f"seen from +{frame.axis.value}), {text}"))
    return arrows


def flow_arrows(config: MachineProjectConfig, scene: Scene) -> tuple[list[Arrow], list[str]]:
    """The flow direction at each inlet, and where it comes from:
    a velocity inlet's vector; a flow-rate inlet's inward patch normal; with no
    inlet condition, the flow axis (VAWT) or the inward normal of each INLET
    patch. Returns (arrows, notes)."""
    size = _scale(scene)
    length = 0.15 * size
    arrows: list[Arrow] = []
    notes: list[str] = []
    inlets = config.physics.inlets if config.physics is not None else {}

    def at(name: str) -> np.ndarray:
        frame = scene.patch(name)
        if frame is not None and frame.area > 0:
            return np.asarray(frame.centroid)
        if scene.bounds is not None:
            return (np.asarray(scene.bounds[0]) + np.asarray(scene.bounds[1])) / 2.0
        return np.zeros(3)

    def add(name: str, start: np.ndarray, direction: np.ndarray, label: str) -> None:
        tip = start + direction * length
        arrows.append(Arrow(name, "flow", np.vstack([start - direction * length, tip]),
                            _t(tip), _t(direction), label))

    for name, inlet in sorted(inlets.items()):
        if isinstance(inlet, VelocityInlet):
            vector = np.array([inlet.velocity.x, inlet.velocity.y, inlet.velocity.z])
            if not np.linalg.norm(vector):
                notes.append(f"Inlet {name}: zero velocity, no flow direction to show.")
                continue
            add(f"flow at {name}", at(name), _normalised(vector),
                f"flow at {name}: inlet velocity ({vector[0]:g}, {vector[1]:g}, "
                f"{vector[2]:g}) m/s")
        else:
            assert isinstance(inlet, FlowRateInlet)
            frame = scene.patch(name)
            if frame is None or not any(frame.normal):
                notes.append(f"Inlet {name}: its surface is not shown, so its flow-rate "
                             "direction cannot be drawn.")
                continue
            add(f"flow at {name}", at(name), -np.asarray(frame.normal),
                f"flow at {name}: flow-rate inlet, normal into the domain")
    if arrows or inlets:
        return arrows, notes
    if config.flow_axis is not None:
        e = unit(config.flow_axis)
        inlet_frames = [p for p in scene.patches if p.kind == PatchType.INLET.value]
        start = at(inlet_frames[0].name) if inlet_frames else at("")
        add("flow direction", start, e, f"flow along +{config.flow_axis.value} (flow axis)")
        return arrows, notes
    for frame in scene.patches:
        if frame.kind == PatchType.INLET.value and any(frame.normal):
            add(f"flow at {frame.name}", np.asarray(frame.centroid), -np.asarray(frame.normal),
                f"flow at {frame.name}: into the domain through the inlet patch "
                "(no inlet condition set)")
    if not arrows:
        notes.append("Flow direction unknown: no inlet condition, flow axis or inlet patch.")
    return arrows, notes


def flow_direction(config: MachineProjectConfig, scene: Scene | None = None) -> np.ndarray | None:
    """One flow direction for the cut along the flow, or None if unknown."""
    if scene is None:
        scene = Scene("setup", "")
    arrows, _ = flow_arrows(config, scene)
    if arrows and arrows[0].head_direction is not None:
        return np.asarray(arrows[0].head_direction)
    return None


def cut_planes(config: MachineProjectConfig, scene: Scene | None = None) -> tuple[Plane, ...]:
    """Two cuts through the first rotating zone: one containing the rotation axis
    and the flow direction (when they are parallel, or the flow is unknown, the
    plane through the axis normal to the first other axis), one normal to the
    axis at the zone's mid-span."""
    zone = config.rotating_zones[0]
    frame = (zone_frames_of(scene, config)[0] if scene is not None else zone_frame(zone))
    e = unit(zone.axis)
    c = np.asarray(frame.centre)
    flow = flow_direction(config, scene)
    normal = None
    if flow is not None:
        crossed = np.cross(e, flow)
        if np.linalg.norm(crossed) > 1e-6:
            normal = _normalised(crossed)
    if normal is None:
        normal = unit(plane_axes(zone.axis)[1])
    along = Plane(f"cut along the flow through the {zone.name} axis", _t(c), _t(normal))
    across = Plane(f"cut across the {zone.name} axis at {zone.axis.value} = "
                   f"{c[zone.axis.position]:.4g} m", _t(c), _t(e))
    return along, across


# --- the mesh scene ------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Faces:
    """Faces as one flat label array with offsets (CSR)."""

    flat: np.ndarray
    offsets: np.ndarray  # (n + 1,)

    @property
    def sizes(self) -> np.ndarray:
        return np.diff(self.offsets)

    @classmethod
    def of(cls, faces: Sequence[np.ndarray]) -> _Faces:
        sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=len(faces))
        offsets = np.zeros(len(faces) + 1, dtype=np.int64)
        np.cumsum(sizes, out=offsets[1:])
        flat = np.concatenate(faces) if faces else np.zeros(0, dtype=np.int64)
        return cls(flat.astype(np.int64), offsets)


def _triangles(faces: _Faces, chosen: np.ndarray) -> np.ndarray:
    """Fan triangulation of the chosen faces."""
    starts, sizes = faces.offsets[chosen], faces.sizes[chosen]
    parts = []
    for k in range(1, int(sizes.max(initial=2)) - 1):
        has = sizes > k + 1
        s = starts[has]
        parts.append(np.column_stack([faces.flat[s], faces.flat[s + k], faces.flat[s + k + 1]]))
    return np.vstack(parts) if parts else np.zeros((0, 3), dtype=np.int64)


def face_centres(points: np.ndarray, faces: _Faces, chosen: np.ndarray) -> np.ndarray:
    starts, sizes = faces.offsets[chosen], faces.sizes[chosen]
    index = np.repeat(starts, sizes) + (np.arange(int(sizes.sum()))
                                       - np.repeat(np.cumsum(sizes) - sizes, sizes))
    sums = np.add.reduceat(points[faces.flat[index]], np.cumsum(sizes) - sizes, axis=0) \
        if len(chosen) else np.zeros((0, 3))
    return sums / sizes[:, None] if len(chosen) else sums


def cut_segments(points: np.ndarray, faces: _Faces, origin: Sequence[float],
                 normal: Sequence[float]) -> np.ndarray:
    """Where the faces cross a plane: one segment per crossing edge pair, shape
    (k, 2, 3). Together they draw the cell outlines on the plane. A vertex
    exactly on the plane counts as on the positive side, so no division by zero."""
    d = (points - np.asarray(origin, float)) @ np.asarray(normal, float)
    d = np.where(d == 0.0, np.finfo(float).tiny, d)
    side = d[faces.flat] > 0
    face_of = np.repeat(np.arange(len(faces.offsets) - 1), faces.sizes)
    nxt = np.arange(len(faces.flat)) + 1
    nxt[faces.offsets[1:] - 1] = faces.offsets[:-1]  # last vertex -> first
    crossing = side != side[nxt]
    a, b = faces.flat[crossing], faces.flat[nxt[crossing]]
    t = d[a] / (d[a] - d[b])
    hits = points[a] + t[:, None] * (points[b] - points[a])
    owner = face_of[crossing]
    # A closed polygon crosses the plane an even number of times; its crossing
    # edges are already grouped by face and in order around it: pair them.
    pairs = hits.reshape(-1, 2, 3)
    return pairs[owner[0::2] == owner[1::2]]


def _cell_centres(points: np.ndarray, faces: _Faces, owner: np.ndarray,
                  neighbour: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """Mean of the face centres of each wanted cell (display approximation)."""
    n_cells = int(max(owner.max(initial=-1), neighbour.max(initial=-1))) + 1
    wanted = np.zeros(n_cells, dtype=bool)
    valid = cells[(cells >= 0) & (cells < n_cells)]
    wanted[valid] = True
    labels = np.concatenate([owner, neighbour])
    face_index = np.concatenate([np.arange(len(owner)), np.arange(len(neighbour))])
    keep = wanted[labels]
    centres = face_centres(points, faces, face_index[keep])
    sums = np.zeros((n_cells, 3))
    counts = np.zeros(n_cells)
    np.add.at(sums, labels[keep], centres)
    np.add.at(counts, labels[keep], 1)
    chosen = np.unique(valid)
    return sums[chosen] / np.maximum(counts[chosen], 1)[:, None]


def mesh_input_files(poly: Path) -> list[Path]:
    files = [poly / name for name in MESH_FILES]
    sets = poly / "sets"
    if sets.is_dir():
        files += sorted(p for p in sets.iterdir() if p.is_file())
    return files


def _mesh_inputs(poly: Path, kinds: Mapping[str, str], planes: Sequence[Plane],
                 limits: PreviewLimits) -> dict[str, Any]:
    return {"version": PREVIEW_VERSION, "view": "mesh", "kinds": dict(sorted(kinds.items())),
            "planes": [p.__dict__ for p in planes], "limits": limits.__dict__}


def mesh_signature(poly: Path, kinds: Mapping[str, str], planes: Sequence[Plane],
                   limits: PreviewLimits) -> str:
    return sha256_json({**_mesh_inputs(poly, kinds, planes, limits),
                        "files": [_stat(p) for p in mesh_input_files(poly)]})


def mesh_hash(poly: Path, kinds: Mapping[str, str], planes: Sequence[Plane],
              limits: PreviewLimits) -> str:
    return sha256_json({**_mesh_inputs(poly, kinds, planes, limits),
                        "files": [[p.relative_to(poly).as_posix(), sha256_file(p)]
                                  for p in mesh_input_files(poly)]})


def _patch_kind(name: str, foam_type: str, kinds: Mapping[str, str]) -> str:
    if foam_type == "cyclicAMI":
        return INTERFACE
    return kinds.get(name) or _FOAM_KINDS.get(foam_type, UNASSIGNED)


def build_mesh_scene(poly: Path, kinds: Mapping[str, str], planes: Sequence[Plane],
                     limits: PreviewLimits | None = None, key: str = "") -> Scene:
    """Boundary by patch, the cuts, and the checkMesh problem locations.
    kinds: patch name -> kind (the configured patch type); AMI patches are
    INTERFACE and other unknown names follow their OpenFOAM type."""
    limits = limits or PreviewLimits()
    try:
        points = read_points(poly / "points")
        faces = _Faces.of(read_faces(poly / "faces"))
        owner = read_labels(poly / "owner")
        neighbour = read_labels(poly / "neighbour")
        patches = read_boundary(poly / "boundary")
    except FoamReadError as exc:
        return Scene("mesh", key, issues=(_issue(
            "VIEW_MESH_UNAVAILABLE", f"The mesh cannot be displayed: {exc}",
            "Run the mesh first; only uncompressed ASCII meshes can be displayed.",
            IssueCategory.MESHING, path=str(poly)),))

    notes: list[str] = []
    issues: list[Issue] = []
    keep = allocate([p.n_faces for p in patches], limits.max_faces, limits.min_faces_per_patch)
    surfaces: list[Surface] = []
    for patch, count in zip(patches, keep, strict=True):
        if not patch.n_faces:
            continue
        chosen = patch.start_face + sample_indices(patch.n_faces, count)
        triangles = _triangles(faces, chosen)
        used, inverse = np.unique(triangles, return_inverse=True)
        kind = _patch_kind(patch.name, patch.patch_type, kinds)
        surfaces.append(Surface(
            f"{patch.name} ({patch.patch_type})", kind, points[used].astype(np.float32),
            inverse.reshape(-1, 3).astype(np.int32), len(chosen), patch.n_faces))
    shown, total = sum(s.shown for s in surfaces), sum(p.n_faces for p in patches)
    if shown < total:
        notes.append(_thin_note("boundary faces", shown, total, limits.max_faces))

    lines: list[Lines] = []
    for plane in planes:
        segments = cut_segments(points, faces, plane.origin, plane.normal)
        n = len(segments)
        segments = segments[sample_indices(n, limits.max_cut_segments)]
        if n > len(segments):
            notes.append(_thin_note(f"cell edges on the {plane.name}", len(segments), n,
                                    limits.max_cut_segments))
        if not n:
            notes.append(f"The {plane.name} does not cross the mesh.")
            continue
        flat = np.full((len(segments) * 3, 3), np.nan)
        flat[0::3], flat[1::3] = segments[:, 0], segments[:, 1]
        lines.append(Lines(plane.name, CUT, flat[:-1].astype(np.float32), len(segments), n))

    markers = _problem_markers(poly, points, faces, owner, neighbour, limits, notes, issues)
    every = [s.vertices for s in surfaces]
    return Scene("mesh", key, tuple(surfaces), tuple(lines), tuple(markers), (),
                 _bounds(every), tuple(notes), tuple(issues))


def _problem_markers(poly: Path, points: np.ndarray, faces: _Faces, owner: np.ndarray,
                     neighbour: np.ndarray, limits: PreviewLimits, notes: list[str],
                     issues: list[Issue]) -> list[Markers]:
    sets_dir = poly / "sets"
    if not sets_dir.is_dir():
        return []
    found: list[tuple[str, np.ndarray]] = []
    regions = []
    for path in sorted(p for p in sets_dir.iterdir() if p.is_file()):
        if REGION_SET.match(path.name):
            regions.append(path.name)
            continue
        try:
            set_class, labels = read_set(path)
        except FoamReadError as exc:
            issues.append(_issue("VIEW_SET_UNREADABLE", f"checkMesh set {path.name}: {exc}",
                                 "Inspect the set file.", IssueCategory.MESH_QUALITY,
                                 set=path.name))
            continue
        if set_class == "faceSet":
            valid = labels[(labels >= 0) & (labels < len(faces.offsets) - 1)]
            found.append((path.name, face_centres(points, faces, valid)))
        elif set_class == "pointSet":
            found.append((path.name, points[labels[(labels >= 0) & (labels < len(points))]]))
        elif set_class == "cellSet":
            found.append((path.name, _cell_centres(points, faces, owner, neighbour, labels)))
        else:
            issues.append(_issue("VIEW_SET_UNKNOWN", f"checkMesh set {path.name} has class "
                                 f"{set_class}; not shown.", "Inspect the set file.",
                                 IssueCategory.MESH_QUALITY, set=path.name))
    if regions:
        notes.append(f"Region sets ({', '.join(regions)}) list whole mesh regions, not "
                     "problems; they are not shown.")
    keep = allocate([len(c) for _, c in found], limits.max_markers,
                    min(MIN_MARKERS_PER_SET, limits.max_markers))
    markers = []
    for (name, centres), count in zip(found, keep, strict=True):
        if not len(centres):
            continue
        chosen = centres[sample_indices(len(centres), count)]
        markers.append(Markers(f"checkMesh: {name}", PROBLEM, chosen.astype(np.float32),
                               (), len(chosen), len(centres)))
    shown, total = sum(m.shown for m in markers), sum(len(c) for _, c in found)
    if shown < total:
        notes.append(_thin_note("checkMesh problem locations", shown, total,
                                limits.max_markers))
    return markers


# --- storage --------------------------------------------------------------------------------

def _encode(scene: Scene) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    meta: dict[str, Any] = {
        "version": PREVIEW_VERSION, "view": scene.view, "key": scene.key,
        "notes": list(scene.notes), "issues": [i.as_dict() for i in scene.issues],
        "bounds": scene.bounds, "patches": [p.__dict__ for p in scene.patches],
        "surfaces": [], "lines": [], "markers": [],
        "zones": [{**z.__dict__, "axis": z.axis.value} for z in scene.zones]}
    for i, s in enumerate(scene.surfaces):
        arrays[f"s{i}v"], arrays[f"s{i}t"] = s.vertices, s.triangles
        meta["surfaces"].append({"name": s.name, "kind": s.kind, "shown": s.shown,
                                 "total": s.total})
    for i, ln in enumerate(scene.lines):
        arrays[f"l{i}"] = ln.points
        meta["lines"].append({"name": ln.name, "kind": ln.kind, "shown": ln.shown,
                              "total": ln.total})
    for i, m in enumerate(scene.markers):
        arrays[f"m{i}"] = m.points
        meta["markers"].append({"name": m.name, "kind": m.kind, "labels": list(m.labels),
                                "shown": m.shown, "total": m.total})
    arrays["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    return arrays


def _decode(data: Mapping[str, np.ndarray]) -> Scene:
    meta = json.loads(bytes(data["meta"]).decode("utf-8"))
    if meta.get("version") != PREVIEW_VERSION:
        raise ValueError("stored preview has another format version")
    bounds = meta["bounds"]
    return Scene(
        meta["view"], meta["key"],
        tuple(Surface(s["name"], s["kind"], data[f"s{i}v"], data[f"s{i}t"], s["shown"],
                      s["total"]) for i, s in enumerate(meta["surfaces"])),
        tuple(Lines(ln["name"], ln["kind"], data[f"l{i}"], ln["shown"], ln["total"])
              for i, ln in enumerate(meta["lines"])),
        tuple(Markers(m["name"], m["kind"], data[f"m{i}"], tuple(m["labels"]), m["shown"],
                      m["total"]) for i, m in enumerate(meta["markers"])),
        tuple(PatchFrame(p["name"], p["kind"], _t(p["centroid"]), _t(p["normal"]),
                         float(p["area"])) for p in meta["patches"]),
        (_t(bounds[0]), _t(bounds[1])) if bounds else None,
        tuple(meta["notes"]), tuple(Issue.from_dict(i) for i in meta["issues"]),
        tuple(ZoneFrame(z["name"], Axis(z["axis"]), _t(z["centre"]), float(z["radius"]),
                        float(z["half_length"])) for z in meta["zones"]))


@dataclass(frozen=True)
class StoredPreview:
    scene: Scene
    built: bool  # True: built now; False: loaded from previews/
    hashed: bool  # True: the inputs were hashed (their stat signature changed)


class PreviewStore:
    """previews/<view>-<hash>.npz plus previews/<view>.json, the index
    {signature, hash} of the newest. Writes are atomic; a stored file whose
    content does not decode, or carries another key, is rebuilt."""

    def __init__(self, root: Path) -> None:
        self.directory = root / PREVIEWS_DIR

    def _file(self, view: str, key: str) -> Path:
        return self.directory / f"{view}-{key}.npz"

    def _index(self, view: str) -> Path:
        return self.directory / f"{view}.json"

    def _load(self, view: str, key: str) -> Scene | None:
        path = self._file(view, key)
        if not path.is_file():
            return None
        try:
            with np.load(path, allow_pickle=False) as data:
                scene = _decode({k: data[k] for k in data.files})
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return scene if scene.key == key and scene.view == view else None

    def _indexed(self, view: str, signature: str) -> str | None:
        try:
            index = json.loads(self._index(view).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(index, dict) and index.get("signature") == signature:
            key = index.get("hash")
            return key if isinstance(key, str) else None
        return None

    def get(self, view: str, signature: str, hasher: Callable[[], str],
            builder: Callable[[str], Scene]) -> StoredPreview:
        key = self._indexed(view, signature)
        if key is not None:
            scene = self._load(view, key)
            if scene is not None:
                return StoredPreview(scene, built=False, hashed=False)
        key = hasher()
        scene = self._load(view, key)
        built = scene is None
        if scene is None:
            scene = builder(key)
            if scene.issues and not scene.surfaces:
                return StoredPreview(scene, built=True, hashed=True)  # nothing to keep
            self._write(view, key, scene)
        self._write_index(view, signature, key)
        return StoredPreview(scene, built=built, hashed=True)

    def _write(self, view: str, key: str, scene: Scene) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._file(view, key)
        partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with partial.open("wb") as handle:
                # numpy's stub types every keyword as allow_pickle; these are arrays.
                savez: Any = np.savez_compressed
                savez(handle, **_encode(scene))
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        self._prune(view, keep=target)

    def _write_index(self, view: str, signature: str, key: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._index(view)
        partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        partial.write_text(json.dumps({"signature": signature, "hash": key}), encoding="utf-8")
        os.replace(partial, target)

    def _prune(self, view: str, keep: Path) -> None:
        stored = sorted(self.directory.glob(f"{view}-*.npz"),
                        key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in [p for p in stored if p != keep][KEEP_PER_VIEW - 1:]:
            path.unlink(missing_ok=True)


def setup_preview(root: Path, config: MachineProjectConfig,
                  limits: PreviewLimits | None = None) -> StoredPreview:
    limits = limits or PreviewLimits()
    return PreviewStore(root).get(
        "setup", setup_signature(config, limits), lambda: setup_hash(config, limits),
        lambda key: build_setup_scene(config, limits, key))


def mesh_preview(root: Path, poly: Path, kinds: Mapping[str, str], planes: Sequence[Plane],
                 limits: PreviewLimits | None = None) -> StoredPreview:
    limits = limits or PreviewLimits()
    return PreviewStore(root).get(
        "mesh", mesh_signature(poly, kinds, planes, limits),
        lambda: mesh_hash(poly, kinds, planes, limits),
        lambda key: build_mesh_scene(poly, kinds, planes, limits, key))


def patch_kinds(config: MachineProjectConfig) -> dict[str, str]:
    """Patch name -> configured type, for colouring the meshed boundary."""
    return {p.name: p.type.value for p in config.patches}
