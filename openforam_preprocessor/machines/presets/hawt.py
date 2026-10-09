"""HAWT preset (docs/rotating_machinery.md section 8; decision H1).

Every value is data with its source. The domain distances are the G0 R3 test
values: no cited source is recorded (rotating_machinery_notes.md, "HAWT domain
distances: unverified"), so they are labelled unverified. Replacing them with
cited values changes only this table.

Flow runs along the rotor axis, from its minimum (inlet) to its maximum
(outlet): orient the rotor so that the wind arrives from the axis minimum.

The rotor is measured about its rotation axis (hawt_rotor), not its bounding
box: an odd number of blades puts the box centre off the axis (G0's 3-blade
rotor: 0.125 m) and its extent below the swept diameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh

from machines.config import MachineProjectConfig
from machines.patches import draft_cylinder_patches
from vawt.config import Axis, plane_axes
from vawt.presets import POINT_OFFSET_CELLS

G0_UNVERIFIED = ("G0 R3 test value (docs/rotating_machinery_notes.md); unverified, "
                 "not a recommendation")


@dataclass(frozen=True)
class PresetValue:
    value: float
    unit: str  # "D": a multiple of the rotor diameter
    source: str


HAWT_VALUES: dict[str, PresetValue] = {
    # Domain: a cylinder along the rotor axis, centred on it.
    "upstream": PresetValue(2.0, "D", G0_UNVERIFIED),  # rotor centre to inlet
    "downstream": PresetValue(5.0, "D", G0_UNVERIFIED),  # rotor centre to outlet
    "domain_radius": PresetValue(2.0, "D", G0_UNVERIFIED),
    "domain_cell": PresetValue(1.0 / 8.0, "D", G0_UNVERIFIED),
    # Rotating zone: a disc around the rotor.
    "zone_diameter": PresetValue(1.2, "D", G0_UNVERIFIED),
    "zone_axial_margin": PresetValue(0.04, "D", G0_UNVERIFIED),  # beyond the rotor, each side
    "zone_cell": PresetValue(1.0 / 40.0, "D", G0_UNVERIFIED),
    # Zone mesh point: between the blade tips and the zone wall.
    "zone_point_radius": PresetValue(0.55, "D", "half-way between the tip (0.5 D) and the "
                                     "zone wall (0.6 D)"),
}

ZONE_NAME = "rotating"
BODY_NAME = "rotor"


def _value(name: str, diameter: float) -> float:
    return HAWT_VALUES[name].value * diameter


@dataclass(frozen=True)
class HawtRotor:
    """A HAWT rotor about a confirmed axis."""

    axis: Axis
    centre: tuple[float, float, float]  # on the rotation axis, mid-rotor along it
    diameter: float  # swept: twice the largest distance from the axis
    axial_min: float
    axial_max: float


def hawt_rotor(mesh: trimesh.Trimesh, axis: Axis,
               centre_uv: tuple[float, float] | None = None) -> HawtRotor:
    """Measure the rotor about `axis`. The axis passes through the area-weighted
    centroid of the surface in the plane normal to it (exact for a rotationally
    symmetric rotor), unless centre_uv gives it."""
    u, v = plane_axes(axis)
    vertices = np.asarray(mesh.vertices, dtype=float)
    if centre_uv is None:
        triangles = vertices[np.asarray(mesh.faces)]
        areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                        triangles[:, 2] - triangles[:, 0]), axis=1) / 2.0
        centroid = (triangles.mean(axis=1) * areas[:, None]).sum(axis=0) / areas.sum()
        centre_uv = (float(centroid[u.position]), float(centroid[v.position]))
    radial = np.hypot(vertices[:, u.position] - centre_uv[0],
                      vertices[:, v.position] - centre_uv[1])
    lo = float(vertices[:, axis.position].min())
    hi = float(vertices[:, axis.position].max())
    centre = [0.0, 0.0, 0.0]
    centre[u.position], centre[v.position] = centre_uv
    centre[axis.position] = (lo + hi) / 2.0
    return HawtRotor(axis, (centre[0], centre[1], centre[2]), 2.0 * float(radial.max()), lo, hi)


def hawt_draft(base: dict[str, Any], rotor: HawtRotor) -> MachineProjectConfig:
    """A HAWT draft for a measured rotor (hawt_rotor). Nothing is saved.

    base: "project_name" and "source" (the rotor STL: source_path,
    source_units and transform); anything else in base is kept. Raises
    pydantic.ValidationError when base is incomplete.
    """
    axis, d = rotor.axis, rotor.diameter
    u, v = plane_axes(axis)
    a = axis.position
    centre = rotor.centre
    rotor_lo, rotor_hi = rotor.axial_min, rotor.axial_max
    middle = centre[a]
    zone_cell, domain_cell = _value("zone_cell", d), _value("domain_cell", d)
    margin = _value("zone_axial_margin", d)
    zone_min, zone_max = rotor_lo - margin, rotor_hi + margin
    inlet, outlet = middle - _value("upstream", d), middle + _value("downstream", d)

    def point(radial: float, along: float, cell: float) -> dict[str, float]:
        p = [0.0, 0.0, 0.0]
        p[u.position] = centre[u.position] + POINT_OFFSET_CELLS[0] * cell
        p[v.position] = centre[v.position] + radial
        p[a] = along + POINT_OFFSET_CELLS[1] * cell
        return {"x": float(p[0]), "y": float(p[1]), "z": float(p[2])}

    patches = [{"name": BODY_NAME, "type": "ROTATING_WALL",
                "source": {"kind": "BODY", "ref": BODY_NAME}},
               *(p.model_dump(mode="json") for p in draft_cylinder_patches())]
    draft = {
        **{k: val for k, val in base.items() if k != "source"},
        "machine": "HAWT",
        "flow_axis": axis.value,
        "bodies": [{"name": BODY_NAME, "source": base.get("source"), "motion": "ROTATING",
                    "zone": ZONE_NAME}],
        "rotating_zones": [{
            "name": ZONE_NAME, "axis": axis.value,
            "shape": {"kind": "CYLINDER", "centre_u": float(centre[u.position]),
                      "centre_v": float(centre[v.position]), "axis_min": float(zone_min),
                      "axis_max": float(zone_max),
                      "diameter": float(_value("zone_diameter", d))},
            "cell_size": float(zone_cell),
            "location_in_mesh": point(_value("zone_point_radius", d), middle, zone_cell)}],
        "domain": {
            "kind": "CYLINDER", "axis": axis.value, "centre_u": float(centre[u.position]),
            "centre_v": float(centre[v.position]), "axis_min": float(inlet),
            "axis_max": float(outlet), "diameter": float(2.0 * _value("domain_radius", d)),
            "cell_size": float(domain_cell),
            # Upstream, half-way between the inlet and the zone.
            # Off the axis by a fraction of a cell, like the other coordinates (V0 E4).
            "location_in_mesh": point(0.29 * domain_cell, (inlet + zone_min) / 2.0,
                                      domain_cell)},
        "patches": patches,
    }
    return MachineProjectConfig.model_validate(draft)
