"""Resolve a configuration into the values case setup writes (G6).

`resolve` turns the physics section and the meshed configuration into plain
numbers: the rotation of the zone, a condition per patch, inlet turbulence,
time controls and a Courant estimate. It assumes case_setup.validation found
no stopping issue; anything it still cannot resolve raises CaseSetupError.

`patch_kinds` takes the patch list from the assembled mesh's boundary file,
so every patch the solver will see gets a condition, and none is invented.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from case_setup.physics import (
    FlowRateInlet,
    PhysicsConfig,
    RotationDirection,
    RpmSpeed,
    SwirlInlet,
    TurbulenceModel,
    VelocityInlet,
    WriteUnit,
    courant,
    kinematic_pressure,
    rad_s_to_rpm,
    revolution_time,
    rpm_to_rad_s,
    step_for_degrees,
    tip_speed_omega,
    turbulence_k,
    turbulence_omega,
)
from machines.assembly import joint_levels
from machines.config import (
    BoxDomain,
    CylinderDomain,
    CylinderZone,
    ImportedZone,
    InterfaceJoint,
    MachineParts,
    MachineProjectConfig,
    Motion,
    PatchType,
    RotatingZone,
    SourceKind,
)
from machines.domains import SurfaceInfo
from machines.msh_import.interfaces import blockage, part_of, sides
from machines.msh_import.surface import PatchRadius
from machines.zones import split_patch
from vawt.config import Axis, plane_axes

CYCLIC_AMI = "cyclicAMI"
CYCLIC_ACMI = "cyclicACMI"  # an ACMI interface of a machine of several files (M2b-2)


class CaseSetupError(ValueError):
    """The configuration or the mesh cannot be set up (message for the user)."""


class PatchKind(StrEnum):
    """What a mesh patch gets in the fields (section 7)."""

    INLET = "INLET"
    OUTLET = "OUTLET"
    WALL = "WALL"  # noSlip: zero velocity in the absolute frame (K6, G0 G10)
    MOVING_WALL = "MOVING_WALL"  # moves with the rotating zone
    SLIP = "SLIP"
    CONSTRAINT = "CONSTRAINT"  # cyclicAMI: caseDicts/setConstraintTypes


_KINDS = {PatchType.INLET: PatchKind.INLET, PatchType.OUTLET: PatchKind.OUTLET,
          PatchType.WALL: PatchKind.WALL, PatchType.ROTATING_WALL: PatchKind.MOVING_WALL,
          PatchType.SLIP: PatchKind.SLIP}


@dataclass(frozen=True)
class Swirl:
    """A SWIRL inlet as written (cylindricalInletVelocity about the zone's
    axis): radial speed -Q/A (towards the axis), tangential Vr / tan(angle) at
    the inlet's mean radius, as the rpm of a solid-body swirl (signed like
    omega: positive is right-handed about +axis, verified on v2512, C1)."""

    origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    radial_velocity: float  # m/s, negative: inwards
    tangential_velocity: float  # m/s at `radius`, signed with the direction
    radius: float  # m: the inlet's area-weighted mean radius, measured from the mesh
    rpm: float
    flow_angle_deg: float
    label: str = "approximate (from vane trailing-edge angle)"


@dataclass(frozen=True)
class Inlet:
    velocity: tuple[float, float, float] | None  # VELOCITY inlet
    flow_rate: float | None  # FLOW_RATE or SWIRL inlet, m^3/s
    speed: float  # m/s: |U|, or flow rate / inlet area
    k: float
    omega: float
    swirl: Swirl | None = None  # a SWIRL inlet (C1)


@dataclass(frozen=True)
class Rotation:
    zone: str  # the cell zone
    origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    omega: float  # rad/s, signed: positive turns right-handed about `axis` (G0)
    radius: float | None  # m: outer radius of the zone (Courant estimate)


@dataclass(frozen=True)
class TimeControls:
    step_for_angle: float  # s: the configured degrees per step
    initial_step: float  # s: the first step, also within the Courant estimate
    end_time: float  # s
    write_interval: float  # s
    max_courant: float
    courant_estimate: float | None  # at step_for_angle; heuristic
    smallest_cell: float | None  # m; heuristic, as used for the estimate
    reference_speed: float | None  # m/s; heuristic, as used for the estimate


# Above this mesh non-orthogonality (deg), case setup adds correctors (owner, after
# M2a; docs/msh_import_notes.md 13.9): GV (81.6 deg) diverged in 10 pimpleFoam
# steps with none and ran cleanly with these.
NON_ORTHO_LIMIT_DEG = 70.0
NON_ORTHO_CORRECTORS = 2
PRESSURE_CORRECTORS = 2


@dataclass(frozen=True)
class NonOrthogonality:
    """The assembled mesh's max non-orthogonality (checkMesh) and the cell zone
    holding it, when known."""

    max_deg: float
    zone: str | None = None

    @property
    def corrected(self) -> bool:
        return self.max_deg > NON_ORTHO_LIMIT_DEG

    @property
    def reason(self) -> str:
        where = f" in {self.zone}" if self.zone else ""
        return (f"max non-orthogonality {self.max_deg:.1f} deg{where} (above "
                f"{NON_ORTHO_LIMIT_DEG:g} deg): {NON_ORTHO_CORRECTORS} non-orthogonal "
                f"correctors, nCorrectors {PRESSURE_CORRECTORS}")


@dataclass(frozen=True)
class CaseSetup:
    patches: tuple[tuple[str, PatchKind], ...]  # every mesh patch, in boundary order
    rotation: Rotation
    inlets: dict[str, Inlet]
    outlets: dict[str, float]  # kinematic pressure, m^2/s^2
    physics: PhysicsConfig
    time: TimeControls
    internal_k: float = 0.0
    internal_omega: float = 0.0
    non_orthogonality: NonOrthogonality | None = None  # None: not measured
    # An imported machine's case (C1): a fixed step, the robust PIMPLE settings,
    # its start-up and the run order (Allrun). An STL-built case is as before.
    imported: bool = False

    @property
    def turbulent(self) -> bool:
        return self.physics.turbulence.model is TurbulenceModel.K_OMEGA_SST

    @property
    def corrected(self) -> bool:
        """Whether the solution settings add correctors for non-orthogonality."""
        return self.non_orthogonality is not None and self.non_orthogonality.corrected


_NON_ORTHO = re.compile(r"Mesh non-orthogonality Max:\s*(\S+)")


def non_orthogonality(check_mesh_log: str,
                      by_zone: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
                      ) -> NonOrthogonality | None:
    """The max non-orthogonality of a checkMesh log, and the zone of the worst
    value from an imported mesh's per-zone rows (reports/mesh_quality_zones.json
    "non-orthogonality"); None when the log has no value."""
    found = _NON_ORTHO.search(check_mesh_log)
    if found is None:
        return None
    try:
        value = float(found[1])
    except ValueError:
        return None
    rows = [r for r in (by_zone or {}).get("non-orthogonality", ())
            if isinstance(r.get("worst"), int | float)]
    worst = max(rows, key=lambda r: float(r["worst"]), default=None)  # type: ignore[arg-type]
    return NonOrthogonality(value, str(worst["zone"]) if worst else None)


# --- patches ------------------------------------------------------------------------------

def configured_kinds(config: MachineProjectConfig) -> dict[str, PatchKind]:
    """Kind of every configured patch and every split body's rotating part."""
    kinds = {p.name: _KINDS[p.type] for p in config.patches}
    kinds.update({split_patch(b): PatchKind.MOVING_WALL for b in config.bodies
                  if b.motion is Motion.SPLIT})
    mesh = config.imported_mesh
    if isinstance(mesh, MachineParts):  # an ACMI interface's walls (M2b-2)
        turning = {z.cell_zone for z in mesh.zones}
        for interface in mesh.interfaces:
            if interface.joint is not InterfaceJoint.ACMI:
                continue
            for side, patches in zip(sides(interface), (interface.first, interface.second),
                                     strict=True):
                kinds[blockage(side)] = (PatchKind.MOVING_WALL if part_of(patches[0])
                                         in turning else PatchKind.WALL)
    return kinds


