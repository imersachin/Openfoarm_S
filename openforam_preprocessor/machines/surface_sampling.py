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


def face_points(mesh: trimesh.Trimesh, spacing: float,
                max_points: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Points on every face, corners included, at most about `spacing` apart
    within the face, each with its face index: every point of a face lies
    within the returned spacing of one of that face's points."""
    vertices = np.asarray(mesh.vertices, dtype=float)
    triangles = vertices[np.asarray(mesh.faces)]
    if len(triangles) == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64), spacing
    edges = np.linalg.norm(triangles - np.roll(triangles, 1, axis=1), axis=2).max(axis=1)
    divisions = np.maximum(1, np.ceil(edges / spacing)).astype(np.int64)
    total = int(((divisions + 1) * (divisions + 2) // 2).sum())
    if total > max_points:
        factor = math.sqrt(total / max_points)
        spacing *= factor
        divisions = np.maximum(1, np.ceil(divisions / factor)).astype(np.int64)
    # Sub-triangle edges are at most the longest edge / divisions.
    used = float((edges / divisions).max())
    points, faces = [], []
    for n in np.unique(divisions):
        a, b = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        keep = a + b <= n
        weights = np.column_stack([a[keep], b[keep], n - a[keep] - b[keep]]) / n
        chosen = np.flatnonzero(divisions == n)
        points.append(np.einsum("wk,mkd->mwd", weights, triangles[chosen]).reshape(-1, 3))
        faces.append(np.repeat(chosen, len(weights)))
    return np.vstack(points), np.concatenate(faces), used
