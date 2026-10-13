"""Physics of the approved family (docs/rotating_machinery.md section 9; G6).

Incompressible, isothermal, transient flow with a rotating mesh. The models
are frozen and strict like machines.config; they never reach a meshing input
(invariant 14). Cross-field rules (a rotation per zone, a value per inlet and
outlet, a direction) are findings in case_setup.validation, not model
errors, so a draft can be saved and completed later.

The derived quantities below are plain arithmetic, with the formulas of
section 9. Defaults are starting values, never recommendations.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.config.models import PositiveFloat
from vawt.config import Vec3

_STRICT = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
C_MU = 0.09  # section 9.2


class FluidConfig(BaseModel):
    """Section 9.1. Density converts pressures in Pa to kinematic pressure."""

    model_config = _STRICT

    density: PositiveFloat  # kg/m^3
    kinematic_viscosity: PositiveFloat  # m^2/s


# Section 9.1 presets: approximate values at about 20 degC.
AIR = FluidConfig(density=1.2, kinematic_viscosity=1.5e-5)
WATER = FluidConfig(density=998.0, kinematic_viscosity=1.0e-6)


class TurbulenceModel(StrEnum):
    K_OMEGA_SST = "K_OMEGA_SST"
    LAMINAR = "LAMINAR"


class TurbulenceConfig(BaseModel):
    """Section 9.2: inlet turbulence from intensity I and length scale L."""

    model_config = _STRICT

    model: TurbulenceModel = TurbulenceModel.K_OMEGA_SST
    intensity_percent: float = Field(default=5.0, gt=0, le=100)  # starting value (G0)
    length_scale: PositiveFloat | None = None  # m; required for k-omega SST


class RotationDirection(StrEnum):
    """Right-hand rule about the zone's axis (+x, +y or +z); never assumed."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class RpmSpeed(BaseModel):
    model_config = _STRICT

    kind: Literal["RPM"]
    rpm: float = Field(ge=0)  # zero is a finding (BLOCKING), not a model error


class TipSpeedRatio(BaseModel):
    """omega = lambda * U / R (section 9.3). U: the free-stream speed, or the
    speed of the one velocity inlet when not given."""

    model_config = _STRICT

    kind: Literal["TIP_SPEED_RATIO"]
    tip_speed_ratio: float = Field(ge=0)
    radius: PositiveFloat  # m
    free_stream_speed: PositiveFloat | None = None  # m/s


class RotationConfig(BaseModel):
    model_config = _STRICT

    speed: Annotated[RpmSpeed | TipSpeedRatio, Field(discriminator="kind")]
    direction: RotationDirection | None = None  # required: BLOCKING when absent


class VelocityInlet(BaseModel):
    model_config = _STRICT

    kind: Literal["VELOCITY"]
    velocity: Vec3  # m/s


class FlowRateInlet(BaseModel):
    """Volumetric flow rate (water), applied normal to the inlet."""

    model_config = _STRICT

    kind: Literal["FLOW_RATE"]
    volumetric_flow_rate: float = Field(ge=0)  # m^3/s; zero is a finding (BLOCKING)


InletCondition = Annotated[VelocityInlet | FlowRateInlet, Field(discriminator="kind")]


class OutletCondition(BaseModel):
    model_config = _STRICT

    pressure_pa: float = 0.0  # static (gauge) pressure, Pa


class WriteUnit(StrEnum):
    REVOLUTIONS = "REVOLUTIONS"
    DEGREES = "DEGREES"


class TimeConfig(BaseModel):
    """Section 9.6. The step is limited by both a rotation angle and a maximum
    Courant number (adjustable time step). Starting values (P3, P6)."""

    model_config = _STRICT

    degrees_per_step: float = Field(default=1.0, gt=0, le=90)
    max_courant: float = Field(default=2.0, gt=0, le=100)
    end_revolutions: float = Field(default=5.0, gt=0)
    write_interval: float = Field(default=1.0, gt=0)
    write_unit: WriteUnit = WriteUnit.REVOLUTIONS


class PhysicsConfig(BaseModel):
    """Everything case setup needs beyond the mesh. Keys are names: a zone name
    for `rotation`, a patch name for `inlets` and `outlets`."""

    model_config = _STRICT

    fluid: FluidConfig
    turbulence: TurbulenceConfig = Field(default_factory=TurbulenceConfig)
    rotation: dict[str, RotationConfig] = Field(default_factory=dict)
    inlets: dict[str, InletCondition] = Field(default_factory=dict)
    outlets: dict[str, OutletCondition] = Field(default_factory=dict)
    time: TimeConfig = Field(default_factory=TimeConfig)


# --- derived quantities ------------------------------------------------------------------

def kinematic_pressure(pressure_pa: float, density: float) -> float:
    """p / rho, m^2/s^2 (section 9.1)."""
    return pressure_pa / density


def turbulence_k(speed: float, intensity_percent: float) -> float:
    """k = 1.5 (U I)^2 (section 9.2)."""
    return 1.5 * (speed * intensity_percent / 100.0) ** 2


def turbulence_omega(k: float, length_scale: float) -> float:
    """omega = sqrt(k) / (C_mu^0.25 L) (section 9.2)."""
    return math.sqrt(k) / (C_MU**0.25 * length_scale)


def rpm_to_rad_s(rpm: float) -> float:
    return rpm * 2.0 * math.pi / 60.0


def rad_s_to_rpm(omega: float) -> float:
    return omega * 60.0 / (2.0 * math.pi)


def tip_speed_omega(tip_speed_ratio: float, free_stream_speed: float, radius: float) -> float:
    """omega = lambda U / R (section 9.3), rad/s."""
    return tip_speed_ratio * free_stream_speed / radius


def step_for_degrees(degrees: float, omega: float) -> float:
    """The time step that turns the zone by `degrees` at `omega` rad/s."""
    return math.radians(degrees) / abs(omega)


def revolution_time(omega: float) -> float:
    return 2.0 * math.pi / abs(omega)


def courant(speed: float, step: float, cell: float) -> float:
    """U dt / dx: a heuristic estimate, not the solver's Courant number."""
    return speed * step / cell
