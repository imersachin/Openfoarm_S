"""G6: every physics field reaches the case files, and none reaches a meshing
input (invariant 14). The machines equivalent for meshing fields is
tests/unit/machines/test_machine_field_coverage.py, which declares `physics`
as case setup only and points here.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from case_setup.dictionaries import dictionaries
from case_setup.fields import fields
from case_setup.setup import resolve
from machines.assembly import MachineCaseGenerator
from machines.config import MachineProjectConfig
from machines.validation import load_bodies
from tests.fixtures.machines import configs

MESH = (("inlet", "patch"), ("outlet", "patch"), ("side", "patch"), ("rotor", "wall"),
        ("rotating_outer_stat", "cyclicAMI"), ("rotating_outer_rot", "cyclicAMI"))
# Enumerations: another valid choice. Discriminators ("kind") select the model
# variant; both variants are exercised below, so they are not mutated.
CHOICES = {"model": "LAMINAR", "direction": "NEGATIVE", "write_unit": "REVOLUTIONS"}
SKIPPED = {"kind"}


def variants() -> dict[str, dict[str, Any]]:
    fluid = {"density": 1.2, "kinematic_viscosity": 1.5e-5}
    turbulence = {"model": "K_OMEGA_SST", "intensity_percent": 5.0, "length_scale": 0.1}
    time = {"degrees_per_step": 0.8, "max_courant": 2.0, "end_revolutions": 5.0,
            "write_interval": 30.0, "write_unit": "DEGREES"}
    outlets = {"outlet": {"pressure_pa": 100.0}}
    return {
        "tip_speed_velocity": {
            "fluid": fluid, "turbulence": turbulence, "time": time, "outlets": outlets,
            "rotation": {"rotating": {"speed": {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 6.0,
                                                "radius": 0.5, "free_stream_speed": 5.0},
                                      "direction": "POSITIVE"}},
            "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0.1, 0.2)}}},
        "rpm_flow_rate": {
            "fluid": fluid, "turbulence": turbulence, "time": time, "outlets": outlets,
            "rotation": {"rotating": {"speed": {"kind": "RPM", "rpm": 573.0},
                                      "direction": "POSITIVE"}},
            "inlets": {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": 60.0}}},
    }


def leaves(data: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            yield from leaves(value, (*path, key))
    else:
        yield path, data


def mutated(physics: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    changed = copy.deepcopy(physics)
    target = changed
    for key in path[:-1]:
        target = target[key]
    leaf = path[-1]
    target[leaf] = CHOICES[leaf] if isinstance(value, str) else value * 1.37 + 0.01
    return changed


@pytest.fixture(scope="module")
def hawt(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return configs.hawt(tmp_path_factory.mktemp("geometry"))


def test_every_physics_field_changes_the_case_and_never_the_mesh(
        hawt: dict[str, Any]) -> None:
    base_config = MachineProjectConfig.model_validate(hawt)
    meshes, _ = load_bodies(base_config)

    def meshing(config: MachineProjectConfig) -> Any:
        cases = MachineCaseGenerator().render(config, [], meshes, typed=False)
        return [(c.name, c.dictionaries, c.surfaces) for c in cases.all], cases.retype

    def case(config: MachineProjectConfig) -> dict[str, str]:
        setup = resolve(config, [], MESH)
        return {**fields(setup), **dictionaries(setup)}

    mesh = meshing(base_config)
    failures, checked = [], 0
    for name, physics in variants().items():
        base = MachineProjectConfig.model_validate({**hawt, "physics": physics})
        assert meshing(base) == mesh, name
        before = case(base)
        for path, value in leaves(physics):
            if path[-1] in SKIPPED:
                continue
            checked += 1
            config = MachineProjectConfig.model_validate(
                {**hawt, "physics": mutated(physics, path, value)})
            if meshing(config) != mesh:
                failures.append(f"{name}: {'.'.join(map(str, path))} changes a meshing input")
            if case(config) == before:
                failures.append(f"{name}: {'.'.join(map(str, path))} reaches no case file")
    assert failures == []
    assert checked == 18 + 14  # leaves of the two variants, discriminators excepted


def test_all_physics_fields_are_exercised() -> None:
    """The variants set every field the models have (so a new field fails here
    until it is added to a variant)."""
    from case_setup.physics import (
        FlowRateInlet,
        FluidConfig,
        OutletCondition,
        RotationConfig,
        RpmSpeed,
        TimeConfig,
        TipSpeedRatio,
        TurbulenceConfig,
        VelocityInlet,
    )
    seen = {path[-1] for physics in variants().values() for path, _ in leaves(physics)}
    models = (FluidConfig, TurbulenceConfig, RotationConfig, RpmSpeed, TipSpeedRatio,
              VelocityInlet, FlowRateInlet, OutletCondition, TimeConfig)
    names = {f for m in models for f in m.model_fields} - {"speed", "velocity"}
    assert names <= seen | SKIPPED, sorted(names - seen - SKIPPED)
    assert {"x", "y", "z"} <= seen


def test_sections_of_the_physics_model_are_exercised() -> None:
    from case_setup.physics import PhysicsConfig
    for physics in variants().values():
        assert set(physics) == set(PhysicsConfig.model_fields)
    assert Path(__file__).name == "test_g6_physics_coverage.py"  # named in the coverage test
