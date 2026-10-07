from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from geometry.importer import import_stl


@dataclass(frozen=True)
class GeometryReport:
    """Geometry metrics are None when the surface could not be read or is empty."""

    source: str
    issues: tuple[Issue, ...]
    vertices: int | None = None
    faces: int | None = None
    components: int | None = None
    is_watertight: bool | None = None
    is_winding_consistent: bool | None = None
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None
    extents: tuple[float, float, float] | None = None
    surface_area: float | None = None
    volume: float | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [issue.as_dict() for issue in self.issues]
        return data


@dataclass(frozen=True)
class DimensionLimits:
    """Heuristic plausibility range for the largest extent, in metres.

    Values outside the range usually indicate wrong source units. These are
    warnings, not engineering limits, and are configurable per validator.
    """

    min_largest_extent_m: float = 1e-4
    max_largest_extent_m: float = 1e4


def _geometry_issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    source: Path,
    *,
    explanation: str = "",
    suggested_action: str = "",
    details: dict[str, Any] | None = None,
) -> Issue:
    return Issue(
        category=IssueCategory.GEOMETRY,
        severity=severity,
        stage=IssueStage.GEOMETRY_VALIDATION,
        code=code,
        message=message,
        explanation=explanation,
        suggested_action=suggested_action,
        artifact_reference=str(source),
        details=details or {},
    )


class TrimeshGeometryValidator:
    """Fast, deterministic STL validation. Never modifies the geometry file."""

    def __init__(self, dimension_limits: DimensionLimits | None = None) -> None:
        self.dimension_limits = dimension_limits or DimensionLimits()

    def validate(self, source: Path, *, check_dimensions_in_metres: bool = False) -> GeometryReport:
        imported = import_stl(source)
        if imported.mesh is None:
            return GeometryReport(source=str(source), issues=imported.issues)
        return self.validate_mesh(
            imported.mesh, source, check_dimensions_in_metres=check_dimensions_in_metres
        )

    def validate_mesh(
        self,
        loaded: trimesh.Trimesh,
        source: Path,
        *,
        check_dimensions_in_metres: bool = False,
    ) -> GeometryReport:
        if not np.isfinite(np.asarray(loaded.vertices, dtype=float)).all():
            return GeometryReport(
                source=str(source),
                faces=len(loaded.faces),
                issues=(_geometry_issue(
                    IssueSeverity.ERROR,
                    "NON_FINITE_COORDINATES",
                    "Geometry contains NaN or infinite vertex coordinates.",
                    source,
                    explanation="Non-finite coordinates make every downstream operation invalid.",
                    suggested_action="Re-export the geometry from CAD.",
                ),),
            )

        # STL stores every triangle's vertices independently. Merge coincident
        # vertices on an in-memory copy so topology checks (watertightness,
        # winding, components) see shared edges. The file is unchanged.
        mesh = loaded.copy()
        mesh.merge_vertices()
        if len(mesh.faces) == 0:
            return GeometryReport(
                source=str(source),
                vertices=len(mesh.vertices),
                faces=0,
                issues=(_geometry_issue(
                    IssueSeverity.ERROR,
                    "NO_FACES",
                    "Geometry has no triangular faces.",
                    source,
                    explanation="An empty surface cannot be meshed.",
                    suggested_action="Check that the correct STL was exported and selected.",
                ),),
            )

        issues: list[Issue] = []
        extents = np.asarray(mesh.extents, dtype=float)
        component_count = len(mesh.split(only_watertight=False))
        is_watertight = bool(mesh.is_watertight)
        is_winding_consistent = bool(mesh.is_winding_consistent)
        volume = float(mesh.volume) if is_watertight else None

        if np.any(extents <= 0.0):
            issues.append(_geometry_issue(
                IssueSeverity.ERROR,
                "DEGENERATE_EXTENT",
                "One or more geometry extents are zero or negative.",
                source,
                explanation="A flat or collapsed surface cannot bound a meshable region.",
                suggested_action="Check the exported geometry and its units.",
                details={"extents": extents.tolist()},
            ))

        faces = np.asarray(mesh.faces)
        collapsed = (
            (faces[:, 0] == faces[:, 1])
            | (faces[:, 1] == faces[:, 2])
            | (faces[:, 0] == faces[:, 2])
        )
        degenerate = int(np.count_nonzero(collapsed | (mesh.area_faces <= 0.0)))
        if degenerate:
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "DEGENERATE_FACES",
                f"{degenerate} zero-area or collapsed triangle(s) found.",
                source,
                explanation="Degenerate triangles can disturb snapping and feature detection.",
                suggested_action="Clean the surface in CAD and re-export.",
                details={"count": degenerate},
            ))

        duplicates = len(faces) - len(np.unique(np.sort(faces, axis=1), axis=0))
        if duplicates:
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "DUPLICATE_FACES",
                f"{duplicates} duplicate triangle(s) found.",
                source,
                explanation="Duplicate triangles create non-manifold edges.",
                suggested_action="Remove duplicate faces in CAD and re-export.",
                details={"count": duplicates},
            ))

        if not is_watertight:
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "NOT_WATERTIGHT",
                "Surface is not watertight; external-flow meshing may still be possible, "
                "but enclosed-volume workflows require review.",
                source,
                suggested_action="Inspect open edges and repair the surface in CAD if required.",
            ))

        if not is_winding_consistent:
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "INCONSISTENT_WINDING",
                "Triangle winding is inconsistent.",
                source,
                suggested_action="Re-export or repair the STL before meshing.",
            ))
        elif volume is not None and volume < 0.0:
            # Orientation is reported only; the geometry is never flipped.
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "INWARD_NORMALS",
                "Face normals point into the enclosed volume (negative signed volume).",
                source,
                explanation="The surface is closed and consistently wound, but inside-out.",
                suggested_action="Flip the surface normals in CAD and re-export.",
                details={"signed_volume": volume},
            ))

        if component_count > 1:
            issues.append(_geometry_issue(
                IssueSeverity.INFO,
                "MULTIPLE_COMPONENTS",
                "Geometry contains multiple disconnected components.",
                source,
                suggested_action="Verify that the disconnected components are intentional.",
                details={"components": component_count},
            ))

        if check_dimensions_in_metres:
            largest = float(extents.max())
            limits = self.dimension_limits
            if not limits.min_largest_extent_m <= largest <= limits.max_largest_extent_m:
                issues.append(Issue(
                    category=IssueCategory.UNITS,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.GEOMETRY_VALIDATION,
                    code="SUSPICIOUS_DIMENSIONS",
                    message=f"Largest geometry extent is {largest:g} m, outside the expected "
                    f"range {limits.min_largest_extent_m:g}-{limits.max_largest_extent_m:g} m.",
                    explanation="An implausible size usually means the source units are wrong.",
                    suggested_action="Confirm geometry.source_units and geometry.scale.",
                    artifact_reference=str(source),
                    details={"largest_extent_m": largest},
                ))

        bounds = np.asarray(mesh.bounds, dtype=float)
        return GeometryReport(
            source=str(source),
            vertices=len(mesh.vertices),
            faces=len(mesh.faces),
            components=component_count,
            is_watertight=is_watertight,
            is_winding_consistent=is_winding_consistent,
            bounds_min=(float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2])),
            bounds_max=(float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2])),
            extents=(float(extents[0]), float(extents[1]), float(extents[2])),
            surface_area=float(mesh.area),
            volume=volume,
            issues=tuple(issues),
        )
