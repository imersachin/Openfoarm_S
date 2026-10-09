"""VAWT projects in the machine model and back (spec section 12, decisions D2 and D6)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from machines.config import (
    BodyConfig,
    CylinderDomain,
    MachineProjectConfig,
    MachineType,
    Motion,
    PatchConfig,
    PatchSource,
    PatchType,
    SourceKind,
)
from machines.vawt_migration import ZONE_NAME, NotVawtConvertible, from_vawt, to_vawt
from tests.fixtures.vawt.drafts import preset_draft
from vawt.case_generator import VawtCaseGenerator
from vawt.config import VawtProjectConfig
from vawt.presets import PresetKind

AXES = [(a, f) for a in "xyz" for f in "xyz" if a != f]


def vawt(directory: Path, edits: dict[str, Any] | None = None, **kwargs: Any) -> VawtProjectConfig:
    data = preset_draft(directory, **kwargs)
    for path, value in (edits or {}).items():
        *parents, leaf = path.split(".")
        target = data
        for key in parents:
            target = target.setdefault(key, {})
        target[leaf] = value
    return VawtProjectConfig.model_validate(data)


@pytest.mark.parametrize("include_domain", [True, False])
@pytest.mark.parametrize("kind", list(PresetKind))
@pytest.mark.parametrize("axis, flow", AXES)
def test_every_preset_draft_round_trips_exactly(tmp_path: Path, axis: str, flow: str,
                                                kind: PresetKind, include_domain: bool) -> None:
    config = vawt(tmp_path, axis=axis, flow_axis=flow, kind=kind, include_domain=include_domain)

    machine = from_vawt(config)

    assert to_vawt(machine) == config
    assert to_vawt(machine).model_dump(mode="json") == config.model_dump(mode="json")
    assert MachineProjectConfig.model_validate(machine.model_dump(mode="json")) == machine


EDITS: dict[str, dict[str, Any]] = {
    "cell_zone_with_domain": {"rotating_zone.interface": "CELL_ZONE"},
    "absolute_layers": {"layers.enabled": True, "layers.sizing": "ABSOLUTE",
                        "layers.first_layer_thickness": 1e-3, "layers.min_thickness_m": 1e-4,
                        "layers.count": 5, "layers.expansion_ratio": 1.3},
    "features": {"refinement.extract_features": True, "refinement.feature_level": 3,
                 "refinement.feature_angle_deg": 45.0},
    "levels": {"refinement.blade_min_level": 2, "refinement.blade_max_level": 4,
               "refinement.interface_level": 3},
    "no_wake": {"refinement.wake": None},
    "patch_names": {"geometry.patch_name": "turbine",
                    "domain.patches": {"inlet": "in", "outlet": "out", "lateral_min": "s1",
                                       "lateral_max": "s2", "axial_min": "floor",
                                       "axial_max": "top"}},
    "transform": {"geometry.source_units": "mm", "geometry.scale": 2.5,
                  "geometry.rotation_deg": {"x": 10.0, "y": 0.0, "z": -5.0},
                  "geometry.translation": {"x": 0.1, "y": -0.2, "z": 0.3}},
    "quality": {"quality.max_non_orthogonality": 70.0,
                "snappy_quality.max_internal_skewness": 3.5, "max_global_cells": 123_456},
    "export_and_profile": {"export.fluent_msh": False,
                           "openfoam_profile": "openfoam_foundation"},
}


@pytest.mark.parametrize("name", list(EDITS))
def test_edited_projects_round_trip_exactly(tmp_path: Path, name: str) -> None:
    config = vawt(tmp_path, EDITS[name])

    assert to_vawt(from_vawt(config)) == config


@pytest.mark.parametrize("include_domain", [True, False])
def test_meshing_dictionaries_are_byte_identical(tmp_path: Path, include_domain: bool) -> None:
    config = vawt(tmp_path, include_domain=include_domain)
    generator = VawtCaseGenerator()

    original = generator.render(config)

    assert original
    assert generator.render(to_vawt(from_vawt(config))) == original


def test_patch_types_and_sources(tmp_path: Path) -> None:
    # Rotor axis z, flow x: the lateral axis is y. Decision D2.
    machine = from_vawt(vawt(tmp_path))

    assert [(p.name, p.type, p.source.kind, p.source.ref) for p in machine.patches] == [
        ("rotor", PatchType.ROTATING_WALL, SourceKind.BODY, "rotor"),
        ("inlet", PatchType.INLET, SourceKind.DOMAIN_FACE, "x_min"),
        ("outlet", PatchType.OUTLET, SourceKind.DOMAIN_FACE, "x_max"),
        ("lateral_min", PatchType.SLIP, SourceKind.DOMAIN_FACE, "y_min"),
        ("lateral_max", PatchType.SLIP, SourceKind.DOMAIN_FACE, "y_max"),
        ("axial_min", PatchType.SLIP, SourceKind.DOMAIN_FACE, "z_min"),
        ("axial_max", PatchType.SLIP, SourceKind.DOMAIN_FACE, "z_max"),
    ]


def test_migrated_project_shape(tmp_path: Path) -> None:
    config = vawt(tmp_path, axis="y", flow_axis="z")
    machine = from_vawt(config)
    body, zone = machine.bodies[0], machine.rotating_zones[0]

    assert machine.machine is MachineType.VAWT and machine.flow_axis.value == "z"
    assert (body.name, body.motion, body.zone) == ("rotor", Motion.ROTATING, ZONE_NAME)
    assert zone.axis.value == "y" and zone.shape.kind == "CYLINDER"
    assert machine.wake == config.refinement.wake
    assert machine.export.fluent_msh is True


def test_rotor_only_project_has_no_domain_patches(tmp_path: Path) -> None:
    machine = from_vawt(vawt(tmp_path, include_domain=False))

    assert machine.domain is None
    assert [p.name for p in machine.patches] == ["rotor"]
    assert machine.rotating_zones[0].interface.value == "CELL_ZONE"


def _changed(config: MachineProjectConfig, **update: Any) -> MachineProjectConfig:
    return config.model_copy(update=update)


def test_to_vawt_refuses_what_vawt_cannot_mesh(tmp_path: Path) -> None:
    machine = from_vawt(vawt(tmp_path))
    body, zone = machine.bodies[0], machine.rotating_zones[0]
    pole = BodyConfig(name="pole", source=body.source, motion=Motion.STATIONARY)
    hole = zone.model_copy(update={"shape": zone.shape.model_copy(
        update={"hole_diameter": 0.1})})
    cylinder = CylinderDomain(kind="CYLINDER", axis=zone.axis, centre_u=0, centre_v=0,
                              axis_min=-3, axis_max=3, diameter=6, cell_size=0.1,
                              location_in_mesh=machine.domain.location_in_mesh)
    extra = PatchConfig(name="extra", type=PatchType.WALL,
                        source=PatchSource(kind=SourceKind.BODY, ref="pole"))
    patches = list(machine.patches)
    swapped = [p.model_copy(update={"type": PatchType.OUTLET}) if p.name == "inlet" else p
               for p in patches]
    wall_rotor = [p.model_copy(update={"type": PatchType.WALL}) if p.name == "rotor" else p
                  for p in patches]

    refused = [
        _changed(machine, machine=MachineType.VAWT_POLE),
        _changed(machine, bodies=(body, pole)),
        _changed(machine, bodies=(body.model_copy(update={"motion": Motion.SPLIT}),)),
        _changed(machine, rotating_zones=(hole,)),
        _changed(machine, flow_axis=None),
        _changed(machine, flow_axis=zone.axis),
        _changed(machine, domain=cylinder),
        _changed(machine, patches=tuple(swapped)),
        _changed(machine, patches=tuple(wall_rotor)),
        _changed(machine, patches=(*patches, extra)),
        _changed(machine, patches=tuple(patches[:-1])),
    ]
    for config in refused:
        with pytest.raises(NotVawtConvertible):
            to_vawt(config)
    assert to_vawt(machine) is not None


def test_base_vawt_data_is_untouched(tmp_path: Path) -> None:
    config = vawt(tmp_path)
    before = copy.deepcopy(config.model_dump())

    from_vawt(config)

    assert config.model_dump() == before
