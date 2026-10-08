from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import (
    VAWT_SCHEMA_VERSION,
    Axis,
    InterfaceType,
    VawtProjectConfig,
    plane_axes,
)
from vawt.validation import parse_config


@pytest.fixture
def draft(tmp_path: Path) -> dict[str, Any]:
    return preset_draft(tmp_path)


def edited(draft: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(draft)
    *parents, leaf = path.split(".")
    target = data
    for key in parents:  # sections left to model defaults may be absent
        target = target.setdefault(key, {})
    if value is _DELETE:
        target.pop(leaf, None)
    else:
        target[leaf] = value
    return data


_DELETE = object()


def errors(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        VawtProjectConfig.model_validate(data)
    return [".".join(map(str, e["loc"])) + ":" + e["type"] for e in caught.value.errors()]


def test_preset_draft_is_valid(draft: dict[str, Any]) -> None:
    config = VawtProjectConfig.model_validate(draft)

    assert config.schema_version == VAWT_SCHEMA_VERSION
    assert config.geometry.patch_name == "rotor"
    assert config.rotating_zone.interface is InterfaceType.AMI
    assert config.export.fluent_msh is True


@pytest.mark.parametrize("path", [
    "bogus", "rotor.bogus", "rotating_zone.bogus", "domain.bogus", "domain.patches.bogus",
    "geometry.bogus", "refinement.bogus", "layers.bogus", "quality.bogus",
    "snappy_quality.bogus", "export.bogus", "rotating_zone.location_in_mesh.w",
    "domain.bounds.minimum.w",
])
def test_unknown_keys_are_rejected_everywhere(draft: dict[str, Any], path: str) -> None:
    assert any(e.endswith("extra_forbidden") for e in errors(edited(draft, path, 1)))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_numbers_are_rejected(draft: dict[str, Any], value: float) -> None:
    assert errors(edited(draft, "rotating_zone.cell_size", value))
    assert errors(edited(draft, "domain.bounds.maximum.x", value))


def test_units_and_axes_have_no_defaults(draft: dict[str, Any]) -> None:
    for path in ("geometry.source_units", "rotor.axis", "rotor.flow_axis"):
        assert errors(edited(draft, path, _DELETE))


def test_missing_confirmations_get_specific_codes(draft: dict[str, Any]) -> None:
    data = edited(edited(draft, "geometry.source_units", None), "rotor", _DELETE)

    _, issues = parse_config(data)

    assert {i.code for i in issues} == {"UNITS_NOT_CHOSEN", "ROTOR_AXIS_NOT_CONFIRMED"}
    assert all(i.severity.value == "BLOCKING" for i in issues)


def test_flow_axis_must_differ_from_rotor_axis(draft: dict[str, Any]) -> None:
    assert errors(edited(draft, "rotor.flow_axis", "z"))


def test_zone_axial_extent_must_be_ordered(draft: dict[str, Any]) -> None:
    zone_max = draft["rotating_zone"]["axis_max"]
    assert errors(edited(draft, "rotating_zone.axis_min", zone_max))


def test_blade_levels_must_be_ordered(draft: dict[str, Any]) -> None:
    assert errors(edited(draft, "refinement.blade_min_level", 3))


def test_all_six_patch_names_are_editable(draft: dict[str, Any]) -> None:
    names = {"inlet": "in_1", "outlet": "out_1", "lateral_min": "south",
             "lateral_max": "north", "axial_min": "ground", "axial_max": "sky"}

    config = VawtProjectConfig.model_validate(edited(draft, "domain.patches", names))

    assert config.domain is not None
    assert config.domain.patches.names() == tuple(names.values())


def test_patch_names_must_be_unique_and_valid(draft: dict[str, Any]) -> None:
    assert errors(edited(draft, "domain.patches", {"inlet": "a", "outlet": "a"}))
    assert errors(edited(draft, "domain.patches", {"inlet": "1bad"}))


def test_rotor_patch_may_not_reuse_a_domain_patch_name(draft: dict[str, Any]) -> None:
    assert errors(edited(draft, "geometry.patch_name", "inlet"))


def test_absolute_layers_require_their_fields_only_when_enabled(
    draft: dict[str, Any]
) -> None:
    absolute = edited(edited(draft, "layers.sizing", "ABSOLUTE"),
                      "layers.min_thickness_m", None)

    VawtProjectConfig.model_validate(edited(absolute, "layers.enabled", False))
    assert errors(edited(absolute, "layers.enabled", True))


def test_domain_and_wake_are_optional(draft: dict[str, Any]) -> None:
    config = VawtProjectConfig.model_validate(
        edited(edited(draft, "domain", None), "refinement.wake", None)
    )
    assert config.domain is None
    assert config.refinement.wake is None


def test_newer_schema_is_rejected_and_missing_version_is_current(
    draft: dict[str, Any]
) -> None:
    assert errors(edited(draft, "schema_version", VAWT_SCHEMA_VERSION + 1))
    config = VawtProjectConfig.model_validate(edited(draft, "schema_version", _DELETE))
    assert config.schema_version == VAWT_SCHEMA_VERSION


@pytest.mark.parametrize("value", [0, -1, 1.0, 1.5, "1", "2", True, None, [1]])
def test_schema_version_that_is_not_a_whole_number_of_one_or_more_is_rejected(
    draft: dict[str, Any], value: Any
) -> None:
    data = edited(draft, "schema_version", value)
    assert any(e.endswith("schema_version_invalid") for e in errors(data))

    config, issues = parse_config(data)

    assert config is None
    assert [(i.code, i.severity.value) for i in issues] == [
        ("SCHEMA_VERSION_INVALID", "BLOCKING")
    ]
    assert issues[0].details["field"] == "schema_version"
    assert issues[0].suggested_action


def test_newer_schema_gets_a_specific_issue(draft: dict[str, Any]) -> None:
    _, issues = parse_config(edited(draft, "schema_version", VAWT_SCHEMA_VERSION + 1))

    assert [(i.code, i.severity.value) for i in issues] == [
        ("SCHEMA_VERSION_NEWER", "BLOCKING")
    ]


def test_current_schema_version_is_accepted(draft: dict[str, Any]) -> None:
    config, issues = parse_config(edited(draft, "schema_version", VAWT_SCHEMA_VERSION))

    assert issues == ()
    assert config is not None and config.schema_version == VAWT_SCHEMA_VERSION


def test_config_is_frozen(draft: dict[str, Any]) -> None:
    config = VawtProjectConfig.model_validate(draft)
    with pytest.raises(ValidationError):
        config.rotating_zone.diameter = 1.0  # type: ignore[misc]


def test_plane_axes_convention() -> None:
    assert plane_axes(Axis.Z) == (Axis.X, Axis.Y)
    assert plane_axes(Axis.Y) == (Axis.X, Axis.Z)
    assert plane_axes(Axis.X) == (Axis.Y, Axis.Z)
