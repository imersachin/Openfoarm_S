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
from dataclasses import dataclass, field
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
    StlFormat,
    StlSource,
    box_face,
)
from machines.domains import (
    BACKGROUND_PATCH,
    SurfaceInfo,
    box_grid,
    cylinder_grid,
    read_imported,
    stl_region_names,
    surface_grid,
)
from machines.presets import DOMAIN_CHOICES
from machines.vawt_migration import NotVawtConvertible, to_vawt
from vawt.case_generator import RESERVED_NAMES, Grid
from vawt.config import (
    SCHEMA_VERSION_INVALID,
    SCHEMA_VERSION_NEWER,
    Axis,
    InterfaceType,
    RotorGeometryConfig,
    Vec3,
    plane_axes,
)
from vawt.rotor_metrics import compute_rotor_metrics
from vawt.validation import (
    UNSET_ERROR_TYPES,
    ValidationThresholds,
    load_rotor_geometry,
)
from vawt.validation import check_config as check_vawt_config


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
    # A mesh point closer than this fraction of a cell to a background-cell face
    # counts as on it (V0 E4), as in the VAWT checks.
    point_face_tolerance_cells: float = 1e-6
    # The VAWT checks run for a VAWT project that converts to the VAWT model.
    vawt: ValidationThresholds = field(default_factory=ValidationThresholds)


# Patch names the generated meshes use themselves.
RESERVED_PATCH_NAMES = RESERVED_NAMES | {BACKGROUND_PATCH}


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


Owners = Mapping[str, str]  # imported region name -> configuration path of its surface


def _stem_owners(config: MachineProjectConfig) -> dict[str, str] | None:
    """Region owners known without reading files (every surface one file per
    patch), or None."""
    owners: dict[str, str] = {}
    for owner, surface in _owned_surfaces(config):
        names = surface.file_regions()
        if names is None:
            return None
        owners.update((name, owner) for name in names)
    return owners


def _owned_surfaces(config: MachineProjectConfig) -> list[tuple[str, ImportedSurface]]:
    surfaces: list[tuple[str, ImportedSurface]] = [
        (f"rotating_zones.{i}", z.shape) for i, z in enumerate(config.rotating_zones)
        if isinstance(z.shape, ImportedZone)]
    if isinstance(config.domain, ImportedDomain):
        surfaces.extend((f"domain.parts.{i}", p) for i, p in enumerate(config.domain.parts))
    return surfaces


def _known_regions(config: MachineProjectConfig, owners: Owners | None) -> set[str] | None:
    """Every imported region name, when known (read, or from file names)."""
    known = owners if owners is not None else _stem_owners(config)
    return None if known is None else set(known)


# --- check_config -----------------------------------------------------------------------

