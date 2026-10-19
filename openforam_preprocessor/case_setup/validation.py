"""Checks before case setup (docs/rotating_machinery.md section 10, G6 rows).

Physics findings never stop meshing (invariant 14): the pipeline meshes, then
skips case setup when one of these is ERROR or BLOCKING. Thresholds are
configuration, never constants.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from case_setup.physics import (
    FlowRateInlet,
    RpmSpeed,
    TipSpeedRatio,
    TurbulenceModel,
)
from case_setup.setup import (
    CaseSetupError,
    PatchKind,
    configured_kinds,
    free_stream_speed,
    inlet_area,
    resolve,
)
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from machines.config import MachineProjectConfig
from machines.domains import SurfaceInfo


@dataclass(frozen=True)
class CaseSetupThresholds:
    # A rotation angle per step above this is a WARNING. Provisional: 1 degree
    # per step is what every G0 solver run used (notes section 4).
    max_degrees_per_step: float = 1.0


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           explanation: str = "", **details: Any) -> Issue:
    return Issue(category=IssueCategory.CONFIGURATION, severity=severity,
                 stage=IssueStage.CASE_SETUP, code=code, message=message,
                 explanation=explanation, suggested_action=action, details=details)


def _blocking(code: str, message: str, action: str, **details: Any) -> Issue:
    return _issue(IssueSeverity.BLOCKING, code, message, action, **details)


def _rotation_issues(config: MachineProjectConfig) -> list[Issue]:
    physics = config.physics
    assert physics is not None
    found: list[Issue] = []
    zones = set(config.zone_names())
    if len(zones) != 1:
        found.append(_blocking(
            "CASE_SETUP_ONE_ZONE", "Case setup supports one rotating zone; this project has "
            f"{len(zones)}.", "Mesh it with one rotating zone, or wait for a later milestone.",
            zones=sorted(zones)))
    for name in sorted(set(physics.rotation) - zones):
        found.append(_blocking("ROTATION_UNKNOWN_ZONE", f"Rotation is set for {name}, which "
                               "is not a rotating zone.", "Remove it or rename the zone.",
                               field=f"physics.rotation.{name}"))
    for name in sorted(zones):
        rotation = physics.rotation.get(name)
        path = f"physics.rotation.{name}"
        if rotation is None:
            found.append(_blocking("ROTATION_NOT_SET", f"Zone {name} has no rotation speed.",
                                   "Set its speed and direction.", field=path))
            continue
        speed = rotation.speed
        zero = (speed.rpm if isinstance(speed, RpmSpeed) else speed.tip_speed_ratio) == 0
        if zero:
            found.append(_blocking("ROTATION_SPEED_ZERO", f"Zone {name} does not rotate "
                                   "(speed zero).", "Set a speed above zero.",
                                   field=f"{path}.speed"))
        if rotation.direction is None:
            found.append(_blocking(
                "ROTATION_DIRECTION_NOT_SET", f"The rotation direction of zone {name} is not "
                "set; it is never assumed.", "Choose POSITIVE or NEGATIVE (right-hand rule "
                "about the zone's axis).", field=f"{path}.direction"))
        if (isinstance(speed, TipSpeedRatio) and speed.free_stream_speed is None
                and free_stream_speed(physics) is None):
            found.append(_blocking(
                "FREE_STREAM_SPEED_NOT_SET", f"Zone {name}: the tip-speed ratio needs the "
                "free-stream speed.", "Give the free-stream speed, or use exactly one "
                "velocity inlet.", field=f"{path}.speed.free_stream_speed"))
    return found


def _boundary_issues(config: MachineProjectConfig,
                     surfaces: Sequence[SurfaceInfo]) -> list[Issue]:
    physics = config.physics
    assert physics is not None
    kinds = configured_kinds(config)
    inlets = {n for n, k in kinds.items() if k is PatchKind.INLET}
    outlets = {n for n, k in kinds.items() if k is PatchKind.OUTLET}
    found: list[Issue] = []
    for name in sorted(inlets - set(physics.inlets)):
        found.append(_blocking("INLET_VALUE_NOT_SET", f"Inlet {name} has no inlet value.",
                               "Give its velocity or flow rate.", field=f"physics.inlets.{name}"))
    for name in sorted(outlets - set(physics.outlets)):
        found.append(_blocking("OUTLET_VALUE_NOT_SET", f"Outlet {name} has no outlet "
                               "pressure.", "Give its static pressure in Pa.",
                               field=f"physics.outlets.{name}"))
    for name in sorted(set(physics.inlets) - inlets):
        found.append(_blocking("INLET_VALUE_NOT_INLET", f"An inlet value is set for {name}, "
                               "which is not an INLET patch.", "Remove it or change the "
                               "patch type.", field=f"physics.inlets.{name}"))
    for name in sorted(set(physics.outlets) - outlets):
        found.append(_blocking("OUTLET_VALUE_NOT_OUTLET", f"An outlet pressure is set for "
                               f"{name}, which is not an OUTLET patch.", "Remove it or "
                               "change the patch type.", field=f"physics.outlets.{name}"))
    for name, inlet in sorted(physics.inlets.items()):
        zero = (inlet.volumetric_flow_rate == 0 if isinstance(inlet, FlowRateInlet)
                else (inlet.velocity.x, inlet.velocity.y, inlet.velocity.z) == (0, 0, 0))
        if name in inlets and zero:
            # k = omega = 0 at the inlet: k-omega SST divides by zero (SIGFPE).
            found.append(_blocking("INLET_VALUE_ZERO", f"Inlet {name} has no inflow (zero "
                                   "velocity or flow rate).", "Give a non-zero inflow, or "
                                   "make the patch a WALL.", field=f"physics.inlets.{name}"))
        if name in inlets and isinstance(inlet, FlowRateInlet) and not inlet_area(
                config, surfaces, name):
            found.append(_blocking("INLET_AREA_UNKNOWN", f"Inlet {name}: the flow rate needs "
                                   "the inlet's area, which could not be measured.",
                                   "Use a velocity inlet, or check the inlet's surface.",
                                   field=f"physics.inlets.{name}"))
    turbulence = physics.turbulence
    if turbulence.model is TurbulenceModel.K_OMEGA_SST and turbulence.length_scale is None:
        found.append(_blocking("TURBULENCE_LENGTH_SCALE_NOT_SET", "k-omega SST needs the "
                               "inlet turbulence length scale.", "Give the length scale L "
                               "(m).", field="physics.turbulence.length_scale"))
    return found


def check_physics(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
                  thresholds: CaseSetupThresholds | None = None) -> tuple[Issue, ...]:
    """Every finding about the physics section. Empty when it is absent."""
    if config.physics is None:
        return ()
    thresholds = thresholds or CaseSetupThresholds()
    controls = config.physics.time
    found = [*_rotation_issues(config), *_boundary_issues(config, surfaces)]
    if controls.degrees_per_step > thresholds.max_degrees_per_step:
        found.append(_issue(
            IssueSeverity.WARNING, "ROTATION_STEP_LARGE",
            f"The time step allows {controls.degrees_per_step:g} degrees of rotation per "
            f"step, more than {thresholds.max_degrees_per_step:g}.",
            "Reduce degrees per step unless a coarser step is intended.",
            field="physics.time.degrees_per_step"))
    if has_stopping_issue(found):
        return tuple(found)
    try:
        setup = resolve(config, surfaces)
    except CaseSetupError as error:
        return (*found, _blocking("CASE_SETUP_UNRESOLVED", str(error),
                                  "Complete the physics settings; see the message."))
    estimate = setup.time.courant_estimate
    if estimate is not None and estimate > controls.max_courant:
        found.append(_issue(
            IssueSeverity.WARNING, "COURANT_ESTIMATE_HIGH",
            f"At {controls.degrees_per_step:g} degrees per step the estimated Courant number "
            f"is {estimate:.3g}, above the maximum {controls.max_courant:g}.",
            "Nothing is required: the solver shortens the step to keep the maximum, so a "
            "revolution takes more steps than the angle suggests.",
            explanation="Heuristic: (rotation speed x zone radius + fastest inlet speed) x "
                        "step / the zone's finest configured cell. It is not the solver's "
                        "Courant number; it understates (snapped and layer cells are "
                        "smaller), so treat it as a lower bound.",
            field="physics.time.max_courant", courant_estimate=estimate,
            smallest_cell=setup.time.smallest_cell, reference_speed=setup.time.reference_speed))
    return tuple(found)
