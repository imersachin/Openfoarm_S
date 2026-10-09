"""MachineProjectConfig model rules and parse_config's named issues (G1)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from machines.config import (
    MACHINE_SCHEMA_VERSION,
    CylinderZone,
    DomainKind,
    ImportedZone,
    MachineProjectConfig,
    Motion,
    PatchType,
)
from machines.validation import parse_config
from tests.fixtures.machines import configs


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return configs.hawt(tmp_path)


@pytest.fixture
def francis(tmp_path: Path) -> dict[str, Any]:
    return configs.francis(tmp_path)


def edited(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Copy with the value at a dotted path replaced (DELETE removes it)."""
    data = copy.deepcopy(data)
    *parents, leaf = path.split(".")
    target: Any = data
    for key in parents:  # sections left to model defaults may be absent
        target = target[int(key)] if isinstance(target, list) else target.setdefault(key, {})
    if value is DELETE:
        del target[int(leaf) if isinstance(target, list) else leaf]
    elif isinstance(target, list):
        target[int(leaf)] = value
    else:
        target[leaf] = value
    return data


DELETE = object()


def errors(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        MachineProjectConfig.model_validate(data)
    return [".".join(map(str, e["loc"])) + ":" + e["type"] for e in caught.value.errors()]


def codes(data: dict[str, Any]) -> list[str]:
    config, issues = parse_config(data)
    assert config is None
    return [i.code for i in issues]


def test_g0_configurations_are_valid(tmp_path: Path) -> None:
    hawt = MachineProjectConfig.model_validate(configs.hawt(tmp_path / "h"))
    francis = MachineProjectConfig.model_validate(configs.francis(tmp_path / "f"))
    pole = MachineProjectConfig.model_validate(
        configs.pole(tmp_path / "p", motion="STATIONARY", hole_diameter=0.16))

    assert hawt.schema_version == MACHINE_SCHEMA_VERSION
    assert hawt.domain is not None and hawt.domain.kind == DomainKind.CYLINDER
    assert isinstance(francis.rotating_zones[0].shape, ImportedZone)
    assert francis.bodies == () and len(francis.joints) == 3
    shape = pole.rotating_zones[0].shape
    assert isinstance(shape, CylinderZone) and shape.hole_diameter == 0.16
    assert pole.bodies[1].motion is Motion.STATIONARY and pole.bodies[1].zone is None
    assert {p.type for p in hawt.patches} == {PatchType.ROTATING_WALL, PatchType.INLET,
                                              PatchType.OUTLET, PatchType.SLIP}


def test_json_round_trip(francis: dict[str, Any], hawt: dict[str, Any]) -> None:
    for data in (francis, hawt):
        config = MachineProjectConfig.model_validate(data)
        assert MachineProjectConfig.model_validate(config.model_dump(mode="json")) == config


def test_models_are_frozen(hawt: dict[str, Any]) -> None:
    config = MachineProjectConfig.model_validate(hawt)
    with pytest.raises(ValidationError):
        config.bodies[0].motion = Motion.STATIONARY  # type: ignore[misc]


@pytest.mark.parametrize("path", [
    "bogus", "bodies.0.bogus", "bodies.0.source.bogus", "bodies.0.refinement.bogus",
    "bodies.0.layers.bogus", "rotating_zones.0.bogus", "rotating_zones.0.shape.bogus",
    "domain.bogus", "patches.0.bogus", "patches.0.source.bogus", "export.bogus",
    "rotating_zones.0.location_in_mesh.w",
])
def test_unknown_keys_rejected(hawt: dict[str, Any], path: str) -> None:
    assert any(e.endswith(":extra_forbidden") for e in errors(edited(hawt, path, 1)))


@pytest.mark.parametrize("path", [
    "rotating_zones.0.cell_size", "domain.diameter", "rotating_zones.0.shape.axis_min",
])
def test_non_finite_numbers_rejected(hawt: dict[str, Any], path: str) -> None:
    assert errors(edited(hawt, path, float("inf")))
    assert errors(edited(hawt, path, float("nan")))


@pytest.mark.parametrize("path, value", [
    ("bodies.0.name", "1rotor"), ("bodies.0.name", "ro tor"), ("patches.0.name", "a-b"),
    ("rotating_zones.0.name", ""), ("joints", [{"first": "x y", "second": "z"}]),
    ("rotating_zones.0.shape.axis_max", -0.5), ("rotating_zones.0.shape.diameter", 0.0),
    ("rotating_zones.0.shape.hole_diameter", 1.2), ("rotating_zones.0.cell_size", -1.0),
    ("domain.axis_min", 6.0), ("bodies.0.source.source_path", "rotor.obj"),
    ("bodies.0.source.scale", 0.0), ("bodies.0.refinement.max_level", 0),
    ("bodies.0.refinement.min_level", 11), ("rotating_zones.0.interface_level", -1),
    ("max_global_cells", 0), ("project_name", " bad"), ("rotating_zones", []),
    ("rotating_zones.0.shape.kind", "SPHERE"), ("domain.kind", "SPHERE"),
    ("flow_axis", "w"), ("patches.0.source.kind", "FILE"), ("patches.0.source.ref", ""),
])
def test_invalid_values_rejected(hawt: dict[str, Any], path: str, value: Any) -> None:
    assert errors(edited(hawt, path, value))


@pytest.mark.parametrize("path, location", [
    ("rotating_zones", ""), ("domain.parts", "domain.IMPORTED"),
    ("rotating_zones.0.shape.files", "rotating_zones.0.shape.IMPORTED"),
    ("domain.parts.0.files", "domain.IMPORTED.parts.0"),
])
def test_empty_lists_give_one_error(francis: dict[str, Any], path: str, location: str) -> None:
    assert errors(edited(francis, path, [])) == [f"{location}:value_error"]


def test_body_zone_follows_motion(hawt: dict[str, Any]) -> None:
    stationary_with_zone = edited(hawt, "bodies.0.motion", "STATIONARY")
    rotating_without_zone = edited(hawt, "bodies.0.zone", DELETE)
    split_without_zone = edited(rotating_without_zone, "bodies.0.motion", "SPLIT")

    for data in (stationary_with_zone, rotating_without_zone, split_without_zone):
        assert errors(data) == ["bodies.0:value_error"]
    MachineProjectConfig.model_validate(
        edited(stationary_with_zone, "bodies.0.zone", DELETE))


def test_named_regions_take_one_file(francis: dict[str, Any]) -> None:
    files = francis["domain"]["parts"][0]["files"]
    data = edited(francis, "domain.parts.0.files", files * 2)

    assert errors(data) == ["domain.IMPORTED.parts.0:value_error"]
    MachineProjectConfig.model_validate(
        edited(data, "domain.parts.0.format", "ONE_FILE_PER_PATCH"))


def test_vawt_only_fields_have_general_defaults(hawt: dict[str, Any]) -> None:
    config = MachineProjectConfig.model_validate(hawt)

    assert config.export.fluent_msh is False
    assert config.wake is None
    assert config.rotating_zones[0].interface.value == "AMI"


def test_schema_version_defaults_and_migrates(hawt: dict[str, Any]) -> None:
    assert MachineProjectConfig.model_validate(
        edited(hawt, "schema_version", 1)).schema_version == MACHINE_SCHEMA_VERSION
    assert MachineProjectConfig.model_validate(hawt).schema_version == MACHINE_SCHEMA_VERSION


@pytest.mark.parametrize("version, code", [
    (0, "SCHEMA_VERSION_INVALID"), ("1", "SCHEMA_VERSION_INVALID"),
    (True, "SCHEMA_VERSION_INVALID"), (MACHINE_SCHEMA_VERSION + 1, "SCHEMA_VERSION_NEWER"),
])
def test_unusable_schema_version_is_blocking(hawt: dict[str, Any], version: Any,
                                             code: str) -> None:
    _, issues = parse_config(edited(hawt, "schema_version", version))

    assert [(i.code, i.severity.value) for i in issues] == [(code, "BLOCKING")]


@pytest.mark.parametrize("path", [
    "bodies.0.source.source_units", "domain.parts.1.files.0.source_units",
    "rotating_zones.0.shape.files.0.source_units",
])
def test_units_never_assumed(hawt: dict[str, Any], francis: dict[str, Any], path: str) -> None:
    data = hawt if path.startswith("bodies") else francis
    for value in (DELETE, None, "furlong"):
        assert codes(edited(data, path, value)) == ["UNITS_NOT_CHOSEN"]


@pytest.mark.parametrize("path, code", [
    ("machine", "MACHINE_NOT_CHOSEN"), ("bodies.0.motion", "MOTION_NOT_CHOSEN"),
    ("rotating_zones.0.axis", "ZONE_AXIS_NOT_CHOSEN"),
    ("patches.2.type", "PATCH_TYPE_NOT_CHOSEN"),
])
def test_choices_never_assumed(hawt: dict[str, Any], path: str, code: str) -> None:
    for value in (DELETE, None, "bogus"):
        issues = parse_config(edited(hawt, path, value))[1]
        assert [(i.code, i.severity.value, i.details["field"]) for i in issues] == [
            (code, "BLOCKING", path)]
