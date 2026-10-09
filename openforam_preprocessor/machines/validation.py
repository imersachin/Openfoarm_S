"""Checks before meshing (docs/rotating_machinery.md sections 3.1 and 10).

Pure Python: no OpenFOAM, no files written. Every finding is a structured
issue; BLOCKING and ERROR stop a run. Thresholds are parameters.

- parse_config: model errors as issues, with named codes for choices not made.
- check_config: rules between sections; reads no files.
- check_rotating_walls: rotating-wall regions against the surfaces holding them.
- check_geometry: rules that need the bodies' surfaces (load_bodies).

Imported surfaces (imported domain parts and zones) are read here only for
their region names, to place rotating walls. Their
closedness, region names, the binary-STL check and the containment of bodies
in them come with the imported-domain reader (G2) and zones (G3); a
configuration that uses them gets an INFO issue saying so.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

import numpy as np
import trimesh
from pydantic import ValidationError

from core.config.validation import issues_from_validation_error
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from geometry.metrics import contains_points
from machines.config import (
    BOX_FACES,
    CYLINDER_FACES,
    MACHINE_SCHEMA_VERSION,
    BoxDomain,
    CylinderDomain,
    CylinderZone,
    DomainKind,
    ImportedDomain,
    ImportedSurface,
    ImportedZone,
    MachineProjectConfig,
    MachineType,
    Motion,
    PatchType,
    RotatingZone,
    SourceKind,
    StlSource,
    box_face,
)
from machines.domains import stl_region_names
from machines.presets import DOMAIN_CHOICES
from vawt.case_generator import RESERVED_NAMES
from vawt.config import (
    SCHEMA_VERSION_INVALID,
    SCHEMA_VERSION_NEWER,
    Axis,
    InterfaceType,
    RotorGeometryConfig,
    Vec3,
    plane_axes,
)
from vawt.validation import UNSET_ERROR_TYPES, load_rotor_geometry


@dataclass(frozen=True)
class MachineThresholds:
    """Placeholder defaults; configurable per call."""

    # Clearance between a zone and the domain, or a body and a zone wall, in
    # cells of the zone (or of the domain, for zone-to-domain). VAWT default.
    min_clearance_cells: float = 1.0
    # Body surfaces are tested at points at most this far apart, in the finest
    # cell size of the cylinder zones and the generated domain: thinner
    # intrusions into a zone or through the domain can be missed.
    sample_spacing_cells: float = 0.5
    max_sample_points: int = 2_000_000  # per body; spacing is widened beyond it
    # Points on a zone's end circles used to test the zone against the domain.
    zone_circle_points: int = 720


@dataclass(frozen=True)
class MachineValidationResult:
    config: MachineProjectConfig | None
    issues: tuple[Issue, ...]

    @property
    def can_run(self) -> bool:
        return self.config is not None and not has_stopping_issue(self.issues)


def _issue(severity: IssueSeverity, code: str, message: str, action: str, *,
           category: IssueCategory = IssueCategory.CONFIGURATION,
           stage: IssueStage = IssueStage.CONFIGURATION, explanation: str = "",
           **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=stage, code=code,
                 message=message, explanation=explanation, suggested_action=action,
                 details=details)


def _blocking(code: str, message: str, action: str, **kwargs: Any) -> Issue:
    return _issue(IssueSeverity.BLOCKING, code, message, action, **kwargs)


# --- configuration parsing ------------------------------------------------------------

def parse_config(raw: Any) -> tuple[MachineProjectConfig | None, tuple[Issue, ...]]:
    """Validate raw data; name the choices that are never assumed."""
    try:
        return MachineProjectConfig.model_validate(raw), ()
    except ValidationError as exc:
        return None, tuple(_rename(issue) for issue in issues_from_validation_error(exc))


# (pattern of the field path, code, message, action) for values never assumed.
_NOT_CHOSEN = (
    (r"machine", "MACHINE_NOT_CHOSEN", "The machine type has not been chosen.",
     "Choose VAWT, VAWT_POLE, HAWT, FRANCIS or CUSTOM."),
    (r"bodies\.\d+\.motion", "MOTION_NOT_CHOSEN",
     "It has not been chosen whether a body rotates.",
     "Set the body's motion: STATIONARY, ROTATING or SPLIT."),
    (r"rotating_zones\.\d+\.axis", "ZONE_AXIS_NOT_CHOSEN",
     "A rotating zone's axis has not been chosen.", "Choose the rotation axis."),
    (r"patches\.\d+\.type", "PATCH_TYPE_NOT_CHOSEN", "A patch has no type.",
     "Give every patch one type: INLET, OUTLET, WALL, ROTATING_WALL or SLIP."),
)


def _rename(issue: Issue) -> Issue:
    field = str(issue.details.get("field", ""))
    kind = str(issue.details.get("error_type", ""))
    if kind == SCHEMA_VERSION_INVALID:
        return _blocking(
            "SCHEMA_VERSION_INVALID", issue.message,
            "Set schema_version to a whole number of 1 or more, or remove it to use "
            f"the current version ({MACHINE_SCHEMA_VERSION}).",
            field="schema_version",
        )
    if kind == SCHEMA_VERSION_NEWER:
        return _blocking(
            "SCHEMA_VERSION_NEWER", issue.message,
            "Open this configuration with the newer application version that wrote it.",
            field="schema_version",
        )
    if kind not in UNSET_ERROR_TYPES:
        return issue
    if field.endswith("source_units"):
        return _blocking(
            "UNITS_NOT_CHOSEN", "The units of an STL file have not been chosen.",
            "Select the units the STL was exported in; they are never assumed.",
            category=IssueCategory.UNITS, field=field,
        )
    for pattern, code, message, action in _NOT_CHOSEN:
        if re.fullmatch(pattern, field):
            return _blocking(code, message, action, field=field)
    return issue


# --- shapes ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Cylinder:
    """An axis-aligned solid cylinder, optionally with a coaxial hole."""

    axis: Axis
    centre_u: float
    centre_v: float
    axis_min: float
    axis_max: float
    radius: float
    hole: float = 0.0

    @classmethod
    def of_zone(cls, zone: RotatingZone) -> _Cylinder | None:
        shape = zone.shape
        if not isinstance(shape, CylinderZone):
            return None
        return cls(zone.axis, shape.centre_u, shape.centre_v, shape.axis_min, shape.axis_max,
                   shape.diameter / 2.0, (shape.hole_diameter or 0.0) / 2.0)

    @classmethod
    def of_domain(cls, domain: CylinderDomain) -> _Cylinder:
        return cls(domain.axis, domain.centre_u, domain.centre_v, domain.axis_min,
                   domain.axis_max, domain.diameter / 2.0)

    def signed_distance(self, points: np.ndarray) -> np.ndarray:
        """Distance to the solid: negative inside (depth below its surface)."""
        u, v = plane_axes(self.axis)
        radial = np.hypot(points[:, u.position] - self.centre_u,
                          points[:, v.position] - self.centre_v)
        along = points[:, self.axis.position]
        out_r = radial - self.radius
        if self.hole > 0.0:
            out_r = np.maximum(out_r, self.hole - radial)
        out_a = np.maximum(self.axis_min - along, along - self.axis_max)
        inside = (out_r < 0.0) & (out_a < 0.0)
        outside = np.hypot(np.maximum(out_r, 0.0), np.maximum(out_a, 0.0))
        return np.where(inside, np.maximum(out_r, out_a), outside)

    def end_circles(self, count: int) -> np.ndarray:
        """Points on both end circles: a solid cylinder's extreme points."""
        u, v = plane_axes(self.axis)
        theta = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
        points = np.zeros((2 * count, 3))
        for k, along in enumerate((self.axis_min, self.axis_max)):
            rows = slice(k * count, (k + 1) * count)
            points[rows, u.position] = self.centre_u + self.radius * np.cos(theta)
            points[rows, v.position] = self.centre_v + self.radius * np.sin(theta)
            points[rows, self.axis.position] = along
        return points

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        u, v = plane_axes(self.axis)
        lo, hi = np.zeros(3), np.zeros(3)
        lo[u.position], hi[u.position] = self.centre_u - self.radius, self.centre_u + self.radius
        lo[v.position], hi[v.position] = self.centre_v - self.radius, self.centre_v + self.radius
        lo[self.axis.position], hi[self.axis.position] = self.axis_min, self.axis_max
        return lo, hi


