"""Joints between imported surfaces: coincidence and proposals (G5; K1, K2).

A joint is two coincident regions of separately meshed imported surfaces
(domain parts or an imported rotating zone). Each becomes one side of a
cyclicAMI pair named after the regions (K1), so the two regions must cover
the same surface: about the same area, and every point of one close to the
other (K2). The limits are provisional, unverified on real CAD.

Distances are measured between points sampled on both regions: each region's
points against dense points on the other. The result is within about the
dense spacing of the true largest distance; the spacing is reported with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from machines.domains import Region, SurfaceInfo
from machines.surface_sampling import surface_points

# Points of each region are compared with points on the other at this
# fraction of the query spacing.
DENSE_FRACTION = 0.2


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
    max_sample_points: int = 2_000_000


@dataclass(frozen=True)
class JointMeasure:
    first_area: float
    second_area: float
    area_difference: float  # relative to the larger area
    max_distance: float  # m, both ways
    spacing: float  # m, of the dense points the distance is measured to

    def as_details(self) -> dict[str, float]:
        return {"first_area": self.first_area, "second_area": self.second_area,
                "area_difference": self.area_difference, "max_distance": self.max_distance,
                "sample_spacing": self.spacing}


def _one_way(points: np.ndarray, target: np.ndarray) -> float:
    distance, _ = cKDTree(target).query(points)
    return float(np.max(distance)) if len(points) else 0.0


def measure_joint(first: trimesh.Trimesh, second: trimesh.Trimesh, cell: float,
                  limits: JointLimits | None = None) -> JointMeasure:
    """Areas and largest point distance of two regions; `cell` is the finer
    cell size of the surfaces holding them."""
    limits = limits or JointLimits()
    spacing = limits.sample_spacing_cells * cell
    a_area, b_area = float(first.area), float(second.area)
    larger = max(a_area, b_area)
    difference = abs(a_area - b_area) / larger if larger > 0.0 else 0.0
    a_query, _ = surface_points(first, spacing, limits.max_sample_points)
    b_query, _ = surface_points(second, spacing, limits.max_sample_points)
    a_dense, a_used = surface_points(first, DENSE_FRACTION * spacing, limits.max_sample_points)
    b_dense, b_used = surface_points(second, DENSE_FRACTION * spacing,
                                     limits.max_sample_points)
    distance = max(_one_way(a_query, b_dense), _one_way(b_query, a_dense))
    return JointMeasure(a_area, b_area, difference, distance, max(a_used, b_used))


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
