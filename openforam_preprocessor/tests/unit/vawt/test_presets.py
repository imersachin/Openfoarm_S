from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tests.fixtures.vawt.rotor import RotorSpec, rotor_mesh
from vawt.config import Axis, VawtProjectConfig
from vawt.presets import PRESET_NOTE, PresetKind, _clamped_wake, draft_from_preset
from vawt.rotor_metrics import compute_rotor_metrics

SPEC = RotorSpec()  # axis z, centred at the origin
D, H = SPEC.diameter, SPEC.span
BASE = {"project_name": "P", "geometry": {"source_path": "rotor.stl", "source_units": "m"}}


def draft(kind: PresetKind = PresetKind.SIMPLE, flow: Axis = Axis.X, **kwargs):
    metrics = compute_rotor_metrics(rotor_mesh(SPEC).vertices, Axis.Z)
    return draft_from_preset(BASE, metrics, flow, kind, **kwargs)


def box(data: dict) -> tuple[list[float], list[float]]:
    return ([data["minimum"][k] for k in "xyz"], [data["maximum"][k] for k in "xyz"])


def test_rotating_zone_values() -> None:
    zone = draft()["rotating_zone"]

    assert zone["diameter"] == pytest.approx(1.5 * D)
    assert zone["axis_min"] == pytest.approx(-H / 2 - 0.05 * H)
    assert zone["axis_max"] == pytest.approx(H / 2 + 0.05 * H)
    assert zone["cell_size"] == pytest.approx(D / 22)
    assert (zone["centre_u"], zone["centre_v"]) == pytest.approx((0.0, 0.0))
    assert zone["interface"] == "AMI"


def test_simple_domain_with_inlet_on_minimum_of_flow_axis() -> None:
    domain = draft(PresetKind.SIMPLE)["domain"]
    lo, hi = box(domain["bounds"])

    assert (lo[0], hi[0]) == pytest.approx((-3 * D, 7 * D))  # flow x: inlet at min
    assert (lo[1], hi[1]) == pytest.approx((-1.5 * D, 1.5 * D))  # lateral y
    assert (lo[2], hi[2]) == pytest.approx((-H / 2 - 1.5 * D, H / 2 + 1.5 * D))  # axis z
    assert domain["cell_size"] == pytest.approx(D / 9)


def test_extended_domain() -> None:
    lo, hi = box(draft(PresetKind.EXTENDED)["domain"]["bounds"])

    assert (lo[0], hi[0]) == pytest.approx((-6 * D, 8 * D))
    assert (lo[1], hi[1]) == pytest.approx((-3 * H, 3 * H))
    assert (lo[2], hi[2]) == pytest.approx((-3 * H, 3 * H))


def test_flow_along_y_swaps_flow_and_lateral() -> None:
    lo, hi = box(draft(flow=Axis.Y)["domain"]["bounds"])

    assert (lo[1], hi[1]) == pytest.approx((-3 * D, 7 * D))
    assert (lo[0], hi[0]) == pytest.approx((-1.5 * D, 1.5 * D))


def test_extended_wake_box_unclamped_where_it_fits() -> None:
    lo, hi = box(draft(PresetKind.EXTENDED)["refinement"]["wake"]["box"])

    assert (lo[0], hi[0]) == pytest.approx((-1.5 * D, 8 * D - 0.05 * 14 * D))  # clamped
    assert (lo[1], hi[1]) == pytest.approx((-2.5 * D, 2.5 * D))  # fits inside +/- 3H
    assert (lo[2], hi[2]) == pytest.approx((-1.5 * H, 1.5 * H))


def test_simple_wake_box_is_clamped_five_percent_inside() -> None:
    data = draft(PresetKind.SIMPLE)
    lo, hi = box(data["refinement"]["wake"]["box"])
    dlo, dhi = box(data["domain"]["bounds"])

    for i in range(3):
        inset = 0.05 * (dhi[i] - dlo[i])
        assert lo[i] >= dlo[i] + inset - 1e-12
        assert hi[i] <= dhi[i] - inset + 1e-12
    assert hi[0] == pytest.approx(7 * D - 0.05 * 10 * D)
    assert (lo[1], hi[1]) == pytest.approx((-1.5 * D + 0.15 * D, 1.5 * D - 0.15 * D))
    assert data["refinement"]["wake"]["level"] == 1


def test_wake_dropped_when_clamping_leaves_nothing() -> None:
    # Wake spans x in [-1.5, 8]; this domain starts at x = 10, so nothing remains.
    assert _clamped_wake([0.0, 0.0, 0.0], 1.0, 1.0, 0, 1, 2,
                         [10.0, -1.0, -1.0], [12.0, 1.0, 1.0]) is None


def test_levels_and_layers() -> None:
    data = draft()
    refinement, layers = data["refinement"], data["layers"]

    assert (refinement["blade_min_level"], refinement["blade_max_level"]) == (1, 2)
    assert refinement["interface_level"] == 1
    assert (layers["count"], layers["expansion_ratio"]) == (3, 1.2)
    assert (layers["final_layer_thickness"], layers["min_thickness"]) == (0.3, 0.1)
    assert layers["sizing"] == "RELATIVE"
    assert layers["first_layer_thickness"] == pytest.approx(D / 5000)


def test_mesh_points() -> None:
    # V2 (R1): points are moved off the symmetry planes by a fraction of a cell,
    # because on them they can lie on background-cell edges (V0 E4).
    data = draft()
    inner = data["rotating_zone"]["location_in_mesh"]
    outer = data["domain"]["location_in_mesh"]
    zone_cell, domain_cell = D / 22, D / 9

    assert (inner["x"], inner["y"], inner["z"]) == pytest.approx(
        (0.625 * D, 0.237 * zone_cell, 0.371 * zone_cell))
    assert outer["x"] == pytest.approx((-3 * D + (-0.75 * D)) / 2)
    assert (outer["y"], outer["z"]) == pytest.approx((0.237 * domain_cell, 0.371 * domain_cell))


def test_without_domain_uses_cell_zone_and_no_wake() -> None:
    data = draft(include_domain=False)

    assert data["domain"] is None
    assert data["rotating_zone"]["interface"] == "CELL_ZONE"
    assert data["refinement"]["wake"] is None


def test_draft_is_valid_and_base_is_untouched(tmp_path: Path) -> None:
    base = copy.deepcopy(BASE)
    metrics = compute_rotor_metrics(rotor_mesh(SPEC).vertices, Axis.Z)

    data = draft_from_preset(base, metrics, Axis.X)

    assert base == BASE
    VawtProjectConfig.model_validate(data)


def test_requires_confirmed_axis_and_distinct_flow() -> None:
    unconfirmed = compute_rotor_metrics(rotor_mesh(SPEC).vertices)
    with pytest.raises(ValueError):
        draft_from_preset(BASE, unconfirmed, Axis.X)
    confirmed = compute_rotor_metrics(rotor_mesh(SPEC).vertices, Axis.Z)
    with pytest.raises(ValueError):
        draft_from_preset(BASE, confirmed, Axis.Z)


def test_preset_note_disclaims_recommendation() -> None:
    assert "not engineering recommendations" in PRESET_NOTE
