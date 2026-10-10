"""Points on a surface at a bounded spacing (shared by the checks and presets)."""

from __future__ import annotations

import math

import numpy as np
import trimesh


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
