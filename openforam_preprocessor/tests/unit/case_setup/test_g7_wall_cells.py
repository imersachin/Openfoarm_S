"""G7: the estimated first-cell y+ and its reverse (section 9.2, deferred from G6)."""

from __future__ import annotations

import math

import pytest

from case_setup.wall_cells import SOURCE, first_layer_for, friction_velocity, y_plus_of

NU_AIR = 1.5e-5


def test_flat_plate_estimate_by_hand() -> None:
    reynolds, cf, u_tau = friction_velocity(10.0, 1.0, NU_AIR)
    assert reynolds == pytest.approx(10.0 / NU_AIR)
    assert cf == pytest.approx(0.026 / (10.0 / NU_AIR) ** (1 / 7))
    assert u_tau == pytest.approx(10.0 * math.sqrt(cf / 2))
    assert cf == pytest.approx(3.8e-3, rel=0.02)  # Re 6.7e5: Cf about 0.0038


def test_y_plus_is_at_the_first_cell_centre() -> None:
    _, _, u_tau = friction_velocity(10.0, 1.0, NU_AIR)
    estimate = y_plus_of(1e-4, 10.0, 1.0, NU_AIR)
    assert estimate.y_plus == pytest.approx(0.5e-4 * u_tau / NU_AIR)
    assert estimate.first_layer_thickness == 1e-4


def test_the_reverse_gives_back_the_target() -> None:
    reverse = first_layer_for(30.0, 25.0, 0.3, 1.0e-6)
    forward = y_plus_of(reverse.first_layer_thickness, 25.0, 0.3, 1.0e-6)
    assert forward.y_plus == pytest.approx(30.0)
    assert reverse.y_plus == 30.0


def test_y_plus_grows_linearly_with_the_first_layer() -> None:
    thin, thick = y_plus_of(1e-5, 10.0, 1.0, NU_AIR), y_plus_of(2e-5, 10.0, 1.0, NU_AIR)
    assert thick.y_plus == pytest.approx(2 * thin.y_plus)


@pytest.mark.parametrize("args", [(0.0, 1.0, NU_AIR), (10.0, -1.0, NU_AIR), (10.0, 1.0, 0.0),
                                  (math.inf, 1.0, NU_AIR), (math.nan, 1.0, NU_AIR)])
def test_non_positive_inputs_are_refused(args: tuple[float, float, float]) -> None:
    with pytest.raises(ValueError, match="positive"):
        friction_velocity(*args)
    with pytest.raises(ValueError, match="positive"):
        first_layer_for(1.0, *args)


def test_the_source_is_stated() -> None:
    assert "0.026 / Re^(1/7)" in SOURCE and "Estimate" in SOURCE and "White" in SOURCE
