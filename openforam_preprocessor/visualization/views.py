"""Plotly views of artifacts. Display only: nothing here feeds computation.

Views answer "is the geometry/mesh what I intended?". They do not say
anything about CFD accuracy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import plotly.graph_objects as go

from core.config.models import ProjectConfig
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from geometry.importer import import_stl
from visualization.foam_reader import FoamReadError, read_poly_mesh, read_set

MAX_DISPLAY_FACES = 150_000
PATCH_COLORS = ("#4C78A8", "#72B7B2", "#54A24B", "#EECA3B", "#B279A2", "#9D755D",
                "#BAB0AC", "#F58518")
PROBLEM_COLOR = "#E45756"


@dataclass(frozen=True)
class View:
    figure: go.Figure | None
    notes: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = field(default_factory=tuple)


def _issue(code: str, message: str, action: str, category: IssueCategory,
           severity: IssueSeverity = IssueSeverity.WARNING) -> Issue:
    return Issue(category=category, severity=severity, stage=IssueStage.VISUALIZATION,
                 code=code, message=message, suggested_action=action)


def sample_indices(count: int, cap: int) -> np.ndarray:
    """Deterministic evenly spaced subset (all indices when count <= cap)."""
    if count <= cap:
        return np.arange(count)
    return np.unique(np.linspace(0, count - 1, cap).astype(np.int64))


def _scene_layout(figure: go.Figure, title: str) -> go.Figure:
    figure.update_layout(
        title=title,
        scene={
            "aspectmode": "data",
            "xaxis_title": "x (m)", "yaxis_title": "y (m)", "zaxis_title": "z (m)",
        },
        legend={"itemsizing": "constant"},
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
    )
    return figure


def _surface(vertices: np.ndarray, triangles: np.ndarray, name: str, color: str,
             opacity: float = 1.0) -> go.Mesh3d:
    return go.Mesh3d(
        x=vertices[:, 0], y=vertices[:, 1], z=vertices[:, 2],
        i=triangles[:, 0], j=triangles[:, 1], k=triangles[:, 2],
        name=name, color=color, opacity=opacity, showlegend=True, flatshading=True,
    )


def box_edges(minimum: Sequence[float], maximum: Sequence[float]) -> go.Scatter3d:
    (x0, y0, z0), (x1, y1, z1) = minimum, maximum
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    xs: list[float | None] = []
    ys: list[float | None] = []
    zs: list[float | None] = []
    for a, b in edges:
        for point in (corners[a], corners[b]):
            xs.append(point[0])
            ys.append(point[1])
            zs.append(point[2])
        xs.append(None)
        ys.append(None)
        zs.append(None)
    return go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", name="Domain (background mesh box)",
                        line={"color": "#333333", "width": 3})


def _sampled_triangles(mesh: Any, cap: int) -> tuple[np.ndarray, np.ndarray, int]:
    faces = np.asarray(mesh.faces)
    keep = sample_indices(len(faces), cap)
    return np.asarray(mesh.vertices, dtype=float), faces[keep], len(faces)


def geometry_view(root: Path, config: ProjectConfig,
                  max_faces: int = MAX_DISPLAY_FACES) -> View:
    """Transformed artifact vs. unit-converted original, with domain and locationInMesh."""
    notes: list[str] = []
    issues: list[Issue] = []
    figure = go.Figure()
    artifact = root / "constant" / "triSurface" / f"{config.geometry.patch_name}.stl"

    source = import_stl(config.geometry.source_path)
    if source.mesh is not None and len(source.mesh.faces):
        vertices, triangles, total = _sampled_triangles(source.mesh, max_faces)
        units = config.geometry.source_units
        figure.add_trace(_surface(
            vertices * units.to_metres, triangles,
            f"Original (only converted {units.value} → m)", "#BAB0AC", opacity=0.35,
        ))
        if total > len(triangles):
            notes.append(f"Original: showing {len(triangles):,} of {total:,} triangles.")
    else:
        issues.append(_issue("VIEW_SOURCE_UNAVAILABLE", "The source STL could not be shown.",
                             "Check the geometry source path.", IssueCategory.INPUT))

    transformed = import_stl(artifact)
    if transformed.mesh is not None and len(transformed.mesh.faces):
        vertices, triangles, total = _sampled_triangles(transformed.mesh, max_faces)
        figure.add_trace(_surface(vertices, triangles, "Transformed (meshed geometry)",
                                  PATCH_COLORS[0]))
        if total > len(triangles):
            notes.append(f"Transformed: showing {len(triangles):,} of {total:,} triangles.")
    else:
        issues.append(_issue("VIEW_ARTIFACT_UNAVAILABLE",
                             "The transformed geometry has not been generated yet.",
                             "Run 'Prepare case' first.", IssueCategory.GEOMETRY,
                             IssueSeverity.INFO))

    domain = config.mesh.background.domain
    figure.add_trace(box_edges(
        (domain.minimum.x, domain.minimum.y, domain.minimum.z),
        (domain.maximum.x, domain.maximum.y, domain.maximum.z),
    ))
    location = config.mesh.location_in_mesh
    figure.add_trace(go.Scatter3d(
        x=[location.x], y=[location.y], z=[location.z], mode="markers",
        name="locationInMesh", marker={"size": 6, "color": "#F58518", "symbol": "diamond"},
    ))
    return View(_scene_layout(figure, "Geometry and domain"), tuple(notes), tuple(issues))


def _fan(face: np.ndarray) -> list[tuple[int, int, int]]:
    return [(int(face[0]), int(face[i]), int(face[i + 1])) for i in range(1, len(face) - 1)]


def _face_center(points: np.ndarray, face: np.ndarray) -> np.ndarray:
    return points[face].mean(axis=0)


def mesh_view(root: Path, max_faces: int = MAX_DISPLAY_FACES) -> View:
    """Boundary patches of constant/polyMesh plus checkMesh problem sets."""
    poly_mesh = root / "constant" / "polyMesh"
    try:
        mesh = read_poly_mesh(poly_mesh)
    except FoamReadError as exc:
        return View(None, (), (_issue(
            "VIEW_MESH_UNAVAILABLE", f"The mesh cannot be displayed: {exc}",
            "Generate the mesh first; only uncompressed ASCII meshes are supported.",
            IssueCategory.MESHING, IssueSeverity.INFO,
        ),))

    notes: list[str] = []
    issues: list[Issue] = []
    figure = go.Figure()
    total_faces = sum(patch.n_faces for patch in mesh.patches)
    stride = max(1, math.ceil(total_faces / max_faces))
    if stride > 1:
        notes.append(f"Showing 1 in every {stride} boundary faces ({total_faces:,} in total).")

    for index, patch in enumerate(mesh.patches):
        triangles = [
            triangle
            for face_index in range(patch.start_face, patch.start_face + patch.n_faces, stride)
            for triangle in _fan(mesh.faces[face_index])
        ]
        if not triangles:
            continue
        figure.add_trace(_surface(
            mesh.points, np.asarray(triangles), f"{patch.name} ({patch.patch_type}, "
            f"{patch.n_faces:,} faces)", PATCH_COLORS[index % len(PATCH_COLORS)],
            opacity=0.6,
        ))

    sets_dir = poly_mesh / "sets"
    if sets_dir.is_dir():
        for path in sorted(sets_dir.iterdir()):
            try:
                set_class, labels = read_set(path)
            except FoamReadError as exc:
                issues.append(_issue("VIEW_SET_UNREADABLE", f"Set {path.name}: {exc}",
                                     "Inspect the set file.", IssueCategory.MESH_QUALITY,
                                     IssueSeverity.INFO))
                continue
            centers = _set_locations(mesh, set_class, labels)
            if centers is None or not len(centers):
                continue
            figure.add_trace(go.Scatter3d(
                x=centers[:, 0], y=centers[:, 1], z=centers[:, 2], mode="markers",
                name=f"checkMesh: {path.name} ({len(labels):,})",
                marker={"size": 4, "color": PROBLEM_COLOR},
            ))
    return View(_scene_layout(figure, "Mesh boundary and checkMesh problem locations"),
                tuple(notes), tuple(issues))


def _set_locations(mesh: Any, set_class: str, labels: np.ndarray) -> np.ndarray | None:
    if set_class == "faceSet":
        valid = [label for label in labels if 0 <= label < len(mesh.faces)]
        return np.array([_face_center(mesh.points, mesh.faces[i]) for i in valid])
    if set_class == "pointSet":
        return mesh.points[labels[(labels >= 0) & (labels < len(mesh.points))]]
    if set_class == "cellSet":
        # Approximate cell centres from the faces each cell owns.
        wanted = set(int(label) for label in labels)
        sums: dict[int, np.ndarray] = {}
        counts: dict[int, int] = {}
        for face_index, cell in enumerate(mesh.owner):
            cell = int(cell)
            if cell in wanted:
                center = _face_center(mesh.points, mesh.faces[face_index])
                sums[cell] = sums.get(cell, 0) + center
                counts[cell] = counts.get(cell, 0) + 1
        return np.array([sums[c] / counts[c] for c in sorted(sums)])
    return None


def quality_chart(report: dict[str, Any]) -> View:
    """Measured checkMesh values against the configured acceptance limits."""
    metrics = report.get("metrics") or {}
    limits = report.get("limits") or {}
    candidates = [
        ("Max non-orthogonality (°)", metrics.get("max_non_orthogonality"),
         limits.get("max_non_orthogonality")),
        ("Max skewness", metrics.get("max_skewness"), limits.get("max_internal_skewness")),
    ]
    rows: list[tuple[str, float, float]] = [
        (label, float(measured), float(limit))
        for label, measured, limit in candidates
        if measured is not None and limit is not None
    ]
    if not rows:
        return View(None, ("No comparable quality metrics in the report.",))
    labels = [row[0] for row in rows]
    figure = go.Figure([
        go.Bar(name="Measured", x=labels, y=[row[1] for row in rows],
               marker_color=[PROBLEM_COLOR if row[1] > row[2] else PATCH_COLORS[2]
                             for row in rows]),
        go.Bar(name="Configured limit", x=labels, y=[row[2] for row in rows],
               marker_color="#BAB0AC"),
    ])
    figure.update_layout(barmode="group", title="Mesh quality vs. acceptance limits",
                         margin={"l": 0, "r": 0, "t": 40, "b": 0})
    return View(figure, ("Within limits does not imply CFD accuracy.",))