def _point(vector: Vec3) -> np.ndarray:
    return np.array([[vector.x, vector.y, vector.z]], dtype=float)


def _box_signed_distance(domain: BoxDomain, points: np.ndarray) -> np.ndarray:
    lo = _point(domain.bounds.minimum)[0]
    hi = _point(domain.bounds.maximum)[0]
    out = np.maximum(lo - points, points - hi)  # per axis, negative inside
    inside = np.all(out < 0.0, axis=1)
    return np.where(inside, out.max(axis=1), np.linalg.norm(np.maximum(out, 0.0), axis=1))


def _domain_signed_distance(config: MachineProjectConfig,
                            points: np.ndarray) -> np.ndarray | None:
    """Signed distance to a generated domain; None without one."""
    domain = config.domain
    if isinstance(domain, BoxDomain):
        return _box_signed_distance(domain, points)
    if isinstance(domain, CylinderDomain):
        return _Cylinder.of_domain(domain).signed_distance(points)
    return None


def _zones(config: MachineProjectConfig) -> list[tuple[int, RotatingZone, _Cylinder]]:
    return [(i, zone, cylinder) for i, zone in enumerate(config.rotating_zones)
            if (cylinder := _Cylinder.of_zone(zone)) is not None]


def _imported_surfaces(config: MachineProjectConfig) -> list[ImportedSurface]:
    surfaces: list[ImportedSurface] = [z.shape for z in config.rotating_zones
                                       if isinstance(z.shape, ImportedZone)]
    if isinstance(config.domain, ImportedDomain):
        surfaces.extend(config.domain.parts)
    return surfaces


