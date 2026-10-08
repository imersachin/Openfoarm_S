"""Engineering checks before meshing (spec section 8).

Pure Python: no OpenFOAM, no files written. Every finding is a structured
issue; BLOCKING and ERROR stop a run. Thresholds are parameters, not constants
inside the checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh
from pydantic import ValidationError

from core.config.validation import issues_from_validation_error
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from geometry.importer import import_stl
from geometry.metrics import body_orientations, contains_points
from geometry.transformer import GeometryTransform
from geometry.validator import TrimeshGeometryValidator
from vawt.case_generator import (
    MERGED,
    OUTER,
    RESERVED_NAMES,
    ROTOR,
    Grid,
    background_grids,
    case_layout,
    zone_cell_size,
)
from vawt.config import (
    SCHEMA_VERSION_INVALID,
    SCHEMA_VERSION_NEWER,
    VAWT_SCHEMA_VERSION,
    Axis,
    InterfaceType,
    LayerSizing,
    RotorGeometryConfig,
    VawtProjectConfig,
    plane_axes,
)
from vawt.rotor_metrics import RotorMetrics, compute_rotor_metrics


@dataclass(frozen=True)
class ValidationThresholds:
    """Placeholder defaults; configurable per call."""

    min_clearance_cells: float = 1.0  # rotor-to-cylinder clearance, in zone cells
    min_cells_across_diameter: float = 10.0  # zone cells across the rotor diameter
    # Absolute layers: final layer below this fraction of the finest blade cell
    # (V0 E3a: D/5000 next to D/88 cells added no layers; E3d at 0.3 did).
    min_final_layer_fraction: float = 0.1
    # A mesh point closer than this fraction of a cell to a background-cell
    # face counts as on it (V0 E4: snappyHexMesh rejects points on cell edges).
    point_face_tolerance_cells: float = 1e-6


@dataclass(frozen=True)
class VawtValidationResult:
    config: VawtProjectConfig | None
    metrics: RotorMetrics | None
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


# --- configuration parsing ------------------------------------------------------

def parse_config(raw: Any) -> tuple[VawtProjectConfig | None, tuple[Issue, ...]]:
    """Validate raw data; name the two required confirmations explicitly."""
    try:
        return VawtProjectConfig.model_validate(raw), ()
    except ValidationError as exc:
        return None, tuple(_rename(issue) for issue in issues_from_validation_error(exc))


# Error types meaning "no value given" (absent, null, or not one of the options).
_UNSET = frozenset({"missing", "enum", "model_type", "model_attributes_type"})
UNSET_ERROR_TYPES = _UNSET


def _rename(issue: Issue) -> Issue:
    field = str(issue.details.get("field", ""))
    kind = str(issue.details.get("error_type", ""))
    if kind == SCHEMA_VERSION_INVALID:
        return _issue(
            IssueSeverity.BLOCKING, "SCHEMA_VERSION_INVALID", issue.message,
            "Set schema_version to a whole number of 1 or more, or remove it to use "
            f"the current version ({VAWT_SCHEMA_VERSION}).",
            field="schema_version",
        )
    if kind == SCHEMA_VERSION_NEWER:
        return _issue(
            IssueSeverity.BLOCKING, "SCHEMA_VERSION_NEWER", issue.message,
            "Open this configuration with the newer application version that wrote it.",
            field="schema_version",
        )
    if field == "geometry.source_units" and kind in _UNSET:
        return _issue(
            IssueSeverity.BLOCKING, "UNITS_NOT_CHOSEN",
            "The units of the STL file have not been chosen.",
            "Select the units the STL was exported in; they are never assumed.",
            category=IssueCategory.UNITS, field=field,
        )
    if field in ("rotor", "rotor.axis", "rotor.flow_axis") and kind in _UNSET:
        return _issue(
            IssueSeverity.BLOCKING, "ROTOR_AXIS_NOT_CONFIRMED",
            "The rotor axis and the flow axis must both be confirmed.",
            "Confirm the rotor axis (a suggestion is offered from the geometry) and "
            "choose the flow axis.",
            field=field,
        )
    return issue


# --- geometry -----------------------------------------------------------------------

def load_rotor(config: VawtProjectConfig) -> tuple[trimesh.Trimesh | None, tuple[Issue, ...]]:
    """Import the source STL and apply the configured transform in memory."""
    return load_rotor_geometry(config.geometry)


def load_rotor_geometry(
    geometry: RotorGeometryConfig,
) -> tuple[trimesh.Trimesh | None, tuple[Issue, ...]]:
    """load_rotor for the geometry section alone (before the rest is configured)."""
    imported = import_stl(geometry.source_path)
    if imported.mesh is None:
        return None, imported.issues
    transformed = GeometryTransform.from_config(geometry).apply_to_mesh(imported.mesh)
    report = TrimeshGeometryValidator().validate_mesh(
        transformed, geometry.source_path, check_dimensions_in_metres=True
    )
    if has_stopping_issue(report.issues):
        return None, report.issues
    return transformed, report.issues


def validate_vawt(
    raw: Any, thresholds: ValidationThresholds | None = None
) -> VawtValidationResult:
    """Parse the configuration, analyse the rotor, and run every check."""
    config, issues = parse_config(raw)
    if config is None:
        return VawtValidationResult(None, None, issues)
    mesh, geometry_issues = load_rotor(config)
    if mesh is None:
        return VawtValidationResult(config, None, geometry_issues)
    metrics = compute_rotor_metrics(mesh.vertices, config.rotor.axis)
    checks = check_config(config, mesh, metrics, thresholds or ValidationThresholds())
    return VawtValidationResult(config, metrics, (*geometry_issues, *checks))


# --- the checks of section 8 ----------------------------------------------------------

def _cylinder_position(config: VawtProjectConfig, point: np.ndarray) -> tuple[float, float]:
    """(radial distance from the zone axis, axial coordinate) of a point."""
    axis = config.rotor.axis
    u, v = plane_axes(axis)
    zone = config.rotating_zone
    radial = float(np.hypot(point[u.position] - zone.centre_u, point[v.position] - zone.centre_v))
    return radial, float(point[axis.position])


def _in_cylinder(config: VawtProjectConfig, point: np.ndarray) -> bool:
    radial, axial = _cylinder_position(config, point)
    zone = config.rotating_zone
    return radial < zone.diameter / 2.0 and zone.axis_min < axial < zone.axis_max


def _point(vector: Any) -> np.ndarray:
    return np.array([vector.x, vector.y, vector.z], dtype=float)


def _cylinder_box(config: VawtProjectConfig) -> tuple[np.ndarray, np.ndarray]:
    axis = config.rotor.axis
    u, v = plane_axes(axis)
    zone = config.rotating_zone
    radius = zone.diameter / 2.0
    lo, hi = np.zeros(3), np.zeros(3)
    lo[u.position], hi[u.position] = zone.centre_u - radius, zone.centre_u + radius
    lo[v.position], hi[v.position] = zone.centre_v - radius, zone.centre_v + radius
    lo[axis.position], hi[axis.position] = zone.axis_min, zone.axis_max
    return lo, hi


def _layer_total(first_or_final: float, ratio: float, count: int, *, outward: bool) -> float:
    """Total thickness of `count` layers growing by `ratio`.

    outward=True: thickness given for the first (wall) layer.
    outward=False: thickness given for the final (outermost) layer.
    """
    factors = [ratio ** (k if outward else -k) for k in range(count)]
    return first_or_final * float(sum(factors))


def check_config(config: VawtProjectConfig, mesh: trimesh.Trimesh, metrics: RotorMetrics,
                 thresholds: ValidationThresholds) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    zone = config.rotating_zone
    domain = config.domain
    radius = zone.diameter / 2.0
    vertices = np.asarray(mesh.vertices, dtype=float)
    axis: Axis = config.rotor.axis
    u, v = plane_axes(axis)

    # AMI needs an outer mesh to slide against.
    if zone.interface is InterfaceType.AMI and domain is None:
        issues.append(_issue(
            IssueSeverity.BLOCKING, "AMI_REQUIRES_DOMAIN",
            "The AMI interface requires an outer domain.",
            "Enable the outer domain, or use the CELL_ZONE interface.",
        ))

    # Rotor inside the rotating zone (swept radius and axial extent).
    sweep = float(np.max(np.hypot(vertices[:, u.position] - zone.centre_u,
                                  vertices[:, v.position] - zone.centre_v)))
    rotor_lo = float(vertices[:, axis.position].min())
    rotor_hi = float(vertices[:, axis.position].max())
    clearances = {"radial": radius - sweep, "axial_min": rotor_lo - zone.axis_min,
                  "axial_max": zone.axis_max - rotor_hi}
    if min(clearances.values()) <= 0.0:
        issues.append(_issue(
            IssueSeverity.BLOCKING, "ROTOR_OUTSIDE_ZONE",
            "The rotor is not fully inside the rotating-zone cylinder.",
            "Enlarge the cylinder (diameter or axial extent) or check its centre.",
            category=IssueCategory.GEOMETRY,
            explanation="The check uses the rotor's swept radius and axial extent.",
            sweep_radius=sweep, cylinder_radius=radius,
            clearances={k: float(c) for k, c in clearances.items()},
        ))
    else:
        limit = thresholds.min_clearance_cells * zone.cell_size
        if min(clearances.values()) < limit:
            issues.append(_issue(
                IssueSeverity.WARNING, "ZONE_CLEARANCE_SMALL",
                f"Clearance between the rotor and the cylinder ({min(clearances.values()):.4g}"
                f" m) is below {thresholds.min_clearance_cells:g} zone cell(s).",
                "Increase the cylinder size, or reduce the zone cell size.",
                category=IssueCategory.GEOMETRY,
                clearances={k: float(c) for k, c in clearances.items()}, limit=limit,
            ))

    if domain is not None:
        lo, hi = _point(domain.bounds.minimum), _point(domain.bounds.maximum)
        cyl_lo, cyl_hi = _cylinder_box(config)
        if not (np.all(lo < cyl_lo) and np.all(cyl_hi < hi)):
            issues.append(_issue(
                IssueSeverity.BLOCKING, "ZONE_OUTSIDE_DOMAIN",
                "The rotating-zone cylinder is not inside the outer domain.",
                "Enlarge the domain or move the cylinder.",
            ))
        wake = config.refinement.wake
        if wake is not None and not domain.bounds.contains_box(wake.box):
            issues.append(_issue(
                IssueSeverity.ERROR, "WAKE_OUTSIDE_DOMAIN",
                "The wake refinement box is not inside the outer domain.",
                "Shrink the wake box or enlarge the domain.",
            ))
        outer = _point(domain.location_in_mesh)
        if not domain.bounds.strictly_contains(domain.location_in_mesh) or _in_cylinder(
            config, outer
        ):
            issues.append(_issue(
                IssueSeverity.BLOCKING, "OUTER_POINT_INVALID",
                "The outer mesh point must be inside the domain and outside the cylinder.",
                "Move domain.location_in_mesh into the fluid between the domain and the "
                "cylinder.",
            ))

    inner = _point(zone.location_in_mesh)
    if not _in_cylinder(config, inner):
        issues.append(_issue(
            IssueSeverity.BLOCKING, "INNER_POINT_OUTSIDE_ZONE",
            "The rotating-zone mesh point is not inside the cylinder.",
            "Move rotating_zone.location_in_mesh inside the cylinder, outside the rotor.",
        ))

    merged = mesh.copy()
    merged.merge_vertices()
    if merged.is_watertight:
        if contains_points(inner[None, :], merged.vertices, merged.faces)[0]:
            issues.append(_issue(
                IssueSeverity.BLOCKING, "INNER_POINT_IN_ROTOR",
                "The rotating-zone mesh point is inside the rotor solid.",
                "Move rotating_zone.location_in_mesh into the fluid around the rotor.",
                category=IssueCategory.GEOMETRY,
            ))
    else:
        issues.append(_issue(
            IssueSeverity.WARNING, "INNER_POINT_UNCHECKED",
            "The rotor surface is not closed, so it cannot be checked whether the mesh "
            "point is inside the rotor.",
            "Check rotating_zone.location_in_mesh manually against the geometry.",
            category=IssueCategory.GEOMETRY,
        ))

    for body in body_orientations(mesh):
        if body.inside_out:
            issues.append(_issue(
                IssueSeverity.WARNING, "BODY_INSIDE_OUT",
                f"Body {body.index} of the rotor is inside-out (normals point inward).",
                "Flip the normals of this body in CAD and re-export.",
                category=IssueCategory.GEOMETRY, stage=IssueStage.GEOMETRY_VALIDATION,
                body=body.index, faces=body.faces, signed_volume=body.signed_volume,
                bounds_min=list(body.bounds_min), bounds_max=list(body.bounds_max),
            ))

    layers = config.layers
    if layers.enabled:
        if layers.sizing is LayerSizing.ABSOLUTE:
            assert layers.first_layer_thickness is not None
            assert layers.min_thickness_m is not None
            finest = zone_cell_size(config) / 2 ** config.refinement.blade_max_level
            final_layer = layers.first_layer_thickness * layers.expansion_ratio ** (
                layers.count - 1)
            if final_layer < thresholds.min_final_layer_fraction * finest:
                issues.append(_issue(
                    IssueSeverity.WARNING, "LAYERS_TOO_THIN_FOR_CELLS",
                    f"The outermost layer ({final_layer:.4g} m) is below "
                    f"{thresholds.min_final_layer_fraction:g} of the finest blade cell "
                    f"({finest:.4g} m); snappyHexMesh is likely to add no layers.",
                    "Increase first_layer_thickness, the expansion ratio or the layer "
                    "count, or refine the blade cells.",
                    explanation="The jump from the last layer to the cell next to it "
                    "fails snappyHexMesh's quality checks, and the layers are removed.",
                    final_layer_thickness=final_layer, finest_blade_cell=finest,
                ))
            if layers.first_layer_thickness > finest:
                issues.append(_issue(
                    IssueSeverity.ERROR, "ABSOLUTE_LAYER_TOO_THICK",
                    f"The first layer ({layers.first_layer_thickness:g} m) is thicker than "
                    f"the finest blade cell ({finest:g} m).",
                    "Reduce first_layer_thickness or the blade refinement.",
                    finest_blade_cell=finest,
                ))
            total = _layer_total(layers.first_layer_thickness, layers.expansion_ratio,
                                 layers.count, outward=True)
            limited, minimum = total, layers.min_thickness_m
        else:
            limited = _layer_total(layers.final_layer_thickness, layers.expansion_ratio,
                                   layers.count, outward=False)
            minimum = layers.min_thickness
        if minimum > limited:
            issues.append(_issue(
                IssueSeverity.ERROR, "LAYER_MIN_THICKNESS_TOO_LARGE",
                f"The minimum layer thickness ({minimum:g}) exceeds the total layer "
                f"thickness it limits ({limited:.4g}); layers would never be added.",
                "Lower the minimum thickness, or add/thicken layers.",
                explanation="snappyHexMesh's minThickness limits the total thickness of "
                "all layers.",
                total_layer_thickness=limited, sizing=layers.sizing.value,
            ))

    if metrics.diameter is not None:
        across = metrics.diameter / zone.cell_size
        if across < thresholds.min_cells_across_diameter:
            issues.append(_issue(
                IssueSeverity.WARNING, "TOO_FEW_ZONE_CELLS",
                f"Only {across:.1f} zone cells across the rotor diameter "
                f"(threshold {thresholds.min_cells_across_diameter:g}).",
                "Reduce the rotating-zone cell size.",
                cells_across_diameter=across,
            ))

    issues.extend(_reserved_name_issues(config))
    issues.extend(_point_on_face_issues(config, thresholds))
    return tuple(issues)


def _reserved_name_issues(config: VawtProjectConfig) -> list[Issue]:
    names = {"geometry.patch_name": config.geometry.patch_name}
    if config.domain is not None:
        patches = config.domain.patches
        names.update({f"domain.patches.{field}": getattr(patches, field)
                      for field in type(patches).model_fields})
    return [
        _issue(
            IssueSeverity.BLOCKING, "RESERVED_PATCH_NAME",
            f"'{name}' ({field}) is a name the generated mesh uses itself.",
            f"Choose another name; reserved: {', '.join(sorted(RESERVED_NAMES))}.",
            field=field, name=name,
        )
        for field, name in names.items() if name in RESERVED_NAMES
    ]


def _faces_hit(grid: Grid, point: np.ndarray, tolerance_cells: float) -> list[str]:
    """Global axes along which the point lies on a background-cell face plane."""
    hit = []
    for index, axis in enumerate("xyz"):
        spacing = grid.spacing(index)
        offset = (point[index] - grid.minimum[index]) / spacing
        if abs(offset - round(offset)) < tolerance_cells:
            hit.append(axis)
    return hit


def _point_on_face_issues(config: VawtProjectConfig,
                          thresholds: ValidationThresholds) -> list[Issue]:
    """Mesh points on background-cell faces or edges (V0 E4)."""
    layout = case_layout(config)
    grids = background_grids(config)
    checks = []
    if ROTOR in layout.sub_cases:  # the rotor mesh uses the zone's point
        checks.append(("rotating_zone.location_in_mesh", config.rotating_zone.location_in_mesh,
                       grids[ROTOR]))
    if config.domain is not None:  # the outer or single mesh uses the domain's point
        grid = grids[OUTER] if OUTER in layout.sub_cases else grids[MERGED]
        checks.append(("domain.location_in_mesh", config.domain.location_in_mesh, grid))
    issues = []
    for field, vector, grid in checks:
        axes = _faces_hit(grid, _point(vector), thresholds.point_face_tolerance_cells)
        if axes:
            where = "edge" if len(axes) > 1 else "face"
            issues.append(_issue(
                IssueSeverity.ERROR, "MESH_POINT_ON_CELL_FACE",
                f"{field} lies on a background-cell {where} (planes normal to "
                f"{', '.join(axes)}); snappyHexMesh may not find the region.",
                f"Move {field} by a fraction of a cell (for example a quarter) along "
                f"{', '.join(axes)}.",
                explanation="snappyHexMesh stops with 'is not inside the mesh or on a "
                "face or edge' for such points.",
                field=field, axes=axes,
                cell_size={a: grid.spacing("xyz".index(a)) for a in axes},
            ))
    return issues
