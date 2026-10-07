"""Geometry measurements shared by workflows. Pure numpy/trimesh; never modifies input."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

_CHUNK = 200_000  # triangles per batch: bounds memory for large surfaces


def winding_numbers(points: np.ndarray, vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Generalized winding number of each point w.r.t. a triangle surface.

    About +1 inside a closed outward-wound surface, -1 inside an inward-wound
    one, 0 outside. Uses the solid angle of each triangle (Van Oosterom and
    Strackee), so it needs no spatial index or extra dependency.
    """
    points = np.atleast_2d(np.asarray(points, dtype=np.float64))
    triangles = np.asarray(vertices, dtype=np.float64)[np.asarray(faces)]
    totals = np.zeros(len(points))
    for start in range(0, len(triangles), _CHUNK):
        batch = triangles[start:start + _CHUNK]
        for index, point in enumerate(points):
            a, b, c = (batch[:, k] - point for k in range(3))
            la, lb, lc = (np.linalg.norm(x, axis=1) for x in (a, b, c))
            det = np.einsum("ij,ij->i", a, np.cross(b, c))
            denominator = (la * lb * lc + np.einsum("ij,ij->i", a, b) * lc
                           + np.einsum("ij,ij->i", b, c) * la
                           + np.einsum("ij,ij->i", c, a) * lb)
            totals[index] += np.sum(2.0 * np.arctan2(det, denominator))
    return totals / (4.0 * np.pi)


def contains_points(points: np.ndarray, vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Whether each point lies inside the closed surface (either winding)."""
    return np.abs(winding_numbers(points, vertices, faces)) > 0.5


@dataclass(frozen=True)
class BodyOrientation:
    index: int
    faces: int
    closed: bool
    signed_volume: float | None  # None when the body is not closed
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]

    @property
    def inside_out(self) -> bool:
        return self.signed_volume is not None and self.signed_volume < 0.0


def body_orientations(mesh: trimesh.Trimesh) -> tuple[BodyOrientation, ...]:
    """Orientation of each connected body, in a deterministic order.

    A total signed volume can hide one inside-out body among correct ones;
    this reports each closed body separately.
    """
    merged = mesh.copy()
    merged.merge_vertices()
    # repair=False: trimesh otherwise fills holes in each body, which would
    # report an open body as closed.
    bodies = merged.split(only_watertight=False, repair=False)
    ordered = sorted(
        bodies, key=lambda b: (tuple(np.round(b.bounds[0], 12)), len(b.faces))
    )
    results = []
    for index, body in enumerate(ordered):
        closed = bool(body.is_watertight)
        bounds = np.asarray(body.bounds, dtype=float)
        results.append(BodyOrientation(
            index=index,
            faces=len(body.faces),
            closed=closed,
            signed_volume=float(body.volume) if closed else None,
            bounds_min=(float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2])),
            bounds_max=(float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2])),
        ))
    return tuple(results)