def _known_regions(config: MachineProjectConfig) -> set[str] | None:
    """Every imported region name, when all are known without reading files."""
    regions: set[str] = set()
    for surface in _imported_surfaces(config):
        names = surface.file_regions()
        if names is None:
            return None
        regions.update(names)
    return regions


# --- check_config -----------------------------------------------------------------------

def check_config(config: MachineProjectConfig,
                 thresholds: MachineThresholds | None = None) -> tuple[Issue, ...]:
    """Rules between sections (section 10), without reading any STL file."""
    limits = thresholds or MachineThresholds()
    return (*_name_issues(config), *_machine_issues(config), *_patch_issues(config),
            *_joint_issues(config), *_zone_issues(config, limits), *_imported_notice(config))


_T = TypeVar("_T", str, tuple[SourceKind, str])


def _duplicates(items: Iterable[_T]) -> list[_T]:
    return sorted(item for item, count in Counter(items).items() if count > 1)


def _name_issues(config: MachineProjectConfig) -> list[Issue]:
    issues = []
    sections = {"bodies": [b.name for b in config.bodies],
                "rotating_zones": [z.name for z in config.rotating_zones]}
    if isinstance(config.domain, ImportedDomain):
        sections["domain.parts"] = [p.name for p in config.domain.parts]
    for section, names in sections.items():
        for name in _duplicates(names):
            issues.append(_blocking(
                "DUPLICATE_NAME", f"'{name}' is used twice in {section}.",
                "Give each one its own name.", section=section, name=name))
    for name in _duplicates(p.name for p in config.patches):
        issues.append(_blocking(
            "PATCH_NAME_DUPLICATE", f"Two patches are named '{name}'.",
            "Rename one of them; a patch name is used once.", name=name))
    for i, patch in enumerate(config.patches):
        if patch.name in RESERVED_NAMES:
            issues.append(_blocking(
                "RESERVED_PATCH_NAME",
                f"'{patch.name}' (patches.{i}.name) is a name the generated mesh uses itself.",
                f"Choose another name; reserved: {', '.join(sorted(RESERVED_NAMES))}.",
                field=f"patches.{i}.name", name=patch.name))
    zones = {z.name for z in config.rotating_zones}
    for i, body in enumerate(config.bodies):
        if body.zone is not None and body.zone not in zones:
            issues.append(_blocking(
                "UNKNOWN_ZONE", f"Body '{body.name}' names rotating zone '{body.zone}', "
                "which does not exist.", "Name one of the configured rotating zones.",
                field=f"bodies.{i}.zone", body=body.name))
    return issues


def _vawt_only(field: str) -> Issue:
    return _blocking(
        "VAWT_ONLY_SETTING", f"{field} is available for the VAWT machine only.",
        f"Change {field}, or set the machine to VAWT.", field=field,
        explanation="These settings are carried for VAWT projects meshed by the VAWT "
        "workflow; the general workflow does not build them.")


def _machine_issues(config: MachineProjectConfig) -> list[Issue]:
    issues = []
    machine = config.machine
    if machine is not MachineType.VAWT:
        if config.domain is None:
            issues.append(_vawt_only("A missing domain (rotating zones only)"))
        issues.extend(_vawt_only(f"rotating_zones.{i}.interface CELL_ZONE")
                      for i, z in enumerate(config.rotating_zones)
                      if z.interface is InterfaceType.CELL_ZONE)
        if config.wake is not None:
            issues.append(_vawt_only("wake"))
        if config.export.fluent_msh:
            issues.append(_vawt_only("export.fluent_msh"))
    if config.domain is not None:
        choice = DOMAIN_CHOICES[machine]
        if DomainKind(config.domain.kind) not in choice.allowed:
            issues.append(_blocking(
                "DOMAIN_KIND_NOT_ALLOWED",
                f"A {config.domain.kind} domain is not available for {machine.value}.",
                "Use one of: " + ", ".join(sorted(choice.allowed)) + ".",
                field="domain.kind"))
    if machine in (MachineType.VAWT, MachineType.VAWT_POLE):
        if config.flow_axis is None:
            issues.append(_blocking(
                "FLOW_AXIS_NOT_CHOSEN", "The flow axis has not been chosen.",
                "Choose the axis the flow runs along (inlet at its minimum).",
                field="flow_axis"))
        else:
            issues.extend(_blocking(
                "FLOW_ALONG_ROTOR_AXIS",
                f"The flow axis is the axis of rotating zone '{z.name}'; a vertical-axis "
                "turbine has flow across its axis.",
                "Choose a flow axis normal to the rotor axis.", field="flow_axis")
                for z in config.rotating_zones if z.axis is config.flow_axis)
    return issues


