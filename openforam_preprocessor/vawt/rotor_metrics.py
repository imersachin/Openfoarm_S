"""Rotor measurements from the transformed geometry, in metres (spec section 7.1).

The output is a report. The suggested axis is never applied without the user
confirming it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from vawt.config import Axis, plane_axes

ROUNDNESS_MARGIN = 0.05  # how clearly one axis must win to be suggested


@dataclass(frozen=True)
class AxisCandidate:
    axis: Axis
    diameter: float  # largest extent normal to the axis
    span: float  # extent along the axis
    roundness: float  # smaller / larger of the two extents normal to the axis


@dataclass(frozen=True)
class RotorMetrics:
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    extents: tuple[float, float, float]
    centre: tuple[float, float, float]  # bounding-box centre (not on the axis in general)
    candidates: tuple[AxisCandidate, ...]
    suggested_axis: Axis | None
    suggestion_note: str
    # For a confirmed axis only:
    axis: Axis | None = None
    diameter: float | None = None  # swept: twice the sweep radius
    span: float | None = None
    sweep_radius: float | None = None  # largest vertex distance from the axis
    axis_point: tuple[float, float, float] | None = None  # on the axis, mid-span

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidates"] = [
            {**asdict(c), "axis": c.axis.value} for c in self.candidates
        ]
        data["suggested_axis"] = self.suggested_axis.value if self.suggested_axis else None
        data["axis"] = self.axis.value if self.axis else None
        return data


def _triple(values: np.ndarray) -> tuple[float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]))


def surface_centroid(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """The area-weighted centroid of a triangulated surface. In the plane normal
    to the rotation axis it lies on the axis of a rotor of 2 or more identical
    blades evenly spaced (an odd count puts the bounding-box centre off it)."""
    triangles = np.asarray(vertices, dtype=float)[np.asarray(faces)]
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                    triangles[:, 2] - triangles[:, 0]), axis=1) / 2.0
    centroid: np.ndarray = (triangles.mean(axis=1) * areas[:, None]).sum(axis=0) / areas.sum()
    return centroid


def compute_rotor_metrics(vertices: np.ndarray, axis: Axis | None = None, *,
                          faces: np.ndarray | None = None,
                          centre_uv: tuple[float, float] | None = None,
                          roundness_margin: float = ROUNDNESS_MARGIN) -> RotorMetrics:
    """Extents and the axis suggestion; for a confirmed axis also the axis point
    (centre_uv when given, else the centroid of the surface `faces`; the app
    always passes them) and D and the sweep radius about it (U1)."""
    points = np.asarray(vertices, dtype=np.float64)
    if points.ndim != 2 or len(points) == 0:
        raise ValueError("Rotor metrics need at least one vertex.")
    lo, hi = points.min(axis=0), points.max(axis=0)
    extents, centre = hi - lo, (lo + hi) / 2.0

    candidates = []
    for candidate in Axis:
        u, v = plane_axes(candidate)
        eu, ev = extents[u.position], extents[v.position]
        larger = max(eu, ev)
        candidates.append(AxisCandidate(
            axis=candidate,
            diameter=float(larger),
            span=float(extents[candidate.position]),
            roundness=float(min(eu, ev) / larger) if larger > 0 else 0.0,
        ))

    # A rotor sweeps a circle about its axis, so its extents normal to the axis
    # are nearly equal. Suggest an axis only when exactly one is clearly round.
    ranked = sorted(candidates, key=lambda c: c.roundness, reverse=True)
    best, runner_up = ranked[0], ranked[1]
    if best.roundness >= 1.0 - roundness_margin and (
        best.roundness - runner_up.roundness > roundness_margin
    ):
        suggested: Axis | None = best.axis
        note = (f"Suggested axis {best.axis.value}: the geometry is round in the plane "
                f"normal to it (roundness {best.roundness:.3f}). Confirm before use.")
    else:
        suggested = None
        note = ("No axis suggested: no single axis has clearly equal extents normal to "
                "it. Choose the rotor axis explicitly.")

    metrics = RotorMetrics(
        bounds_min=_triple(lo), bounds_max=_triple(hi), extents=_triple(extents),
        centre=_triple(centre), candidates=tuple(candidates),
        suggested_axis=suggested, suggestion_note=note,
    )
    if axis is None:
        return metrics

    u, v = plane_axes(axis)
    chosen = next(c for c in candidates if c.axis is axis)
    if centre_uv is None and faces is not None:
        centroid = surface_centroid(points, faces)
        centre_uv = (float(centroid[u.position]), float(centroid[v.position]))
    elif centre_uv is None:  # vertices only: the box centre (on the axis only if symmetric)
        centre_uv = (float(centre[u.position]), float(centre[v.position]))
    point = centre.copy()  # mid-span along the axis
    point[u.position], point[v.position] = centre_uv
    radial = np.hypot(points[:, u.position] - point[u.position],
                      points[:, v.position] - point[v.position])
    return replace(
        metrics,
        axis=axis,
        diameter=2.0 * float(radial.max()),
        span=chosen.span,
        sweep_radius=float(radial.max()),
        axis_point=_triple(point),
    )
