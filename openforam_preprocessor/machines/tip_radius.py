"""The rotor tip radius measured from the geometry, offered beside a typed
tip-speed-ratio radius (M1c, owner): never applied without the user's button.

STL: the largest distance from the zone's axis of the points of the rotating
bodies in the zone ("from rotor body <name>"). Imported mesh: of the points of
the faces of rotating-wall patches on the zone's cells ("from rotor patch
<name>"; a rotor of another zone is left out). A typed radius
more than RADIUS_TOLERANCE away from the measured one is an INFO note.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.mesh_folder import poly_mesh_dir
from visualization.foam_reader import (
    FoamReadError,
    read_boundary,
    read_faces,
    read_labels,
    read_points,
)

RADIUS_TOLERANCE = 0.05  # 5 % (owner)
Point = tuple[float, float, float]


@dataclass(frozen=True)
class MeasuredRadius:
    radius: float  # m
    source: str  # "from rotor body hub, blades" / "from rotor patch rotor"


def radial_max(points: np.ndarray, origin: Point, axis: Point) -> float:
    """The largest distance of the points from the axis through origin."""
    direction = np.asarray(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    relative = np.asarray(points, dtype=float) - np.asarray(origin, dtype=float)
    radial = relative - np.outer(relative @ direction, direction)
    return float(np.linalg.norm(radial, axis=1).max()) if len(radial) else 0.0


def zone_patch_points(folder: Path, names: Sequence[str], cells: np.ndarray,
                      ) -> tuple[tuple[str, ...], np.ndarray] | None:
    """The named patches with faces on the zone's cells (a rotor wall of this
    zone, not of another), and the points of those faces; None if there are none."""
    poly = poly_mesh_dir(folder)
    try:
        patches = [p for p in read_boundary(poly / "boundary") if p.name in names]
        if not any(p.n_faces for p in patches):
            return None
        owner = read_labels(poly / "owner")
        points = read_points(poly / "points")
        faces = read_faces(poly / "faces")
    except (FoamReadError, OSError):
        return None
    inside = np.zeros(int(max(owner.max(), cells.max(initial=0))) + 1, dtype=bool)
    inside[cells] = True
    used = [p for p in patches if p.n_faces
            and inside[owner[p.start_face:p.start_face + p.n_faces]].any()]
    if not used:
        return None
    labels = np.unique(np.concatenate([
        faces[i] for p in used for i in range(p.start_face, p.start_face + p.n_faces)
        if inside[owner[i]]]))
    return tuple(p.name for p in used), points[labels]


def differs(zone: str, typed: float, measured: MeasuredRadius, field: str) -> Issue | None:
    """INFO when the typed radius is more than RADIUS_TOLERANCE off the measured."""
    if measured.radius <= 0 or abs(typed - measured.radius) <= RADIUS_TOLERANCE * measured.radius:
        return None
    percent = 100 * (typed - measured.radius) / measured.radius
    return Issue(
        category=IssueCategory.CONFIGURATION, severity=IssueSeverity.INFO,
        stage=IssueStage.CASE_SETUP, code="TIP_RADIUS_DIFFERS",
        message=f"Zone {zone}: the tip radius R = {typed:g} m is {percent:+.1f} % from the "
                f"measured {measured.radius:.4g} m ({measured.source}).",
        suggested_action="Check R: the tip-speed ratio gives the rotation speed from it. "
                         "'Use measured' sets the measured value.",
        details={"field": field, "typed": typed, "measured": measured.radius,
                 "source": measured.source})