def _patch_issues(config: MachineProjectConfig) -> list[Issue]:
    issues = []
    domain = config.domain
    faces: tuple[str, ...] = ()
    if isinstance(domain, BoxDomain):
        faces = BOX_FACES
    elif isinstance(domain, CylinderDomain):
        faces = CYLINDER_FACES
    regions = _known_regions(config)
    has_imported = bool(_imported_surfaces(config))
    bodies = {b.name: b for b in config.bodies}

    for i, patch in enumerate(config.patches):
        kind, ref = patch.source.kind, patch.source.ref
        known = {
            SourceKind.BODY: ref in bodies,
            SourceKind.DOMAIN_FACE: ref in faces,
            SourceKind.REGION: has_imported and (regions is None or ref in regions),
        }[kind]
        if not known:
            issues.append(_blocking(
                "PATCH_SOURCE_UNKNOWN",
                f"Patch '{patch.name}' comes from {kind.value} '{ref}', which does not exist.",
                "Choose a body, a face of the generated domain, or an imported region.",
                field=f"patches.{i}.source", patch=patch.name))
            continue
        body = bodies.get(ref) if kind is SourceKind.BODY else None
        if body is not None:
            wanted = PatchType.ROTATING_WALL if body.motion is Motion.ROTATING else PatchType.WALL
            if patch.type is not wanted:
                issues.append(_issue(
                    IssueSeverity.ERROR, "BODY_PATCH_TYPE_MISMATCH",
                    f"Patch '{patch.name}' of {body.motion.value} body '{body.name}' is "
                    f"{patch.type.value}; it must be {wanted.value}.",
                    f"Set the patch type to {wanted.value}, or change the body's motion.",
                    explanation="A rotating body's wall moves with its zone; a stationary "
                    "body's wall does not. A split body's patch is its stationary part.",
                    field=f"patches.{i}.type", patch=patch.name))
        if kind is SourceKind.DOMAIN_FACE and patch.type is PatchType.ROTATING_WALL:
            issues.append(_issue(
                IssueSeverity.ERROR, "ROTATING_WALL_ON_DOMAIN",
                f"Patch '{patch.name}' on the generated domain is a ROTATING_WALL.",
                "Use INLET, OUTLET, WALL or SLIP for the domain faces.",
                field=f"patches.{i}.type", patch=patch.name))

    sources = [(p.source.kind, p.source.ref) for p in config.patches]
    for kind, ref in _duplicates(sources):
        issues.append(_blocking(
            "PATCH_SOURCE_DUPLICATE", f"{kind.value} '{ref}' is the source of two patches.",
            "Give each body, face or region one patch, so it has exactly one type.",
            source=f"{kind.value}:{ref}"))

    used = set(sources)
    for name in bodies:
        if (SourceKind.BODY, name) not in used:
            issues.append(_blocking(
                "BODY_WITHOUT_PATCH", f"Body '{name}' has no patch.",
                "Add a patch with the body as its source.", body=name))
    for face in faces:
        if (SourceKind.DOMAIN_FACE, face) not in used:
            issues.append(_blocking(
                "DOMAIN_FACE_WITHOUT_PATCH", f"Domain face '{face}' has no patch and no type.",
                "Add a patch for this face and choose its type.", face=face))
    if regions is not None:
        joined = {name for joint in config.joints for name in (joint.first, joint.second)}
        for region in sorted(regions - joined):
            if (SourceKind.REGION, region) not in used:
                issues.append(_blocking(
                    "REGION_WITHOUT_PATCH",
                    f"Imported region '{region}' has no patch and is not a joint.",
                    "Add a patch for it and choose its type, or list it in a joint.",
                    region=region))

    if domain is not None:
        types = {p.type for p in config.patches}
        for wanted, code in ((PatchType.INLET, "NO_INLET"), (PatchType.OUTLET, "NO_OUTLET")):
            if wanted not in types:
                issues.append(_blocking(
                    code, f"No patch is of type {wanted.value}.",
                    f"Choose the {wanted.value.lower()} patch in the patch table."))
    return issues


