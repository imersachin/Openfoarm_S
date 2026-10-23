"""The rotor tip radius measured from the geometry, offered beside a typed
tip-speed-ratio radius (M1c, owner): never applied without the user's button.

STL: the largest distance from the zone's axis of the points of the rotating
bodies in the zone ("from rotor body <name>"). Imported mesh: of the points of
the faces of rotating-wall patches on the zone's cells ("from rotor patch
<name>"; a rotor of another zone is left out), from the mesh's extracted
surface (machines.msh_import.surface.zone_patch_points; M2a). A typed radius
more than RADIUS_TOLERANCE away from the measured one is an INFO note.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage

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


def differs(zone: str, typed: float, measured: MeasuredRadius, field: str) -> Issue | None:
    """INFO when the typed radius is more than RADIUS_TOLERANCE off the measured."""
    if measured.radius <= 0 or abs(typed - measured.radius) <= RADIUS_TOLERANCE * measured.radius:
        return None
    percent = 100 * (typed - measured.radius) / measured.radius
    return Issue(
        category=IssueCategory.CONFIGURATION, severity=IssueSeverity.INFO,
        stage=IssueStage.CASE_SETUP, code="TIP_RADIUS_DIFFERS",
        message=f"Zone {zone}: the tip radius R = {typed:g} m is {percent:+.1f} % from the "
                f"measured outer swept radius (blade corners) {measured.radius:.4g} m "
                f"({measured.source}).",
        suggested_action="Check R: the tip-speed ratio gives the rotation speed from it, "
                         "usually with the blade mounting radius, which may be slightly "
                         "smaller than the measured outer swept radius. "
                         "'Use measured' sets the measured value.",
        details={"field": field, "typed": typed, "measured": measured.radius,
                 "source": measured.source})
