"""Joints between imported surfaces: coincidence and proposals (G5; K1, K2).

A joint is two coincident regions of separately meshed imported surfaces
(domain parts or an imported rotating zone). Each becomes one side of a
cyclicAMI pair named after the regions (K1), so the two regions must cover
the same surface: about the same area, and every point of one close to the
other (K2). The limits are provisional, unverified on real CAD.

Each region's points (every vertex, plus points spread by area about half a
cell apart) are measured to the other region's surface exactly:
point-to-triangle distances, the candidate triangles found with a KD-tree
over the centres of the surface split into triangles of bounded size. No
spatial-index dependency; the error is only what the query points can miss
between them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from machines.domains import Region, SurfaceInfo
from machines.surface_sampling import face_points

# Candidate triangles are found among points sampled on each triangle about
# sqrt(area / CANDIDATE_POINTS) apart: it bounds the work, not the accuracy.
CANDIDATE_POINTS = 50_000
_CHUNK = 5_000  # query points per batch of candidate pairs


@dataclass(frozen=True)
class JointLimits:
    """K2 (provisional; unverified on real CAD)."""

    # |area1 - area2| / the larger area.
    max_area_difference: float = 0.01
    # The largest distance from a point of one region to the other, in cells of
    # the finer of the two surfaces.
    max_distance_cells: float = 0.5
    # Query points per region are at most this many cells apart.
    sample_spacing_cells: float = 0.5
    # Per region; beyond it the query points are spaced further apart.
    max_sample_points: int = 200_000


@dataclass(frozen=True)
class JointMeasure:
    first_area: float
    second_area: float
    area_difference: float  # relative to the larger area
    max_distance: float  # m, both ways
    spacing: float  # m, of the query points (the distance to the surface is exact)

    def as_details(self) -> dict[str, float]:
        return {"first_area": self.first_area, "second_area": self.second_area,
                "area_difference": self.area_difference, "max_distance": self.max_distance,
                "sample_spacing": self.spacing}


def _exact(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    closest = trimesh.triangles.closest_point(triangles, points)
    return np.asarray(np.linalg.norm(closest - points, axis=1))


def distance_to_surface(points: np.ndarray, mesh: trimesh.Trimesh) -> np.ndarray:
    """Exact distance of every point to the surface (its original triangles)."""
    triangles = np.asarray(mesh.vertices, dtype=float)[np.asarray(mesh.faces)]
    if len(points) == 0 or len(triangles) == 0:
        return np.zeros(len(points))
    area = float(mesh.area)
    spacing = np.sqrt(area / CANDIDATE_POINTS) if area > 0.0 else 1.0
    samples, owner, used = face_points(mesh, spacing, 4 * CANDIDATE_POINTS)
    tree = cKDTree(samples)
    _, nearest = tree.query(points)
    best = _exact(points, triangles[owner[nearest]])  # an upper bound per point
    # A triangle closer than `best` has one of its points within best + used.
    for start in range(0, len(points), _CHUNK):
        rows = np.arange(start, min(start + _CHUNK, len(points)))
        found = tree.query_ball_point(points[rows], best[rows] + used)
        counts = np.array([len(f) for f in found])
        point_index = np.repeat(rows, counts)
        face_index = owner[np.concatenate([np.asarray(f, dtype=np.int64) for f in found])]
        pairs = np.unique(np.column_stack([point_index, face_index]), axis=0)
        np.minimum.at(best, pairs[:, 0], _exact(points[pairs[:, 0]], triangles[pairs[:, 1]]))
    return best


def _one_way(points: np.ndarray, target: trimesh.Trimesh) -> float:
    return float(distance_to_surface(points, target).max()) if len(points) else 0.0


def measure_joint(first: trimesh.Trimesh, second: trimesh.Trimesh, cell: float,
                  limits: JointLimits | None = None) -> JointMeasure:
    """Areas and largest point-to-surface distance of two regions; `cell` is
    the finer cell size of the surfaces holding them."""
    limits = limits or JointLimits()
    spacing = limits.sample_spacing_cells * cell
    a_area, b_area = float(first.area), float(second.area)
    larger = max(a_area, b_area)
    difference = abs(a_area - b_area) / larger if larger > 0.0 else 0.0
    a_query, a_used = _query_points(first, spacing, limits.max_sample_points)
    b_query, b_used = _query_points(second, spacing, limits.max_sample_points)
    distance = max(_one_way(a_query, second), _one_way(b_query, first))
    return JointMeasure(a_area, b_area, difference, distance, max(a_used, b_used))


def _query_points(mesh: trimesh.Trimesh, spacing: float,
                  max_points: int) -> tuple[np.ndarray, float]:
    """Every vertex, plus points spread by area (seeded, so repeatable) at
    about `spacing` apart; long thin triangles get no more than their share."""
    area = float(mesh.area)
    vertices = np.asarray(mesh.vertices, dtype=float)
    count = int(np.ceil(2.0 * area / spacing**2)) if spacing > 0.0 else 0
    if count > max_points:
        spacing *= float(np.sqrt(count / max_points))
        count = max_points
    if count == 0 or area <= 0.0:
        return vertices, spacing
    points, _ = trimesh.sample.sample_surface(mesh, count, seed=0)
    return np.vstack([vertices, np.asarray(points, dtype=float)]), spacing


def coincide(measure: JointMeasure, cell: float, limits: JointLimits | None = None) -> bool:
    limits = limits or JointLimits()
    return (measure.area_difference <= limits.max_area_difference
            and measure.max_distance <= limits.max_distance_cells * cell)


@dataclass(frozen=True)
class ProposedJoint:
    first: str
    second: str
    measure: JointMeasure


def _near(a: trimesh.Trimesh, b: trimesh.Trimesh, gap: float) -> bool:
    (alo, ahi), (blo, bhi) = a.bounds, b.bounds
    return bool(np.all(alo <= bhi + gap) and np.all(blo <= ahi + gap))


def propose_joints(surfaces: Sequence[SurfaceInfo], cells: dict[str, float],
                   limits: JointLimits | None = None) -> tuple[ProposedJoint, ...]:
    """Pairs of coincident regions of different surfaces, for the user to
    confirm. `cells`: owner -> cell size. Each region joins at most one other
    (the closest), in surface order."""
    limits = limits or JointLimits()
    regions: list[tuple[str, Region]] = [(s.owner, r) for s in surfaces for r in s.regions]
    found: list[tuple[float, int, int, JointMeasure]] = []
    for i, (owner_a, a) in enumerate(regions):
        for j in range(i + 1, len(regions)):
            owner_b, b = regions[j]
            if owner_a == owner_b:
                continue
            cell = min(cells[owner_a], cells[owner_b])
            if not _near(a.mesh, b.mesh, limits.max_distance_cells * cell):
                continue
            larger = max(a.area, b.area)
            if larger <= 0.0 or abs(a.area - b.area) / larger > limits.max_area_difference:
                continue
            measure = measure_joint(a.mesh, b.mesh, cell, limits)
            if coincide(measure, cell, limits):
                found.append((measure.max_distance, i, j, measure))
    used: set[int] = set()
    proposed = []
    for _, i, j, measure in sorted(found, key=lambda f: (f[0], f[1], f[2])):
        if i in used or j in used:
            continue
        used.update((i, j))
        proposed.append((i, j, measure))
    return tuple(ProposedJoint(regions[i][1].name, regions[j][1].name, m)
                 for i, j, m in sorted(proposed))
