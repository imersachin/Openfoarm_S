"""Checks before case setup (docs/rotating_machinery.md section 10, G6 rows).

Physics findings never stop meshing (invariant 14): the pipeline meshes, then
skips case setup when one of these is ERROR or BLOCKING. Thresholds are
configuration, never constants.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from case_setup.physics import (
    FlowRateInlet,
    RpmSpeed,
    SwirlInlet,
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
from machines.config import MachineProjectConfig, SourceKind
from machines.domains import SurfaceInfo


@dataclass(frozen=True)
class CaseSetupThresholds:
    # A rotation angle per step above this is a WARNING. Provisional: 1 degree
    # per step is what every G0 solver run used (notes section 4).
    max_degrees_per_step: float = 1.0
    # An imported machine (C1): its labelled choices go to 2 deg a step (slow
    # effects), stable on v2512 over a blade pitch.
    max_degrees_per_step_imported: float = 2.0


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


def area_after_import(config: MachineProjectConfig, patch: str,
                      mesh_areas: Mapping[str, float] | None) -> bool:
    """A patch of an imported mesh whose area is measured once it is imported
    (mesh_areas None: not imported yet)."""
    source = next((p.source for p in config.patches if p.name == patch), None)
    return (mesh_areas is None and source is not None
            and source.kind is SourceKind.MESH_PATCH)


def _boundary_issues(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
                     mesh_areas: Mapping[str, float] | None) -> list[Issue]:
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
        zero = (inlet.volumetric_flow_rate == 0 if isinstance(inlet, FlowRateInlet | SwirlInlet)
                else (inlet.velocity.x, inlet.velocity.y, inlet.velocity.z) == (0, 0, 0))
        if isinstance(inlet, SwirlInlet):
            found += _swirl_issues(config, name, inlet)
        if name in inlets and zero:
            # k = omega = 0 at the inlet: k-omega SST divides by zero (SIGFPE).
            found.append(_blocking("INLET_VALUE_ZERO", f"Inlet {name} has no inflow (zero "
                                   "velocity or flow rate).", "Give a non-zero inflow, or "
                                   "make the patch a WALL.", field=f"physics.inlets.{name}"))
        if name not in inlets or not isinstance(inlet, FlowRateInlet | SwirlInlet) or inlet_area(
                config, surfaces, name, mesh_areas):
            continue
        if area_after_import(config, name, mesh_areas):
            found.append(_issue(
                IssueSeverity.INFO, "INLET_AREA_AFTER_IMPORT", f"Inlet {name}: the flow rate "
                "needs the inlet's area, which is measured from the mesh when it is imported.",
                "Nothing to do: the measured area is in the import report and the case "
                "setup report.", field=f"physics.inlets.{name}"))
        elif config.imported_mesh is not None:
            found.append(_blocking("INLET_AREA_UNKNOWN", f"Inlet {name}: the flow rate needs "
                                   "the inlet's area, which could not be measured from the "
                                   "mesh (the patch has no faces in it).",
                                   "Check the patch in the mesh file, or use a velocity "
                                   "inlet.", field=f"physics.inlets.{name}"))
        else:
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


def _swirl_issues(config: MachineProjectConfig, name: str, inlet: SwirlInlet) -> list[Issue]:
    """A swirl inlet (C1): imported meshes only, its direction set, and a
    WARNING when the runner turns against it (a turbine's runner turns with
    the swirl the guide vanes give)."""
    path = f"physics.inlets.{name}"
    if config.imported_mesh is None:
        return [_blocking("SWIRL_INLET_NOT_IMPORTED", f"Inlet {name}: a swirl inlet is for an "
                          "imported machine without its guide vanes.", "Use a velocity or "
                          "flow-rate inlet.", field=path)]
    if inlet.direction is None:
        return [_blocking("SWIRL_DIRECTION_NOT_SET", f"Inlet {name}: the swirl's direction is "
                          "not set; it is never assumed.", "Choose the direction the guide "
                          "vanes turn the flow (right-hand rule about the zone's axis).",
                          field=f"{path}.direction")]
    physics = config.physics
    assert physics is not None
    against = [zone for zone, rotation in sorted(physics.rotation.items())
               if rotation.direction is not None and rotation.direction is not inlet.direction]
    if not against:
        return []
    return [_issue(
        IssueSeverity.WARNING, "RUNNER_AGAINST_SWIRL",
        f"Inlet {name} swirls {inlet.direction.value} about the axis, but {', '.join(against)} "
        "turns the other way.",
        "Check the runner's direction and the swirl's: in a turbine the runner turns with "
        "the swirl the guide vanes give.",
        explanation="Both directions follow the right-hand rule about the zone's axis.",
        field=f"{path}.direction", zones=against)]


def _guide_vane_issues(config: MachineProjectConfig) -> list[Issue]:
    """The operating point's guide vane opening against the mesh's (C1)."""
    physics = config.physics
    assert physics is not None
    vanes = physics.guide_vanes
    if vanes is None or None in (vanes.operating_deg, vanes.mesh_min_deg, vanes.mesh_max_deg):
        return []
    assert vanes.operating_deg is not None and vanes.mesh_min_deg is not None
    assert vanes.mesh_max_deg is not None
    if vanes.mesh_min_deg > vanes.mesh_max_deg:
        return [_blocking("GUIDE_VANE_RANGE_INVALID", "The mesh's guide vane opening range "
                          f"runs from {vanes.mesh_min_deg:g} down to {vanes.mesh_max_deg:g} deg.",
                          "Give the smaller value first.", field="physics.guide_vanes")]
    if vanes.mesh_min_deg <= vanes.operating_deg <= vanes.mesh_max_deg:
        return []
    source = f" ({vanes.mesh_source})" if vanes.mesh_source else ""
    return [_issue(
        IssueSeverity.WARNING, "GUIDE_VANE_OPENING_MISMATCH",
        f"The operating point is for a guide vane opening of {vanes.operating_deg:g} deg; the "
        f"mesh's is {vanes.mesh_min_deg:g}-{vanes.mesh_max_deg:g} deg{source}.",
        "Use the operating point of the mesh's guide vane opening, or a mesh of this one.",
        explanation="Openings from fully closed. The flow the guide vanes give depends on "
                    "their opening: another operating point's speed and flow rate do not fit "
                    "this mesh.", field="physics.guide_vanes")]


def check_physics(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
                  thresholds: CaseSetupThresholds | None = None, *,
                  mesh_areas: Mapping[str, float] | None = None) -> tuple[Issue, ...]:
    """Every finding about the physics section. Empty when it is absent.
    `mesh_areas`: an imported mesh's patch areas, measured from the mesh; None
    before the import, when a flow-rate inlet's area is not known yet (INFO)."""
    if config.physics is None:
        return ()
    thresholds = thresholds or CaseSetupThresholds()
    controls = config.physics.time
    found = [*_rotation_issues(config), *_boundary_issues(config, surfaces, mesh_areas),
             *_guide_vane_issues(config)]
    largest = (thresholds.max_degrees_per_step if config.imported_mesh is None
               else thresholds.max_degrees_per_step_imported)
    if controls.degrees_per_step > largest:
        found.append(_issue(
            IssueSeverity.WARNING, "ROTATION_STEP_LARGE",
            f"The time step allows {controls.degrees_per_step:g} degrees of rotation per "
            f"step, more than {largest:g}.",
            "Reduce degrees per step unless a coarser step is intended.",
            field="physics.time.degrees_per_step"))
    if has_stopping_issue(found):
        return tuple(found)
    # An area measured only after the import gets a placeholder here: the setup
    # is resolved to check the other settings, and an imported mesh has no
    # Courant estimate, so nothing depends on the value.
    try:
        if config.imported_mesh is None:
            setup = resolve(config, surfaces)
        else:
            setup = resolve(config, surfaces, mesh_areas={
                **{n: 1.0 for n in config.physics.inlets
                   if area_after_import(config, n, mesh_areas)}, **(mesh_areas or {})})
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