def _joint_issues(config: MachineProjectConfig) -> list[Issue]:
    if not config.joints:
        return []
    if not isinstance(config.domain, ImportedDomain):
        return [_blocking(
            "JOINT_WITHOUT_IMPORTED_DOMAIN",
            "Joints are listed, but the domain is not imported.",
            "Remove the joints, or import the domain parts they join.", field="joints")]
    issues = []
    regions = _known_regions(config)
    patch_regions = {p.source.ref for p in config.patches if p.source.kind is SourceKind.REGION}
    names = [name for joint in config.joints for name in (joint.first, joint.second)]
    for i, joint in enumerate(config.joints):
        if joint.first == joint.second:
            issues.append(_blocking(
                "JOINT_INVALID", f"Joint {i} joins region '{joint.first}' to itself.",
                "Name the two coincident regions of the two parts.", field=f"joints.{i}"))
    for name in sorted(set(names)):
        if regions is not None and name not in regions:
            issues.append(_blocking(
                "JOINT_REGION_UNKNOWN", f"Joint region '{name}' does not exist.",
                "Name a region of an imported part.", region=name))
        if name in patch_regions:
            issues.append(_blocking(
                "JOINT_REGION_IS_PATCH",
                f"Region '{name}' is both a joint and a patch.",
                "A joint becomes an AMI pair; remove its patch.", region=name))
    for name in _duplicates(names):
        issues.append(_blocking(
            "JOINT_REGION_REUSED", f"Region '{name}' is in more than one joint.",
            "Each region joins exactly one other region.", region=name))
    return issues


def _zone_issues(config: MachineProjectConfig, limits: MachineThresholds) -> list[Issue]:
    issues = []
    zones = _zones(config)
    for i, zone, cylinder in zones:
        if cylinder.signed_distance(_point(zone.location_in_mesh))[0] >= 0.0:
            issues.append(_blocking(
                "INNER_POINT_OUTSIDE_ZONE",
                f"The mesh point of rotating zone '{zone.name}' is not inside the zone.",
                f"Move rotating_zones.{i}.location_in_mesh inside the zone, outside every "
                "body.", field=f"rotating_zones.{i}.location_in_mesh"))

    domain = config.domain
    if isinstance(domain, BoxDomain | CylinderDomain):
        outer = _point(domain.location_in_mesh)
        distance = _domain_signed_distance(config, outer)
        assert distance is not None
        if distance[0] >= 0.0 or any(c.signed_distance(outer)[0] <= 0.0 for _, _, c in zones):
            issues.append(_blocking(
                "OUTER_POINT_INVALID",
                "The domain mesh point must be inside the domain and outside every "
                "rotating zone.",
                "Move domain.location_in_mesh into the stationary fluid.",
                field="domain.location_in_mesh"))
        for i, zone, cylinder in zones:
            circles = cylinder.end_circles(limits.zone_circle_points)
            reach = _domain_signed_distance(config, circles)
            assert reach is not None
            clearance = -float(reach.max())
            limit = limits.min_clearance_cells * domain.cell_size
            if clearance <= 0.0:
                issues.append(_blocking(
                    "ZONE_OUTSIDE_DOMAIN",
                    f"Rotating zone '{zone.name}' is not inside the domain.",
                    "Enlarge the domain or move the zone.", field=f"rotating_zones.{i}",
                    clearance=clearance))
            elif clearance < limit:
                issues.append(_issue(
                    IssueSeverity.WARNING, "ZONE_CLEARANCE_SMALL",
                    f"Clearance between rotating zone '{zone.name}' and the domain "
                    f"({clearance:.4g} m) is below {limits.min_clearance_cells:g} domain "
                    "cell(s).", "Enlarge the domain or reduce the zone.",
                    category=IssueCategory.GEOMETRY, field=f"rotating_zones.{i}",
                    clearance=clearance, limit=limit))

    for k, (i, first, a) in enumerate(zones):
        for j, second, b in zones[k + 1:]:
            issue = _overlap_issue(first, a, second, b)
            if issue is not None:
                issues.append(dataclasses.replace(
                    issue, details={**issue.details, "fields": [f"rotating_zones.{i}",
                                                                f"rotating_zones.{j}"]}))
    return issues


def _overlap_issue(first: RotatingZone, a: _Cylinder,
                   second: RotatingZone, b: _Cylinder) -> Issue | None:
    names = f"'{first.name}' and '{second.name}'"
    if a.axis is b.axis:
        apart_along = a.axis_max <= b.axis_min or b.axis_max <= a.axis_min
        d = math.hypot(a.centre_u - b.centre_u, a.centre_v - b.centre_v)
        apart_across = (d >= a.radius + b.radius or d + b.radius <= a.hole
                        or d + a.radius <= b.hole)
        if apart_along or apart_across:
            return None
        return _blocking("ZONES_OVERLAP", f"Rotating zones {names} overlap.",
                         "Move or resize the zones so that they do not overlap.")
    (alo, ahi), (blo, bhi) = a.bounds(), b.bounds()
    if np.any(ahi <= blo) or np.any(bhi <= alo):
        return None
    return _issue(
        IssueSeverity.WARNING, "ZONES_MAY_OVERLAP",
        f"Rotating zones {names} have different axes and their bounding boxes overlap.",
        "Check that the zones do not overlap.",
        explanation="Zones on different axes are compared by their bounding boxes only.")


