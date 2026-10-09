"""VAWT projects in the machine model, and back (docs/rotating_machinery.md section 12).

from_vawt maps every VawtProjectConfig field to a MachineProjectConfig field;
to_vawt restores the VAWT configuration exactly, so to_vawt(from_vawt(c)) == c.
An unchanged VAWT project therefore meshes through the existing VAWT case
generator with byte-identical dictionaries. Nothing here reads or writes files.

Patch types (owner decision D2): the inlet is INLET, the outlet OUTLET, the
four side faces SLIP and the rotor ROTATING_WALL. Meshing writes wall types as
snappy `wall` and every other type as `patch`, as VAWT does today.
"""

from __future__ import annotations

from machines.config import (
    BodyConfig,
    BodyRefinement,
    BoxDomain,
    CylinderZone,
    ExportConfig,
    MachineProjectConfig,
    MachineType,
    Motion,
    PatchConfig,
    PatchSource,
    PatchType,
    RotatingZone,
    SourceKind,
    StlSource,
    box_face,
)
from vawt.case_generator import CELL_ZONE_NAME
from vawt.config import (
    DomainConfig,
    DomainPatches,
    RefinementConfig,
    RotatingZoneConfig,
    RotorAxes,
    RotorGeometryConfig,
    VawtProjectConfig,
)
from vawt.config import ExportConfig as VawtExportConfig

ZONE_NAME = CELL_ZONE_NAME  # the VAWT rotating zone, as its cell zone is named


class NotVawtConvertible(ValueError):
    """The machine configuration has no exact VawtProjectConfig equivalent."""


def _domain_faces(axes: RotorAxes) -> dict[str, tuple[str, PatchType]]:
    """DomainPatches field -> (box face, patch type) for these axes."""
    flow, lateral, axis = axes.flow_axis, axes.lateral_axis, axes.axis
    return {
        "inlet": (box_face(flow, False), PatchType.INLET),
        "outlet": (box_face(flow, True), PatchType.OUTLET),
        "lateral_min": (box_face(lateral, False), PatchType.SLIP),
        "lateral_max": (box_face(lateral, True), PatchType.SLIP),
        "axial_min": (box_face(axis, False), PatchType.SLIP),
        "axial_max": (box_face(axis, True), PatchType.SLIP),
    }


def from_vawt(config: VawtProjectConfig) -> MachineProjectConfig:
    geometry, zone, refinement = config.geometry, config.rotating_zone, config.refinement
    rotor = geometry.patch_name  # the body is named after its patch
    patches = [PatchConfig(name=rotor, type=PatchType.ROTATING_WALL,
                           source=PatchSource(kind=SourceKind.BODY, ref=rotor))]
    domain = None
    if config.domain is not None:
        names = config.domain.patches
        patches += [
            PatchConfig(name=getattr(names, field), type=kind,
                        source=PatchSource(kind=SourceKind.DOMAIN_FACE, ref=face))
            for field, (face, kind) in _domain_faces(config.rotor).items()
        ]
        domain = BoxDomain(kind="BOX", bounds=config.domain.bounds,
                           cell_size=config.domain.cell_size,
                           location_in_mesh=config.domain.location_in_mesh)
    return MachineProjectConfig(
        schema_version=config.schema_version,
        project_name=config.project_name,
        openfoam_profile=config.openfoam_profile,
        machine=MachineType.VAWT,
        flow_axis=config.rotor.flow_axis,
        bodies=(BodyConfig(
            name=rotor,
            source=StlSource(source_path=geometry.source_path,
                             source_units=geometry.source_units, scale=geometry.scale,
                             rotation_deg=geometry.rotation_deg,
                             translation=geometry.translation),
            motion=Motion.ROTATING,
            zone=ZONE_NAME,
            refinement=BodyRefinement(
                min_level=refinement.blade_min_level, max_level=refinement.blade_max_level,
                extract_features=refinement.extract_features,
                feature_level=refinement.feature_level,
                feature_angle_deg=refinement.feature_angle_deg),
            layers=config.layers,
        ),),
        rotating_zones=(RotatingZone(
            name=ZONE_NAME,
            axis=config.rotor.axis,
            shape=CylinderZone(kind="CYLINDER", centre_u=zone.centre_u,
                               centre_v=zone.centre_v, axis_min=zone.axis_min,
                               axis_max=zone.axis_max, diameter=zone.diameter),
            cell_size=zone.cell_size,
            location_in_mesh=zone.location_in_mesh,
            interface=zone.interface,
            interface_level=refinement.interface_level,
        ),),
        domain=domain,
        patches=tuple(patches),
        wake=refinement.wake,
        snappy_quality=config.snappy_quality,
        quality=config.quality,
        max_global_cells=config.max_global_cells,
        export=ExportConfig(fluent_msh=config.export.fluent_msh),
    )


