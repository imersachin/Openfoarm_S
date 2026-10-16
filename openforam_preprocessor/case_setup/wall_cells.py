"""Estimated first-cell y+ and the first-layer thickness for a target y+
(section 9.2; deferred from G6 to G7). A heuristic: shown as an estimate,
with its source, wherever it appears.

    Re = U L / nu
    Cf = 0.026 / Re^(1/7)            turbulent flat plate, 1/7-power law
    u_tau = U sqrt(Cf / 2)           friction velocity (tau_w = Cf rho U^2 / 2)

OpenFOAM's yPlus is evaluated at the centre of the wall cell, so a first
layer of thickness h puts it at y = h / 2:

    y+ = (h / 2) u_tau / nu          h = 2 y+ nu / u_tau

U and L are a reference speed and length the configuration does not hold
(a blade's relative speed and chord, say): the user gives them. The estimate
ignores pressure gradients, rotation, transition and separation; the
solver's yPlus after a run is the real value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SOURCE = ("Estimate: turbulent flat-plate skin friction Cf = 0.026 / Re^(1/7) "
          "(F. M. White, Fluid Mechanics; the correlation common y+ calculators use), "
          "y+ at the first cell centre (half the first-layer thickness). Not a solver result.")
CF_COEFFICIENT = 0.026
CF_EXPONENT = 1.0 / 7.0


@dataclass(frozen=True)
class WallEstimate:
    reynolds: float
    skin_friction: float  # Cf
    friction_velocity: float  # u_tau, m/s
    y_plus: float  # at the first cell centre
    first_layer_thickness: float  # h, m


def _check(**values: float) -> None:
    for name, value in values.items():
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"{name.replace('_', ' ')} must be a positive number, "
                             f"got {value!r}.")


def friction_velocity(speed: float, length: float,
                      kinematic_viscosity: float) -> tuple[float, float, float]:
    """(Re, Cf, u_tau) for a reference speed (m/s) and length (m)."""
    _check(reference_speed=speed, reference_length=length,
           kinematic_viscosity=kinematic_viscosity)
    reynolds = speed * length / kinematic_viscosity
    skin_friction = CF_COEFFICIENT / reynolds ** CF_EXPONENT
    return reynolds, skin_friction, speed * math.sqrt(skin_friction / 2.0)


def y_plus_of(first_layer_thickness: float, speed: float, length: float,
              kinematic_viscosity: float) -> WallEstimate:
    """The estimated y+ of a first layer of the given thickness (m)."""
    _check(first_layer_thickness=first_layer_thickness)
    reynolds, cf, u_tau = friction_velocity(speed, length, kinematic_viscosity)
    y_plus = (first_layer_thickness / 2.0) * u_tau / kinematic_viscosity
    return WallEstimate(reynolds, cf, u_tau, y_plus, first_layer_thickness)


def first_layer_for(target_y_plus: float, speed: float, length: float,
                    kinematic_viscosity: float) -> WallEstimate:
    """The first-layer thickness (m) that gives the target y+ (the reverse)."""
    _check(target_y_plus=target_y_plus)
    reynolds, cf, u_tau = friction_velocity(speed, length, kinematic_viscosity)
    thickness = 2.0 * target_y_plus * kinematic_viscosity / u_tau
    return WallEstimate(reynolds, cf, u_tau, target_y_plus, thickness)