def _imported_notice(config: MachineProjectConfig) -> list[Issue]:
    if not _imported_surfaces(config):
        return []
    return [_issue(
        IssueSeverity.INFO, "IMPORTED_SURFACES_NOT_CHECKED",
        "Imported domain and zone surfaces are not checked yet: closedness, region "
        "names, binary STL, and whether bodies lie inside them.",
        "These checks come with the imported-domain reader.")]


# --- geometry -----------------------------------------------------------------------------

def load_bodies(
    config: MachineProjectConfig,
) -> tuple[dict[str, trimesh.Trimesh], tuple[Issue, ...]]:
    """Import every body's STL and apply its transform in memory."""
    meshes: dict[str, trimesh.Trimesh] = {}
    issues: list[Issue] = []
    for body in config.bodies:
        mesh, found = load_rotor_geometry(_geometry(body.source, body.name))
        issues.extend(dataclasses.replace(i, details={**i.details, "body": body.name})
                      for i in found)
        if mesh is not None:
            meshes[body.name] = mesh
    return meshes, tuple(issues)


def _geometry(source: StlSource, name: str) -> RotorGeometryConfig:
    return RotorGeometryConfig(
        source_path=source.source_path, source_units=source.source_units, scale=source.scale,
        rotation_deg=source.rotation_deg, translation=source.translation, patch_name=name)


