from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage


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


def _geometry_issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    source: Path,
    *,
    category: IssueCategory = IssueCategory.GEOMETRY,
    stage: IssueStage = IssueStage.GEOMETRY_VALIDATION,
    explanation: str = "",
    suggested_action: str = "",
    details: dict[str, Any] | None = None,
) -> Issue:
    return Issue(
        category=category,
        severity=severity,
        stage=stage,
        code=code,
        message=message,
        explanation=explanation,
        suggested_action=suggested_action,
        artifact_reference=str(source),
        details=details or {},
    )


class TrimeshGeometryValidator:
    """Fast, deterministic STL preflight validation."""

    def validate(self, source: Path) -> GeometryReport:
        if not source.is_file():
            return GeometryReport(
                source=str(source),
                issues=(_geometry_issue(
                    IssueSeverity.ERROR,
                    "GEOMETRY_SOURCE_MISSING",
                    f"Geometry file not found: {source}",
                    source,
                    category=IssueCategory.INPUT,
                    stage=IssueStage.GEOMETRY_IMPORT,
                    explanation="The configured STL path does not point to an existing file.",
                    suggested_action="Check the geometry source path in the project settings.",
                ),),
            )

        try:
            loaded = trimesh.load_mesh(source, process=False)
            if isinstance(loaded, trimesh.Scene):
                loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
        except Exception as exc:  # trimesh raises many unrelated types for bad input
            return GeometryReport(
                source=str(source),
                issues=(_geometry_issue(
                    IssueSeverity.ERROR,
                    "GEOMETRY_UNREADABLE",
                    f"Geometry file could not be parsed as STL: {source}",
                    source,
                    category=IssueCategory.INPUT,
                    stage=IssueStage.GEOMETRY_IMPORT,
                    explanation="The file is corrupt, truncated, or not a valid STL surface.",
                    suggested_action="Re-export the geometry as ASCII or binary STL.",
                    details={"exception_type": type(exc).__name__, "exception": str(exc)},
                ),),
            )

        if not isinstance(loaded, trimesh.Trimesh):
            return GeometryReport(
                source=str(source),
                issues=(_geometry_issue(
                    IssueSeverity.ERROR,
                    "GEOMETRY_UNSUPPORTED_OBJECT",
                    f"Unsupported geometry object read from {source}",
                    source,
                    category=IssueCategory.INPUT,
                    stage=IssueStage.GEOMETRY_IMPORT,
                    explanation="The file did not contain a triangulated surface.",
                    suggested_action="Provide a triangulated STL surface.",
                    details={"object_type": type(loaded).__name__},
                ),),
            )

        # STL stores every triangle's vertices independently. Merge coincident
        # vertices on this in-memory copy so topology checks (watertightness,
        # winding, components) see shared edges. The artifact file is unchanged.
        mesh = loaded
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

        if not mesh.is_watertight:
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "NOT_WATERTIGHT",
                "Surface is not watertight; external-flow meshing may still be possible, "
                "but enclosed-volume workflows require review.",
                source,
                suggested_action="Inspect open edges and repair the surface in CAD if required.",
            ))

        if not mesh.is_winding_consistent:
            issues.append(_geometry_issue(
                IssueSeverity.WARNING,
                "INCONSISTENT_WINDING",
                "Triangle winding is inconsistent.",
                source,
                suggested_action="Re-export or repair the STL before meshing.",
            ))

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

        if component_count > 1:
            issues.append(_geometry_issue(
                IssueSeverity.INFO,
                "MULTIPLE_COMPONENTS",
                "Geometry contains multiple disconnected components.",
                source,
                suggested_action="Verify that the disconnected components are intentional.",
                details={"components": component_count},
            ))

        bounds = np.asarray(mesh.bounds, dtype=float)
        return GeometryReport(
            source=str(source),
            vertices=len(mesh.vertices),
            faces=len(mesh.faces),
            components=component_count,
            is_watertight=bool(mesh.is_watertight),
            is_winding_consistent=bool(mesh.is_winding_consistent),
            bounds_min=(float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2])),
            bounds_max=(float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2])),
            extents=(float(extents[0]), float(extents[1]), float(extents[2])),
            surface_area=float(mesh.area),
            volume=float(mesh.volume) if mesh.is_watertight else None,
            issues=tuple(issues),
        )