def check_config(config: MachineProjectConfig, thresholds: MachineThresholds | None = None,
                 owners: Owners | None = None) -> tuple[Issue, ...]:
    """Rules between sections (section 10), without reading any STL file.

    owners: the imported regions as read (read_imported); without it, region
    names are known only for surfaces given one file per patch.
    """
    limits = thresholds or MachineThresholds()
    return (*_name_issues(config), *_machine_issues(config), *_patch_issues(config, owners),
            *_joint_issues(config, owners), *_zone_issues(config, limits),
            *_grid_point_issues(config, limits), *_imported_notice(config))


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
        if patch.name in RESERVED_PATCH_NAMES:
            issues.append(_blocking(
                "RESERVED_PATCH_NAME",
                f"'{patch.name}' (patches.{i}.name) is a name the generated mesh uses itself.",
                f"Choose another name; reserved: {', '.join(sorted(RESERVED_PATCH_NAMES))}.",
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


def _patch_issues(config: MachineProjectConfig, owners: Owners | None) -> list[Issue]:
    issues = []
    domain = config.domain
    faces: tuple[str, ...] = ()
    if isinstance(domain, BoxDomain):
        faces = BOX_FACES
    elif isinstance(domain, CylinderDomain):
        faces = CYLINDER_FACES
    regions = _known_regions(config, owners)
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


def _joint_issues(config: MachineProjectConfig, owners: Owners | None) -> list[Issue]:
    if not config.joints:
        return []
    if not isinstance(config.domain, ImportedDomain):
        return [_blocking(
            "JOINT_WITHOUT_IMPORTED_DOMAIN",
            "Joints are listed, but the domain is not imported.",
            "Remove the joints, or import the domain parts they join.", field="joints")]
    issues = []
    known = owners if owners is not None else _stem_owners(config)
    regions = None if known is None else set(known)
    patch_regions = {p.source.ref for p in config.patches if p.source.kind is SourceKind.REGION}
    names = [name for joint in config.joints for name in (joint.first, joint.second)]
    for i, joint in enumerate(config.joints):
        if joint.first == joint.second:
            issues.append(_blocking(
                "JOINT_INVALID", f"Joint {i} joins region '{joint.first}' to itself.",
                "Name the two coincident regions of the two parts.", field=f"joints.{i}"))
        elif known is not None and known.get(joint.first, "a") == known.get(joint.second, "b"):
            issues.append(_blocking(
                "JOINT_SAME_PART",
                f"Joint {i} joins two regions of the same surface ({known[joint.first]}).",
                "A joint joins coincident regions of two separately meshed parts.",
                field=f"joints.{i}"))
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
        "Whether bodies and zones lie inside the imported surfaces is not checked yet.",
        "Check the placement of bodies and zones against the imported surfaces.",
        explanation="The imported files themselves (regions, closedness, binary STL) "
        "are checked by check_imported.")]


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
    """Imported region name -> the configuration path of the surface holding it,
    from the region names alone (no geometry is read).

    Files whose region names cannot be read (missing, binary) are left out;
    check_imported reports them.
    """
    owners: dict[str, str] = {}
    for owner, surface in _owned_surfaces(config):
        names = surface.file_regions()
        if names is None:
            names = tuple(name for f in surface.files
                          for name in stl_region_names(f.source_path) or ())
        owners.update((name, owner) for name in names)
    return owners


def surface_owners(surfaces: Iterable[SurfaceInfo]) -> dict[str, str] | None:
    """Region owners of read surfaces; None when a file could not be read."""
    surfaces = list(surfaces)
    if not all(s.readable for s in surfaces):
        return None
    return {region.name: s.owner for s in surfaces for region in s.regions}


def check_rotating_walls(config: MachineProjectConfig,
                         owners: Owners | None = None) -> tuple[Issue, ...]:
    """Every rotating-wall region lies in a rotating zone (an imported zone).

    A body's rotating wall is covered by BODY_PATCH_TYPE_MISMATCH and
    ROTATING_BODY_OUTSIDE_ZONE; a domain face by ROTATING_WALL_ON_DOMAIN.
    """
    walls = [(i, p) for i, p in enumerate(config.patches)
             if p.type is PatchType.ROTATING_WALL and p.source.kind is SourceKind.REGION]
    if not walls:
        return ()
    known = owners if owners is not None else region_owners(config)
    return tuple(
        _issue(IssueSeverity.ERROR, "ROTATING_WALL_OUTSIDE_ZONE",
               f"Rotating-wall patch '{patch.name}' comes from region "
               f"'{patch.source.ref}' of a stationary domain part ({owner}).",
               "Make the patch a WALL, or import the region as part of a rotating zone.",
               explanation="A rotating wall moves with its zone; a stationary part's "
               "mesh does not move.",
               field=f"patches.{i}.type", patch=patch.name, owner=owner)
        for i, patch in walls
        if (owner := known.get(patch.source.ref, "")).startswith("domain.parts."))


# --- imported surfaces (section 5.3; decisions E3, E4) ----------------------------------------

def _owned_by(config: MachineProjectConfig, owner: str) -> tuple[str, Vec3, float]:
    """(name, mesh point, cell size) of an imported part or zone."""
    section, index = owner.rsplit(".", 1)
    if section == "domain.parts":
        assert isinstance(config.domain, ImportedDomain)
        part = config.domain.parts[int(index)]
        return part.name, part.location_in_mesh, part.cell_size
    zone = config.rotating_zones[int(index)]
    return zone.name, zone.location_in_mesh, zone.cell_size