def to_vawt(config: MachineProjectConfig) -> VawtProjectConfig:
    """The VawtProjectConfig a VAWT machine configuration stands for.

    Raises NotVawtConvertible when the configuration uses anything the VAWT
    workflow cannot mesh: another machine, more than one body or zone, a
    stationary or split body, an annular or imported zone, a cylinder or
    imported domain, joints, or patches other than the VAWT set.
    """
    def refuse(reason: str) -> NotVawtConvertible:
        return NotVawtConvertible(f"Not a VAWT project configuration: {reason}.")

    if config.machine is not MachineType.VAWT:
        raise refuse(f"machine is {config.machine.value}")
    if len(config.bodies) != 1 or len(config.rotating_zones) != 1:
        raise refuse("VAWT has exactly one body and one rotating zone")
    body, zone = config.bodies[0], config.rotating_zones[0]
    shape = zone.shape
    if body.motion is not Motion.ROTATING or body.zone != zone.name:
        raise refuse("the body must rotate in the rotating zone")
    if not isinstance(shape, CylinderZone) or shape.hole_diameter is not None:
        raise refuse("the rotating zone must be a cylinder without a hole")
    if config.flow_axis is None or config.flow_axis is zone.axis:
        raise refuse("the flow axis must be set and differ from the rotor axis")
    if config.joints:
        raise refuse("VAWT has no joints")
    axes = RotorAxes(axis=zone.axis, flow_axis=config.flow_axis)

    rotor = config.patch_for(SourceKind.BODY, body.name)
    if rotor is None or rotor.type is not PatchType.ROTATING_WALL:
        raise refuse("the body needs one ROTATING_WALL patch")
    expected = 1
    domain = None
    if config.domain is not None:
        if not isinstance(config.domain, BoxDomain):
            raise refuse("the domain must be a box")
        names: dict[str, str] = {}
        for field, (face, kind) in _domain_faces(axes).items():
            patch = config.patch_for(SourceKind.DOMAIN_FACE, face)
            if patch is None or patch.type is not kind:
                raise refuse(f"face {face} needs one {kind.value} patch ({field})")
            names[field] = patch.name
        expected += len(names)
        domain = DomainConfig(bounds=config.domain.bounds, cell_size=config.domain.cell_size,
                              patches=DomainPatches(**names),
                              location_in_mesh=config.domain.location_in_mesh)
    if len(config.patches) != expected:
        raise refuse("patches other than the rotor and the six box faces")

    source, refinement = body.source, body.refinement
    return VawtProjectConfig(
        schema_version=config.schema_version,
        project_name=config.project_name,
        openfoam_profile=config.openfoam_profile,
        geometry=RotorGeometryConfig(
            source_path=source.source_path, source_units=source.source_units,
            scale=source.scale, rotation_deg=source.rotation_deg,
            translation=source.translation, patch_name=rotor.name),
        rotor=axes,
        rotating_zone=RotatingZoneConfig(
            centre_u=shape.centre_u, centre_v=shape.centre_v, axis_min=shape.axis_min,
            axis_max=shape.axis_max, diameter=shape.diameter, interface=zone.interface,
            cell_size=zone.cell_size, location_in_mesh=zone.location_in_mesh),
        domain=domain,
        refinement=RefinementConfig(
            blade_min_level=refinement.min_level, blade_max_level=refinement.max_level,
            interface_level=zone.interface_level, wake=config.wake,
            extract_features=refinement.extract_features,
            feature_level=refinement.feature_level,
            feature_angle_deg=refinement.feature_angle_deg),
        layers=body.layers,
        snappy_quality=config.snappy_quality,
        quality=config.quality,
        max_global_cells=config.max_global_cells,
        export=VawtExportConfig(fluent_msh=config.export.fluent_msh),
    )