def surface_points(mesh: trimesh.Trimesh, spacing: float,
                   max_points: int) -> tuple[np.ndarray, float]:
    """Vertices plus points on every face, at most about `spacing` apart.

    Returns the points and the spacing used (wider when max_points is reached).
    """
    vertices = np.asarray(mesh.vertices, dtype=float)
    triangles = vertices[np.asarray(mesh.faces)]
    if len(triangles) == 0:
        return vertices, spacing
    edges = np.linalg.norm(triangles - np.roll(triangles, 1, axis=1), axis=2).max(axis=1)
    divisions = np.maximum(1, np.ceil(edges / spacing)).astype(np.int64)
    total = int(((divisions + 1) * (divisions + 2) // 2).sum())
    if total > max_points:
        factor = math.sqrt(total / max_points)
        spacing *= factor
        divisions = np.maximum(1, np.ceil(divisions / factor)).astype(np.int64)
    points = [vertices]
    for n in np.unique(divisions[divisions > 1]):
        a, b = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        keep = a + b <= n
        weights = np.column_stack([a[keep], b[keep], n - a[keep] - b[keep]]) / n
        points.append(np.einsum("wk,mkd->mwd", weights,
                                triangles[divisions == n]).reshape(-1, 3))
    return np.vstack(points), spacing


def check_geometry(config: MachineProjectConfig, meshes: Mapping[str, trimesh.Trimesh],
                   thresholds: MachineThresholds | None = None) -> tuple[Issue, ...]:
    """Section 3.1 rules for the bodies in `meshes` (name -> transformed surface)."""
    limits = thresholds or MachineThresholds()
    zones = _zones(config)
    cells = [zone.cell_size for _, zone, _ in zones]
    if isinstance(config.domain, BoxDomain | CylinderDomain):
        cells.append(config.domain.cell_size)
    issues: list[Issue] = []
    for body in config.bodies:
        mesh = meshes.get(body.name)
        if mesh is None:
            continue
        # A body's vertices can all lie outside a zone or the domain while its
        # faces pass through (a pole with vertices only at its ends).
        points, spacing = surface_points(mesh, limits.sample_spacing_cells * min(cells),
                                         limits.max_sample_points) if cells else (
            np.asarray(mesh.vertices, dtype=float), 0.0)
        issues.extend(_domain_body_issues(config, body.name, body.motion, points))
        for _, zone, cylinder in zones:
            issues.extend(_zone_body_issues(body.name, body.motion, body.zone, zone,
                                            cylinder.signed_distance(points), spacing,
                                            limits))
        issues.extend(_mesh_point_issues(config, body.name, mesh))
    return tuple(issues)


def _geometry_issue(severity: IssueSeverity, code: str, message: str, action: str,
                    **details: Any) -> Issue:
    return _issue(severity, code, message, action, category=IssueCategory.GEOMETRY, **details)


def _crossed_faces(config: MachineProjectConfig, outside: np.ndarray) -> list[str]:
    """Faces of the generated domain that the points outside it lie beyond."""
    domain = config.domain
    if isinstance(domain, BoxDomain):
        lo = _point(domain.bounds.minimum)[0]
        hi = _point(domain.bounds.maximum)[0]
        beyond = {face_name: bool(np.any(test)) for axis in Axis for face_name, test in (
            (box_face(axis, False), outside[:, axis.position] <= lo[axis.position]),
            (box_face(axis, True), outside[:, axis.position] >= hi[axis.position]))}
        return [face for face in BOX_FACES if beyond[face]]
    assert isinstance(domain, CylinderDomain)
    cylinder = _Cylinder.of_domain(domain)
    u, v = plane_axes(cylinder.axis)
    along = outside[:, cylinder.axis.position]
    radial = np.hypot(outside[:, u.position] - cylinder.centre_u,
                      outside[:, v.position] - cylinder.centre_v)
    beyond = {"axis_min": bool(np.any(along <= cylinder.axis_min)),
              "axis_max": bool(np.any(along >= cylinder.axis_max)),
              "side": bool(np.any(radial >= cylinder.radius))}
    return [face for face in CYLINDER_FACES if beyond[face]]


def _domain_body_issues(config: MachineProjectConfig, name: str, motion: Motion,
                        points: np.ndarray) -> list[Issue]:
    distance = _domain_signed_distance(config, points)
    if distance is None:
        return []
    if distance.min() >= 0.0:
        return [_geometry_issue(
            IssueSeverity.BLOCKING, "BODY_OUTSIDE_DOMAIN",
            f"Body '{name}' lies outside the domain.",
            "Enlarge or move the domain, or check the body's units and transform.",
            body=name)]
    if distance.max() < 0.0:
        return []
    faces = _crossed_faces(config, points[distance >= 0.0])
    patches = [config.patch_for(SourceKind.DOMAIN_FACE, face) for face in faces]
    details: dict[str, Any] = {
        "body": name, "faces": faces, "patches": [p.name for p in patches if p is not None],
        "outside": float(distance.max())}
    where = ", ".join(faces)
    if motion is Motion.ROTATING:
        return [_geometry_issue(
            IssueSeverity.BLOCKING, "ROTATING_BODY_CROSSES_DOMAIN_BOUNDARY",
            f"Rotating body '{name}' reaches through the domain boundary ({where}).",
            "Enlarge the domain; a rotating body lies entirely inside it.", **details)]
    # A split body counts as stationary: only its stationary part can reach the domain.
    flow = [p.name for p in patches
            if p is not None and p.type in (PatchType.INLET, PatchType.OUTLET)]
    if flow:
        return [_geometry_issue(
            IssueSeverity.ERROR, "BODY_CROSSES_INLET_OUTLET",
            f"Body '{name}' reaches through the domain boundary at {where}, through "
            f"the inlet or outlet ({', '.join(flow)}).",
            "Enlarge the domain, or move the body; a body may pass only through wall "
            "or slip faces.", **details)]
    return [_geometry_issue(
        IssueSeverity.WARNING, "BODY_CROSSES_DOMAIN_BOUNDARY",
        f"Body '{name}' reaches through the domain boundary at {where}; the part "
        "outside is not meshed.",
        "Intended for a pole or tower through a wall or slip face; otherwise enlarge "
        "the domain.", **details)]


def _zone_body_issues(name: str, motion: Motion, own_zone: str | None, zone: RotatingZone,
                      distance: np.ndarray, spacing: float,
                      limits: MachineThresholds) -> list[Issue]:
    details: dict[str, Any] = {"body": name, "zone": zone.name, "sample_spacing": spacing}
    limit = limits.min_clearance_cells * zone.cell_size
    inside, outside = distance.min() < 0.0, distance.max() > 0.0
    if zone.name == own_zone and motion is Motion.ROTATING:
        if distance.max() >= 0.0:
            return [_geometry_issue(
                IssueSeverity.BLOCKING, "ROTATING_BODY_OUTSIDE_ZONE",
                f"Rotating body '{name}' is not fully inside rotating zone '{zone.name}'.",
                "Enlarge the zone or check its centre and axis.",
                outside=float(distance.max()), **details)]
        clearance = -float(distance.max())
        if clearance < limit:
            return [_geometry_issue(
                IssueSeverity.WARNING, "ZONE_CLEARANCE_SMALL",
                f"Clearance between body '{name}' and the wall of zone '{zone.name}' "
                f"({clearance:.4g} m) is below {limits.min_clearance_cells:g} zone cell(s).",
                "Enlarge the zone, or reduce its cell size.",
                clearance=clearance, limit=limit, **details)]
        return []
    if zone.name == own_zone:  # SPLIT: the zone's interface cuts the body
        if inside and outside:
            return []
        return [_geometry_issue(
            IssueSeverity.ERROR, "SPLIT_BODY_NOT_SPLIT",
            f"Split body '{name}' does not pass through rotating zone '{zone.name}'.",
            "A split body (a pole whose inner part rotates) must cross the zone. Use a "
            "STATIONARY body for a pole in an annular zone's hole.", **details)]
    if distance.min() <= 0.0:
        stationary = motion is Motion.STATIONARY
        return [_geometry_issue(
            IssueSeverity.BLOCKING,
            "STATIONARY_BODY_CROSSES_INTERFACE" if stationary else "BODY_IN_OTHER_ZONE",
            f"{'Stationary' if stationary else motion.value.capitalize()} body '{name}' "
            f"enters rotating zone '{zone.name}'.",
            "Pass a pole through an annular zone's hole (STATIONARY), or split it by the "
            "zone (SPLIT); otherwise move the body or the zone.",
            depth=-float(distance.min()), **details)]
    clearance = float(distance.min())
    if clearance < limit:
        return [_geometry_issue(
            IssueSeverity.WARNING, "BODY_ZONE_CLEARANCE_SMALL",
            f"Clearance between body '{name}' and rotating zone '{zone.name}' "
            f"({clearance:.4g} m) is below {limits.min_clearance_cells:g} zone cell(s).",
            "Move the body or resize the zone.", clearance=clearance, limit=limit, **details)]
    return []


def _mesh_points(config: MachineProjectConfig) -> list[tuple[str, Vec3]]:
    points = [(f"rotating_zones.{i}.location_in_mesh", z.location_in_mesh)
              for i, z in enumerate(config.rotating_zones)]
    domain = config.domain
    if isinstance(domain, BoxDomain | CylinderDomain):
        points.append(("domain.location_in_mesh", domain.location_in_mesh))
    elif isinstance(domain, ImportedDomain):
        points.extend((f"domain.parts.{i}.location_in_mesh", p.location_in_mesh)
                      for i, p in enumerate(domain.parts))
    return points


def _mesh_point_issues(config: MachineProjectConfig, name: str,
                       mesh: trimesh.Trimesh) -> list[Issue]:
    merged = mesh.copy()
    merged.merge_vertices()
    points = _mesh_points(config)
    if not merged.is_watertight:
        return [_geometry_issue(
            IssueSeverity.WARNING, "MESH_POINT_UNCHECKED",
            f"Body '{name}' is not closed, so it cannot be checked whether a mesh point "
            "lies inside it.", "Check the mesh points manually against the geometry.",
            body=name)]
    coordinates = np.vstack([_point(vector) for _, vector in points])
    inside = contains_points(coordinates, merged.vertices, merged.faces)
    return [_geometry_issue(
        IssueSeverity.BLOCKING, "MESH_POINT_IN_BODY",
        f"{field} lies inside body '{name}'.", f"Move {field} into the fluid.",
        field=field, body=name)
        for (field, _), hit in zip(points, inside, strict=True) if hit]


# --- everything -----------------------------------------------------------------------------

def region_owners(config: MachineProjectConfig) -> dict[str, str]:
    """Imported region name -> the configuration path of the surface holding it.

    Files whose region names cannot be read (missing, binary) are left out;
    the imported-domain checks (G2) report them.
    """
    surfaces: list[tuple[str, ImportedSurface]] = [
        (f"rotating_zones.{i}", z.shape) for i, z in enumerate(config.rotating_zones)
        if isinstance(z.shape, ImportedZone)]
    if isinstance(config.domain, ImportedDomain):
        surfaces.extend((f"domain.parts.{i}", p) for i, p in enumerate(config.domain.parts))
    owners: dict[str, str] = {}
    for owner, surface in surfaces:
        names = surface.file_regions()
        if names is None:
            names = tuple(name for f in surface.files
                          for name in stl_region_names(f.source_path) or ())
        owners.update((name, owner) for name in names)
    return owners


def check_rotating_walls(config: MachineProjectConfig) -> tuple[Issue, ...]:
    """Every rotating-wall region lies in a rotating zone (an imported zone).

    A body's rotating wall is covered by BODY_PATCH_TYPE_MISMATCH and
    ROTATING_BODY_OUTSIDE_ZONE; a domain face by ROTATING_WALL_ON_DOMAIN.
    """
    walls = [(i, p) for i, p in enumerate(config.patches)
             if p.type is PatchType.ROTATING_WALL and p.source.kind is SourceKind.REGION]
    if not walls:
        return ()
    owners = region_owners(config)
    return tuple(
        _issue(IssueSeverity.ERROR, "ROTATING_WALL_OUTSIDE_ZONE",
               f"Rotating-wall patch '{patch.name}' comes from region "
               f"'{patch.source.ref}' of a stationary domain part ({owner}).",
               "Make the patch a WALL, or import the region as part of a rotating zone.",
               explanation="A rotating wall moves with its zone; a stationary part's "
               "mesh does not move.",
               field=f"patches.{i}.type", patch=patch.name, owner=owner)
        for i, patch in walls
        if (owner := owners.get(patch.source.ref, "")).startswith("domain.parts."))


def validate_machine(raw: Any,
                     thresholds: MachineThresholds | None = None) -> MachineValidationResult:
    """Parse the configuration, load the bodies, and run every check."""
    config, issues = parse_config(raw)
    if config is None:
        return MachineValidationResult(None, issues)
    meshes, loaded = load_bodies(config)
    return MachineValidationResult(config, (
        *check_config(config, thresholds), *check_rotating_walls(config), *loaded,
        *check_geometry(config, meshes, thresholds)))