def check_imported(config: MachineProjectConfig, surfaces: Iterable[SurfaceInfo],
                   thresholds: MachineThresholds | None = None) -> tuple[Issue, ...]:
    """The imported files as read: readable, region names, binary STL given as
    named regions, closed (each part on its own, E4), and the mesh point."""
    limits = thresholds or MachineThresholds()
    surfaces = list(surfaces)
    issues: list[Issue] = []
    for surface in surfaces:
        for f in surface.files:
            if f.error is not None:
                issues.append(_blocking(
                    "IMPORTED_FILE_UNREADABLE", f"{f.path.name} of {surface.owner} cannot be "
                    f"read: {f.error}.", "Check the file and export it again.",
                    category=IssueCategory.INPUT, stage=IssueStage.GEOMETRY_IMPORT,
                    owner=surface.owner, file=str(f.path)))
            elif f.binary and surface.format is StlFormat.NAMED_REGIONS:
                issues.append(_issue(
                    IssueSeverity.ERROR, "BINARY_STL_AS_NAMED_REGIONS",
                    f"{f.path.name} is binary STL, which stores no region names: the "
                    f"whole surface would become one patch, '{f.path.stem}'.",
                    "Export ASCII STL with one named region per patch, or one STL file "
                    "per patch.", category=IssueCategory.INPUT,
                    stage=IssueStage.GEOMETRY_IMPORT,
                    explanation="Detected by the file size (84 + 50 bytes per triangle), "
                    "not by a leading 'solid'. In G0 such a mesh lost its inlet and outlet "
                    "and still passed checkMesh.", owner=surface.owner, file=str(f.path)))
        if surface.readable and surface.open_edges:
            issues.append(_blocking(
                "IMPORTED_SURFACE_OPEN",
                f"The surfaces of {surface.owner} ('{surface.name}') do not join into a "
                f"closed surface ({surface.open_edges} open edges).",
                "Add the missing surfaces, or close the gaps in CAD.",
                category=IssueCategory.GEOMETRY, stage=IssueStage.GEOMETRY_VALIDATION,
                explanation="snappyHexMesh then meshes through the gap into the background "
                "box, exits 0, and checkMesh passes (G0 R2).",
                owner=surface.owner, open_edges=surface.open_edges))
        issues.extend(_imported_point_issues(config, surface, limits))

    names = [r.name for s in surfaces for r in s.regions]
    for name in sorted(set(names)):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or len(name) > 64:
            issues.append(_blocking(
                "REGION_NAME_INVALID", f"Region name '{name}' is not a valid OpenFOAM name.",
                "Rename the region (letters, digits, underscores; not starting with a "
                "digit), or the file for one file per patch.", region=name))
        elif name in RESERVED_PATCH_NAMES:
            issues.append(_blocking(
                "REGION_NAME_RESERVED",
                f"Region name '{name}' is a name the generated mesh uses itself.",
                f"Rename the region; reserved: {', '.join(sorted(RESERVED_PATCH_NAMES))}.",
                region=name))
    for name in _duplicates(names):
        issues.append(_blocking(
            "REGION_NAME_DUPLICATE", f"Region name '{name}' is used more than once.",
            "Give every region of every imported surface its own name (patches and "
            "joints refer to regions by name).", region=name,
            owners=sorted({s.owner for s in surfaces for r in s.regions if r.name == name})))
    return tuple(issues)


def _imported_point_issues(config: MachineProjectConfig, surface: SurfaceInfo,
                           limits: MachineThresholds) -> list[Issue]:
    name, point, cell_size = _owned_by(config, surface.owner)
    field = f"{surface.owner}.location_in_mesh"
    if not surface.closed:
        return []
    union = surface.union()
    issues = []
    if not contains_points(_point(point), union.vertices, union.faces)[0]:
        code = ("INNER_POINT_OUTSIDE_ZONE" if surface.owner.startswith("rotating_zones")
                else "PART_POINT_OUTSIDE")
        issues.append(_blocking(
            code, f"The mesh point of '{name}' is not inside its imported surface.",
            f"Move {field} inside the surface.", field=field,
            explanation="snappyHexMesh keeps the region holding the mesh point; outside "
            "the surface it keeps the background box instead."))
    grid = surface_grid(surface, cell_size)
    if grid is not None:
        issues.extend(_on_face(field, point, grid, limits))
    return issues


