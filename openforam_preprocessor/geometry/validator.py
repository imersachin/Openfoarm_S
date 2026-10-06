from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import trimesh


@dataclass(frozen=True)
class GeometryIssue:
    severity: str  # info | warning | error
    code: str
    message: str


@dataclass(frozen=True)
class GeometryReport:
    source: str
    vertices: int
    faces: int
    components: int
    is_watertight: bool
    is_winding_consistent: bool
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    extents: tuple[float, float, float]
    surface_area: float
    volume: float | None
    issues: tuple[GeometryIssue, ...]

    def as_dict(self) -> dict:
        data = asdict(self)
        data["issues"] = [asdict(issue) for issue in self.issues]
        return data


class TrimeshGeometryValidator:
    """Fast, deterministic STL preflight validation."""

    def validate(self, source: Path) -> GeometryReport:
        loaded = trimesh.load_mesh(source, process=False)

        if isinstance(loaded, trimesh.Scene):
            mesh = trimesh.util.concatenate(tuple(loaded.geometry.values()))
        else:
            mesh = loaded

        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError(f"Unsupported geometry object read from {source}")

        issues: list[GeometryIssue] = []
        extents = np.asarray(mesh.extents, dtype=float)

        if len(mesh.faces) == 0:
            issues.append(GeometryIssue("error", "NO_FACES", "Geometry has no triangular faces."))

        if not mesh.is_watertight:
            issues.append(GeometryIssue(
                "warning",
                "NOT_WATERTIGHT",
                "Surface is not watertight; external-flow meshing may still be possible, "
                "but enclosed-volume workflows require review.",
            ))

        if not mesh.is_winding_consistent:
            issues.append(GeometryIssue(
                "warning",
                "INCONSISTENT_WINDING",
                "Triangle winding is inconsistent. Re-export or repair the STL before meshing.",
            ))

        if np.any(extents <= 0.0):
            issues.append(GeometryIssue(
                "error",
                "DEGENERATE_EXTENT",
                "One or more geometry extents are zero or negative.",
            ))

        if len(mesh.split(only_watertight=False)) > 1:
            issues.append(GeometryIssue(
                "info",
                "MULTIPLE_COMPONENTS",
                "Geometry contains multiple disconnected components; verify that this is intentional.",
            ))

        bounds = np.asarray(mesh.bounds, dtype=float)
        return GeometryReport(
            source=str(source),
            vertices=len(mesh.vertices),
            faces=len(mesh.faces),
            components=len(mesh.split(only_watertight=False)),
            is_watertight=bool(mesh.is_watertight),
            is_winding_consistent=bool(mesh.is_winding_consistent),
            bounds_min=tuple(bounds[0]),
            bounds_max=tuple(bounds[1]),
            extents=tuple(extents),
            surface_area=float(mesh.area),
            volume=float(mesh.volume) if mesh.is_watertight else None,
            issues=tuple(issues),
        )