def patch_kinds(config: MachineProjectConfig,
                boundary: Sequence[tuple[str, str]]) -> tuple[tuple[str, PatchKind], ...]:
    """(name, kind) for every patch of the mesh, from its (name, type) list.
    A patch the configuration does not type is a CaseSetupError."""
    kinds = configured_kinds(config)
    found: list[tuple[str, PatchKind]] = []
    untyped: list[str] = []
    for name, kind in boundary:
        if kind in (CYCLIC_AMI, CYCLIC_ACMI):
            found.append((name, PatchKind.CONSTRAINT))
        elif name in kinds:
            found.append((name, kinds[name]))
        else:
            untyped.append(name)
    if untyped:
        raise CaseSetupError(f"The mesh has patches without a configured type: "
                             f"{', '.join(untyped)}.")
    return tuple(found)


# --- geometry for the estimates -----------------------------------------------------------

def _unit(axis: Axis) -> tuple[float, float, float]:
    return (float(axis is Axis.X), float(axis is Axis.Y), float(axis is Axis.Z))


def _zone_origin(zone: RotatingZone) -> tuple[float, float, float]:
    shape = zone.shape
    if isinstance(shape, ImportedZone):
        return (shape.origin.x, shape.origin.y, shape.origin.z)
    point = {zone.axis: (shape.axis_min + shape.axis_max) / 2}
    u, v = plane_axes(zone.axis)
    point[u], point[v] = shape.centre_u, shape.centre_v
    return (point[Axis.X], point[Axis.Y], point[Axis.Z])