# --- mesh points on background-cell faces (V0 E4) ------------------------------------------

def _on_face(field: str, point: Vec3, grid: Grid, limits: MachineThresholds) -> list[Issue]:
    hit = []
    for index, axis in enumerate("xyz"):
        offset = ((point.x, point.y, point.z)[index] - grid.minimum[index]) / grid.spacing(index)
        if abs(offset - round(offset)) < limits.point_face_tolerance_cells:
            hit.append(axis)
    if not hit:
        return []
    where = "edge" if len(hit) > 1 else "face"
    return [_issue(
        IssueSeverity.ERROR, "MESH_POINT_ON_CELL_FACE",
        f"{field} lies on a background-cell {where} (planes normal to {', '.join(hit)}); "
        "snappyHexMesh may not find the region.",
        f"Move {field} by a fraction of a cell (for example a quarter) along "
        f"{', '.join(hit)}.",
        explanation="snappyHexMesh stops with 'is not inside the mesh or on a face or "
        "edge' for such points.", field=field, axes=hit)]


def _grid_point_issues(config: MachineProjectConfig, limits: MachineThresholds) -> list[Issue]:
    domain = config.domain
    if isinstance(domain, BoxDomain):
        return _on_face("domain.location_in_mesh", domain.location_in_mesh, box_grid(domain),
                        limits)
    if isinstance(domain, CylinderDomain):
        return _on_face("domain.location_in_mesh", domain.location_in_mesh,
                        cylinder_grid(domain), limits)
    return []


# --- everything -----------------------------------------------------------------------------

def _unique(issues: Iterable[Issue]) -> tuple[Issue, ...]:
    """Drop an issue repeating an earlier one's code and field (the VAWT checks
    report some machine findings again)."""
    seen: set[tuple[str, str]] = set()
    kept = []
    for issue in issues:
        key = (issue.code, str(issue.details.get("field", "")))
        if key[1] and key in seen:
            continue
        seen.add(key)
        kept.append(issue)
    return tuple(kept)


def validate_machine(raw: Any,
                     thresholds: MachineThresholds | None = None) -> MachineValidationResult:
    """Parse the configuration, read the imported surfaces, load the bodies,
    and run every check."""
    config, issues = parse_config(raw)
    if config is None:
        return MachineValidationResult(None, issues)
    surfaces = read_imported(config)
    owners = surface_owners(surfaces)
    meshes, loaded = load_bodies(config)
    return MachineValidationResult(config, _unique((
        *check_config(config, thresholds, owners), *check_imported(config, surfaces, thresholds),
        *check_rotating_walls(config, owners), *loaded,
        *check_geometry(config, meshes, thresholds),
        *check_vawt(config, meshes, thresholds))))


# VAWT checks with no machine equivalent. The others (rotor in the zone, mesh
# points, zone in the domain, clearances, reserved names) are machine checks too.
VAWT_ONLY_CHECKS = frozenset({
    "AMI_REQUIRES_DOMAIN", "WAKE_OUTSIDE_DOMAIN", "MESH_POINT_ON_CELL_FACE",
    "ABSOLUTE_LAYER_TOO_THICK", "LAYER_MIN_THICKNESS_TOO_LARGE", "LAYERS_TOO_THIN_FOR_CELLS",
    "TOO_FEW_ZONE_CELLS", "BODY_INSIDE_OUT",
})


def check_vawt(config: MachineProjectConfig, meshes: Mapping[str, trimesh.Trimesh],
               thresholds: MachineThresholds | None = None) -> tuple[Issue, ...]:
    """The VAWT checks for a VAWT project that the VAWT workflow would mesh.

    Such a project is held to everything vawt.validation checks (G1 review).
    """
    if config.machine is not MachineType.VAWT:
        return ()
    try:
        vawt = to_vawt(config)
    except NotVawtConvertible:
        return ()
    mesh = meshes.get(config.bodies[0].name)
    if mesh is None:
        return ()
    metrics = compute_rotor_metrics(mesh.vertices, vawt.rotor.axis)
    limits = (thresholds or MachineThresholds()).vawt
    return tuple(i for i in check_vawt_config(vawt, mesh, metrics, limits)
                 if i.code in VAWT_ONLY_CHECKS)
