"""Preset starting values (spec section 7.2).

These are starting values carried over from the earlier application. They are
not engineering recommendations and must not be presented as such. A preset
produces a draft; nothing is saved or applied without the user.

Conventions: inlet on the minimum face of the flow axis, outlet on the maximum.
The lateral axis is the axis that is neither the rotor axis nor the flow axis.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from vawt.config import Axis, InterfaceType, LayerSizing, plane_axes, third_axis
from vawt.rotor_metrics import RotorMetrics

PRESET_NOTE = (
    "Preset values are starting points carried over from the earlier application, "
    "not engineering recommendations. Review every value for your case."
)


class PresetKind(StrEnum):
    SIMPLE = "SIMPLE"
    EXTENDED = "EXTENDED"


@dataclass(frozen=True)
class DomainExtents:
    """Distances, as multiples of D or H, measured as noted per field."""

    upstream_d: float  # rotor centre to inlet, in D
    downstream_d: float  # rotor centre to outlet, in D
    lateral_half: tuple[float, str]  # (multiple, "D" | "H") about the centre
    axial: tuple[float, str, str]  # (multiple, "D" | "H", "rotor" | "centre")


DOMAIN_PRESETS: dict[PresetKind, DomainExtents] = {
    PresetKind.SIMPLE: DomainExtents(3.0, 7.0, (1.5, "D"), (1.5, "D", "rotor")),
    PresetKind.EXTENDED: DomainExtents(6.0, 8.0, (3.0, "H"), (3.0, "H", "centre")),
}

ZONE_DIAMETER_D = 1.5
ZONE_AXIAL_MARGIN_H = 0.05  # cylinder extends rotor +/- 0.05 H along the axis
WAKE_UPSTREAM_D, WAKE_DOWNSTREAM_D = 1.5, 8.0
WAKE_LATERAL_HALF_D = 2.5
WAKE_HEIGHT_H = 3.0  # total, centred on the rotor
WAKE_CLAMP_FRACTION = 0.05  # kept this fraction of each domain length inside the domain
DOMAIN_CELLS_ACROSS_D = 9.0  # domain cell size D/9
ZONE_CELLS_ACROSS_D = 22.0  # rotating-zone cell size D/22
LEVELS = {"wake": 1, "interface": 1, "blade_min": 1, "blade_max": 2}
LAYERS = {"count": 3, "expansion_ratio": 1.2, "final_layer_thickness": 0.3,
          "min_thickness": 0.1}
ABSOLUTE_FIRST_LAYER_PER_D = 1.0 / 5000.0
INNER_POINT_RADIUS_D = 0.625  # midway between rotor edge (D/2) and zone wall (0.75 D)


def _vec(values: list[float]) -> dict[str, float]:
    return {"x": float(values[0]), "y": float(values[1]), "z": float(values[2])}


def draft_from_preset(
    base: dict[str, Any],
    metrics: RotorMetrics,
    flow_axis: Axis,
    kind: PresetKind = PresetKind.SIMPLE,
    *,
    include_domain: bool = True,
) -> dict[str, Any]:
    """A draft configuration filled from rotor metrics for a confirmed axis.

    `base` supplies everything presets do not set (project, geometry, ...).
    """
    if metrics.axis is None or metrics.diameter is None or metrics.span is None:
        raise ValueError("Presets need rotor metrics computed for a confirmed axis.")
    axis = metrics.axis
    if flow_axis is axis:
        raise ValueError("flow_axis must differ from the rotor axis.")
    lateral = third_axis(axis, flow_axis)
    d, h = metrics.diameter, metrics.span
    centre = list(metrics.centre)
    a, f, lat = axis.position, flow_axis.position, lateral.position
    rotor_lo, rotor_hi = metrics.bounds_min[a], metrics.bounds_max[a]
    u, v = plane_axes(axis)
    scale = {"D": d, "H": h}

    zone_radius = ZONE_DIAMETER_D * d / 2.0
    zone_axis_min = rotor_lo - ZONE_AXIAL_MARGIN_H * h
    zone_axis_max = rotor_hi + ZONE_AXIAL_MARGIN_H * h

    inner = list(centre)
    inner[u.position] = centre[u.position] + INNER_POINT_RADIUS_D * d
    inner[a] = (rotor_lo + rotor_hi) / 2.0

    draft = copy.deepcopy(base)
    draft["rotor"] = {"axis": axis.value, "flow_axis": flow_axis.value}
    draft["rotating_zone"] = {
        "centre_u": float(centre[u.position]),
        "centre_v": float(centre[v.position]),
        "axis_min": float(zone_axis_min),
        "axis_max": float(zone_axis_max),
        "diameter": float(2.0 * zone_radius),
        "interface": (InterfaceType.AMI if include_domain else InterfaceType.CELL_ZONE).value,
        "cell_size": float(d / ZONE_CELLS_ACROSS_D),
        "location_in_mesh": _vec(inner),
    }

    refinement = dict(draft.get("refinement") or {})
    refinement.update(
        blade_min_level=LEVELS["blade_min"], blade_max_level=LEVELS["blade_max"],
        interface_level=LEVELS["interface"], wake=None,
    )
    if include_domain:
        extents = DOMAIN_PRESETS[kind]
        lo, hi = list(centre), list(centre)
        lo[f] = centre[f] - extents.upstream_d * d
        hi[f] = centre[f] + extents.downstream_d * d
        lateral_half = extents.lateral_half[0] * scale[extents.lateral_half[1]]
        lo[lat], hi[lat] = centre[lat] - lateral_half, centre[lat] + lateral_half
        axial = extents.axial[0] * scale[extents.axial[1]]
        if extents.axial[2] == "rotor":
            lo[a], hi[a] = rotor_lo - axial, rotor_hi + axial
        else:
            lo[a], hi[a] = centre[a] - axial, centre[a] + axial

        outer = list(centre)
        outer[f] = (lo[f] + (centre[f] - zone_radius)) / 2.0
        draft["domain"] = {
            "bounds": {"minimum": _vec(lo), "maximum": _vec(hi)},
            "cell_size": float(d / DOMAIN_CELLS_ACROSS_D),
            "patches": dict((base.get("domain") or {}).get("patches") or {}),
            "location_in_mesh": _vec(outer),
        }
        wake = _clamped_wake(centre, d, h, f, lat, a, lo, hi)
        if wake is not None:
            refinement["wake"] = {"box": wake, "level": LEVELS["wake"]}
    else:
        draft["domain"] = None
    draft["refinement"] = refinement

    layers = dict(draft.get("layers") or {})
    layers.update(LAYERS, first_layer_thickness=float(d * ABSOLUTE_FIRST_LAYER_PER_D))
    layers.setdefault("sizing", LayerSizing.RELATIVE.value)
    draft["layers"] = layers
    return draft


def _clamped_wake(centre: list[float], d: float, h: float, f: int, lat: int, a: int,
                  domain_lo: list[float], domain_hi: list[float]) -> dict[str, Any] | None:
    lo, hi = list(centre), list(centre)
    lo[f], hi[f] = centre[f] - WAKE_UPSTREAM_D * d, centre[f] + WAKE_DOWNSTREAM_D * d
    lo[lat], hi[lat] = centre[lat] - WAKE_LATERAL_HALF_D * d, centre[lat] + WAKE_LATERAL_HALF_D * d
    lo[a], hi[a] = centre[a] - WAKE_HEIGHT_H * h / 2.0, centre[a] + WAKE_HEIGHT_H * h / 2.0
    for i in range(3):
        inset = WAKE_CLAMP_FRACTION * (domain_hi[i] - domain_lo[i])
        lo[i] = max(lo[i], domain_lo[i] + inset)
        hi[i] = min(hi[i], domain_hi[i] - inset)
        if lo[i] >= hi[i]:
            return None
    return {"minimum": _vec(lo), "maximum": _vec(hi)}