def _zone_surface(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
                  zone: RotatingZone) -> SurfaceInfo | None:
    owner = f"rotating_zones.{config.rotating_zones.index(zone)}"
    return next((s for s in surfaces if s.owner == owner and s.readable), None)


def zone_radius(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
                zone: RotatingZone) -> float | None:
    """The zone's outer radius about its axis: an upper bound of the rotor tip."""
    shape = zone.shape
    if isinstance(shape, CylinderZone):
        return shape.diameter / 2
    surface = _zone_surface(config, surfaces, zone)
    if surface is None:
        return None
    points = np.concatenate([np.asarray(r.mesh.vertices) for r in surface.regions])
    axis = np.array(_unit(zone.axis))
    relative = points - np.array(_zone_origin(zone))
    radial = relative - np.outer(relative @ axis, axis)
    return float(np.linalg.norm(radial, axis=1).max())


def smallest_zone_cell(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
                       zone: RotatingZone) -> float:
    """The zone's cell size at its finest configured level (heuristic)."""
    levels = [b.refinement.max_level for b in config.bodies
              if b.zone == zone.name and b.motion is not Motion.STATIONARY]
    shape = zone.shape
    if isinstance(shape, CylinderZone):
        levels.append(zone.interface_level)
    else:
        levels += list(shape.refinement.values())
        surface = _zone_surface(config, surfaces, zone)
        joints = joint_levels(config, [s for s in surfaces if s.readable])
        if surface is not None:
            levels += [joints[r.name] for r in surface.regions if r.name in joints]
    return zone.cell_size / 2 ** max(levels, default=0)


def inlet_area(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
               patch: str, mesh_areas: Mapping[str, float] | None = None) -> float | None:
    """Area of an inlet patch, from its imported region or generated face, or,
    for a patch of an imported mesh, `mesh_areas` (patch -> m^2, measured from
    the mesh: machines.msh_import.checks.patch_areas; None before the import)."""
    source = next((p.source for p in config.patches if p.name == patch), None)
    if source is None:
        return None
    if source.kind is SourceKind.MESH_PATCH:
        return (mesh_areas or {}).get(patch)
    if source.kind is SourceKind.REGION:
        return next((r.area for s in surfaces if s.readable for r in s.regions
                     if r.name == source.ref), None)
    domain = config.domain
    if source.kind is SourceKind.DOMAIN_FACE and isinstance(domain, BoxDomain):
        lo, hi = domain.bounds.minimum, domain.bounds.maximum
        size = {Axis.X: hi.x - lo.x, Axis.Y: hi.y - lo.y, Axis.Z: hi.z - lo.z}
        normal = Axis(source.ref.split("_")[0])
        u, v = plane_axes(normal)
        return size[u] * size[v]
    if source.kind is SourceKind.DOMAIN_FACE and isinstance(domain, CylinderDomain):
        radius = domain.diameter / 2
        if source.ref == "side":
            return 2 * math.pi * radius * (domain.axis_max - domain.axis_min)
        return math.pi * radius**2
    return None


# --- resolution ---------------------------------------------------------------------------

SWIRL_FACING_MIN = 0.9  # a swirl inlet faces the axis over at least 90 % of its area


def _swirl(name: str, inlet: SwirlInlet, speed: float, rotation: Rotation,
           radius: PatchRadius | None) -> Swirl:
    """The swirl inlet's values; `radius` None (not imported yet) gives a
    placeholder radius of 1 m, used by the checks only."""
    if inlet.direction is None:
        raise CaseSetupError(f"Inlet {name}: the swirl direction is not set.")
    if radius is not None and radius.facing < SWIRL_FACING_MIN:
        raise CaseSetupError(
            f"Inlet {name} faces the axis over {radius.facing:.0%} of its area: a swirl inlet "
            "needs an inlet around the axis (a runner's inflow), with radial inflow.")
    r = radius.radius if radius is not None else 1.0
    sign = 1.0 if inlet.direction is RotationDirection.POSITIVE else -1.0
    tangential = sign * speed / math.tan(math.radians(inlet.flow_angle_deg))
    return Swirl(rotation.origin, rotation.axis, -speed, tangential, r,
                 rad_s_to_rpm(tangential / r), inlet.flow_angle_deg)


def _speed(inlet: VelocityInlet | FlowRateInlet | SwirlInlet, area: float | None) -> float:
    if isinstance(inlet, VelocityInlet):
        v = inlet.velocity
        return math.sqrt(v.x**2 + v.y**2 + v.z**2)
    if area is None or area <= 0:
        raise CaseSetupError("A flow-rate inlet needs a patch with a known area.")
    return inlet.volumetric_flow_rate / area


def free_stream_speed(physics: PhysicsConfig) -> float | None:
    """The speed of the one velocity inlet, or None when there is not exactly one."""
    velocities = [i for i in physics.inlets.values() if isinstance(i, VelocityInlet)]
    if len(velocities) != 1 or len(physics.inlets) != 1:
        return None
    return _speed(velocities[0], None)


def angular_speed(physics: PhysicsConfig, zone: str) -> float:
    """Signed omega (rad/s) of a zone; positive is right-handed about +axis."""
    rotation = physics.rotation[zone]
    speed = rotation.speed
    if isinstance(speed, RpmSpeed):
        omega = rpm_to_rad_s(speed.rpm)
    else:
        u = speed.free_stream_speed or free_stream_speed(physics)
        if u is None:
            raise CaseSetupError(f"Zone {zone}: a tip-speed ratio needs the free-stream "
                                 "speed, or exactly one velocity inlet.")
        omega = tip_speed_omega(speed.tip_speed_ratio, u, speed.radius)
    if rotation.direction is None:
        raise CaseSetupError(f"Zone {zone}: the rotation direction is not set.")
    return omega if rotation.direction is RotationDirection.POSITIVE else -omega


def resolve(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
            boundary: Sequence[tuple[str, str]] | None = None, *,
            mesh_radius: float | None = None,
            mesh_areas: Mapping[str, float] | None = None,
            mesh_radii: Mapping[str, PatchRadius] | None = None) -> CaseSetup:
    """Everything the case files need. `boundary`: the mesh's (name, type)
    list; None uses the configured patches (for estimates before meshing).
    `mesh_radius`: an imported mesh's zone radius, computed from the zone
    (M1-MSH); None before meshing. `mesh_areas`: an imported mesh's patch
    areas, measured from the mesh (inlet_area). `mesh_radii`: its patches'
    radii about the zone's axis (a swirl inlet, C1); None before meshing."""
    physics = config.physics
    if physics is None:
        raise CaseSetupError("The project has no physics settings.")
    names = config.zone_names()
    if len(names) != 1:
        raise CaseSetupError("Case setup supports one rotating zone (G6, P5).")
    name = names[0]
    if name not in physics.rotation:
        raise CaseSetupError(f"Zone {name} has no rotation settings.")
    omega = angular_speed(physics, name)
    if omega == 0:
        raise CaseSetupError(f"Zone {name}: the rotation speed is zero.")
    mesh = config.imported_mesh
    cell: float | None = None
    if mesh is not None:  # the cell zone of an imported mesh (M1-MSH)
        imported = mesh.zones[0]
        origin = (imported.origin.x, imported.origin.y, imported.origin.z)
        rotation = Rotation(imported.cell_zone, origin, _unit(imported.axis), omega,
                            mesh_radius)
    else:
        zone = config.rotating_zones[0]
        radius = zone_radius(config, surfaces, zone)
        rotation = Rotation(zone.name, _zone_origin(zone), _unit(zone.axis), omega, radius)
        cell = smallest_zone_cell(config, surfaces, zone)

    if boundary is None:
        boundary = [(n, "patch") for n in configured_kinds(config)]
    patches = patch_kinds(config, boundary)

    turbulence = physics.turbulence
    sst = turbulence.model is TurbulenceModel.K_OMEGA_SST
    if sst and turbulence.length_scale is None:
        raise CaseSetupError("k-omega SST needs the inlet turbulence length scale.")
    inlets: dict[str, Inlet] = {}
    for name, kind in patches:
        if kind is not PatchKind.INLET:
            continue
        condition = physics.inlets.get(name)
        if condition is None:
            raise CaseSetupError(f"Inlet {name} has no inlet value.")
        speed = _speed(condition, inlet_area(config, surfaces, name, mesh_areas))
        if speed == 0:
            raise CaseSetupError(f"Inlet {name} has no inflow (zero velocity or flow rate).")
        k =turbulence_k(speed, turbulence.intensity_percent) if sst else 0.0
        w = turbulence_omega(k, turbulence.length_scale or 1.0) if sst else 0.0
        velocity = ((condition.velocity.x, condition.velocity.y, condition.velocity.z)
                    if isinstance(condition, VelocityInlet) else None)
        flow = (condition.volumetric_flow_rate
                if isinstance(condition, FlowRateInlet | SwirlInlet) else None)
        swirl = None
        if isinstance(condition, SwirlInlet):
            if mesh is None:
                raise CaseSetupError(f"Inlet {name}: a swirl inlet is for an imported mesh.")
            if mesh_radii is not None and name not in mesh_radii:
                raise CaseSetupError(f"Inlet {name}: its radius could not be measured from "
                                     "the mesh (the patch has no faces in it).")
            swirl = _swirl(name, condition, speed, rotation,
                           None if mesh_radii is None else mesh_radii[name])
        inlets[name] = Inlet(velocity, flow, speed, k, w, swirl)
    outlets: dict[str, float] = {}
    for name, kind in patches:
        if kind is not PatchKind.OUTLET:
            continue
        if name not in physics.outlets:
            raise CaseSetupError(f"Outlet {name} has no outlet pressure.")
        outlets[name] = kinematic_pressure(physics.outlets[name].pressure_pa,
                                           physics.fluid.density)
    if not inlets or not outlets:
        raise CaseSetupError("The mesh needs at least one inlet and one outlet.")
    fastest = max(inlets.values(), key=lambda i: i.speed)

    controls = physics.time
    angle_step = step_for_degrees(controls.degrees_per_step, omega)
    # An imported mesh has no configured cell size: no Courant estimate.
    radius = rotation.radius
    reference = None if radius is None or cell is None else abs(omega) * radius + fastest.speed
    estimate = None if reference is None or cell is None else courant(reference, angle_step,
                                                                      cell)
    initial = angle_step
    if estimate is not None and estimate > controls.max_courant:
        initial = angle_step * controls.max_courant / estimate
    period = revolution_time(omega)
    write = (controls.write_interval * period if controls.write_unit is WriteUnit.REVOLUTIONS
             else step_for_degrees(controls.write_interval, omega))
    time = TimeControls(angle_step, initial, controls.end_revolutions * period, write,
                        controls.max_courant, estimate, cell if reference else None, reference)
    return CaseSetup(patches, rotation, inlets, outlets, physics, time,
                     internal_k=fastest.k, internal_omega=fastest.omega,
                     imported=mesh is not None)
