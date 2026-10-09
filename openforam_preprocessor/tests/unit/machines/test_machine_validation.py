"""Checks before meshing (spec sections 3.1 and 10) on the G0 synthetic geometry."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core.issues import Issue
from machines.config import MachineProjectConfig
from machines.validation import (
    MachineThresholds,
    check_config,
    surface_points,
    validate_machine,
)
from machines.vawt_migration import from_vawt
from tests.fixtures.machines import configs
from tests.fixtures.machines.configs import patch, vec
from tests.fixtures.machines.g0.common import make_geometry
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import VawtProjectConfig
from vawt.presets import PresetKind


def edited(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(data)
    *parents, leaf = path.split(".")
    target: Any = data
    for key in parents:
        target = target[int(key)] if isinstance(target, list) else target[key]
    if isinstance(target, list):
        target[int(leaf)] = value
    else:
        target[leaf] = value
    return data


def found(data: dict[str, Any], thresholds: MachineThresholds | None = None) -> list[Issue]:
    result = validate_machine(data, thresholds)
    assert result.config is not None
    # The STL check reports separate blades and hubs as INFO; not a finding here.
    return [i for i in result.issues if i.code != "MULTIPLE_COMPONENTS"]


def summary(issues: list[Issue]) -> list[tuple[str, str]]:
    return [(i.code, i.severity.value) for i in issues]


def config_codes(data: dict[str, Any]) -> list[str]:
    return [i.code for i in check_config(MachineProjectConfig.model_validate(data))]


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return configs.hawt(tmp_path)


@pytest.fixture
def francis(tmp_path: Path) -> dict[str, Any]:
    return configs.francis(tmp_path)


@pytest.fixture
def duct(tmp_path: Path) -> dict[str, Any]:
    return configs.duct(tmp_path)


# --- every VAWT project and the G0 machines pass -----------------------------------------

@pytest.mark.parametrize("include_domain", [True, False])
@pytest.mark.parametrize("kind", list(PresetKind))
@pytest.mark.parametrize("axis, flow", [("z", "x"), ("x", "y"), ("y", "z")])
def test_migrated_vawt_presets_have_no_findings(tmp_path: Path, axis: str, flow: str,
                                                kind: PresetKind, include_domain: bool) -> None:
    config = VawtProjectConfig.model_validate(preset_draft(
        tmp_path, axis=axis, flow_axis=flow, kind=kind, include_domain=include_domain))

    assert found(from_vawt(config).model_dump(mode="json")) == []


def test_hawt_has_no_findings(hawt: dict[str, Any]) -> None:
    assert found(hawt) == []


def test_split_pole_crosses_only_the_domain_boundary(tmp_path: Path) -> None:
    # G0 R5: the pole runs through the domain's z faces (z +-2.3 vs +-2.16).
    issues = found(configs.pole(tmp_path, motion="SPLIT"))

    assert summary(issues) == [("BODY_CROSSES_DOMAIN_BOUNDARY", "WARNING")]
    assert issues[0].details["body"] == "pole"


def test_stationary_pole_in_annular_zone_hole_passes(tmp_path: Path) -> None:
    issues = found(configs.pole(tmp_path, motion="STATIONARY", hole_diameter=0.16))

    assert summary(issues) == [("BODY_CROSSES_DOMAIN_BOUNDARY", "WARNING")]


def test_francis_needs_the_imported_surface_checks_later(francis: dict[str, Any]) -> None:
    assert summary(found(francis)) == [("IMPORTED_SURFACES_NOT_CHECKED", "INFO")]


def test_duct_with_files_per_patch_passes(duct: dict[str, Any]) -> None:
    assert summary(found(duct)) == [("IMPORTED_SURFACES_NOT_CHECKED", "INFO")]


# --- bodies, zones and the interface (section 3.1) ----------------------------------------

def test_stationary_pole_crossing_the_zone_is_blocking(tmp_path: Path) -> None:
    # The pole's vertices lie only at its ends, outside the zone: the check
    # must test the surface between them.
    issues = found(configs.pole(tmp_path, motion="STATIONARY"))

    crossing = [i for i in issues if i.code == "STATIONARY_BODY_CROSSES_INTERFACE"]
    assert [i.severity.value for i in crossing] == ["BLOCKING"]
    assert crossing[0].details["body"] == "pole"
    assert crossing[0].details["depth"] == pytest.approx(0.66, abs=0.03)


def test_split_pole_in_an_annular_zone_is_not_split(tmp_path: Path) -> None:
    issues = found(configs.pole(tmp_path, motion="SPLIT", hole_diameter=0.16))

    assert ("SPLIT_BODY_NOT_SPLIT", "ERROR") in summary(issues)


def test_pole_close_to_the_hole_wall_warns(tmp_path: Path) -> None:
    issues = found(configs.pole(tmp_path, motion="STATIONARY", hole_diameter=0.07))

    small = [i for i in issues if i.code == "BODY_ZONE_CLEARANCE_SMALL"]
    assert [i.severity.value for i in small] == ["WARNING"]
    assert small[0].details["clearance"] == pytest.approx(0.005, abs=1e-3)


def test_rotating_body_entering_another_zone_is_blocking(tmp_path: Path) -> None:
    data = configs.pole(tmp_path, motion="SPLIT")
    data["rotating_zones"].append({
        "name": "upper", "axis": "z",
        "shape": {"kind": "CYLINDER", "centre_u": 0.0, "centre_v": 0.0,
                  "axis_min": 0.8, "axis_max": 1.2, "diameter": 0.6},
        "cell_size": 0.02, "location_in_mesh": vec(0.2013, 0.0113, 1.0013)})

    issues = found(data)

    assert ("BODY_IN_OTHER_ZONE", "BLOCKING") in summary(issues)
    assert [i.details["zone"] for i in issues if i.code == "BODY_IN_OTHER_ZONE"] == ["upper"]


def test_rotor_larger_than_its_zone_is_blocking(hawt: dict[str, Any]) -> None:
    issues = found(edited(hawt, "rotating_zones.0.shape.diameter", 0.9))

    assert ("ROTATING_BODY_OUTSIDE_ZONE", "BLOCKING") in summary(issues)


def test_rotor_close_to_its_zone_wall_warns(hawt: dict[str, Any]) -> None:
    # Blade tips reach r = 0.5007; a 1.02 m zone leaves about 9 mm < one 25 mm cell.
    issues = found(edited(hawt, "rotating_zones.0.shape.diameter", 1.02))

    assert summary(issues) == [("ZONE_CLEARANCE_SMALL", "WARNING")]
    assert issues[0].details["clearance"] == pytest.approx(0.0093, abs=1e-3)
    assert summary(found(edited(hawt, "rotating_zones.0.shape.diameter", 1.02),
                         MachineThresholds(min_clearance_cells=0.3))) == []


def test_body_outside_the_domain_is_blocking(hawt: dict[str, Any]) -> None:
    issues = found(edited(hawt, "bodies.0.source.translation", vec(0.0, 10.0, 0.0)))

    assert ("BODY_OUTSIDE_DOMAIN", "BLOCKING") in summary(issues)
    assert ("ROTATING_BODY_OUTSIDE_ZONE", "BLOCKING") in summary(issues)


def test_mesh_point_inside_a_body_is_blocking(tmp_path: Path) -> None:
    # Blade at +x: a 0.04 x 0.2 x 1.0 box centred at (0.5, 0, 0).
    data = edited(configs.pole(tmp_path), "rotating_zones.0.location_in_mesh",
                  vec(0.5013, 0.0113, 0.0137))

    issues = [i for i in found(data) if i.code == "MESH_POINT_IN_BODY"]

    assert [(i.severity.value, i.details["body"], i.details["field"]) for i in issues] == [
        ("BLOCKING", "blades", "rotating_zones.0.location_in_mesh")]


def test_open_body_cannot_be_checked_for_mesh_points(tmp_path: Path) -> None:
    data = configs.pole(tmp_path, motion="STATIONARY", hole_diameter=0.16)
    make_geometry.side(0.02, -0.05, 0.02, 0.05).export(tmp_path / "plate.stl")  # open tube
    data["bodies"].append({"name": "plate", "source": configs.stl(tmp_path / "plate.stl"),
                           "motion": "STATIONARY"})
    data["patches"].append(patch("plate", "WALL", "BODY", "plate"))

    issues = found(edited(data, "bodies.2.source.translation", vec(-1.0, 0.0, 0.0)))

    assert ("MESH_POINT_UNCHECKED", "WARNING") in summary(issues)


def test_body_loading_problems_name_the_body(hawt: dict[str, Any], tmp_path: Path) -> None:
    issues = found(edited(hawt, "bodies.0.source.source_path", str(tmp_path / "missing.stl")))

    assert issues and all(i.details.get("body") == "rotor" for i in issues)
    assert any(i.is_stopping for i in issues)


def test_surface_points_cover_long_faces() -> None:
    pole = make_geometry.closed_cylinder(0.03, -2.3, 2.3)

    points, spacing = surface_points(pole, 0.05, 1_000_000)
    capped, wider = surface_points(pole, 0.05, 2_000)

    assert spacing == 0.05 and len(points) > 20 * len(pole.vertices)
    middle = np.abs(points[:, 2]) < 0.05
    assert middle.any()  # the vertices alone are all at z = +-2.3
    assert wider > spacing and len(capped) < len(points)


# --- configuration rules (no geometry) ------------------------------------------------------

def test_duplicate_and_reserved_names(hawt: dict[str, Any]) -> None:
    data = copy.deepcopy(hawt)
    data["rotating_zones"].append(copy.deepcopy(data["rotating_zones"][0]))
    data["patches"][3]["name"] = "inlet"
    data["patches"][0]["name"] = "AMI1"

    assert config_codes(data) == ["DUPLICATE_NAME", "PATCH_NAME_DUPLICATE",
                                  "RESERVED_PATCH_NAME", "ZONES_OVERLAP"]


def test_body_naming_a_missing_zone(hawt: dict[str, Any]) -> None:
    assert config_codes(edited(hawt, "bodies.0.zone", "nowhere")) == ["UNKNOWN_ZONE"]


@pytest.mark.parametrize("path, value", [
    ("domain", None), ("rotating_zones.0.interface", "CELL_ZONE"),
    ("wake", {"box": {"minimum": vec(-1, -1, -1), "maximum": vec(1, 1, 1)}}),
    ("export", {"fluent_msh": True}),
])
def test_vawt_only_settings(hawt: dict[str, Any], path: str, value: Any) -> None:
    issues = check_config(MachineProjectConfig.model_validate(edited(hawt, path, value)))

    vawt_only = [i for i in issues if i.code == "VAWT_ONLY_SETTING"]
    assert [i.severity.value for i in vawt_only] == ["BLOCKING"]
    assert "VAWT_ONLY_SETTING" not in config_codes(edited(edited(hawt, path, value),
                                                         "machine", "VAWT"))


def test_domain_kind_per_machine(francis: dict[str, Any], hawt: dict[str, Any]) -> None:
    box = {"kind": "BOX", "bounds": {"minimum": vec(-1, -1, -1), "maximum": vec(1, 1, 1)},
           "cell_size": 0.1, "location_in_mesh": vec(0.5, 0.5, 0.5)}

    assert "DOMAIN_KIND_NOT_ALLOWED" in config_codes(edited(francis, "domain", box))
    assert "DOMAIN_KIND_NOT_ALLOWED" not in config_codes(edited(hawt, "domain", box))


def test_vawt_flow_axis(tmp_path: Path) -> None:
    data = configs.pole(tmp_path)

    assert config_codes(edited(data, "flow_axis", None)) == ["FLOW_AXIS_NOT_CHOSEN"]
    assert config_codes(edited(data, "flow_axis", "z")) == ["FLOW_ALONG_ROTOR_AXIS"]
    assert config_codes(data) == []


def test_patch_sources_must_exist(hawt: dict[str, Any], francis: dict[str, Any],
                                  duct: dict[str, Any]) -> None:
    assert config_codes(edited(hawt, "patches.0.source.ref", "hub")) == [
        "PATCH_SOURCE_UNKNOWN", "BODY_WITHOUT_PATCH"]
    assert config_codes(edited(hawt, "patches.3.source.ref", "x_min")) == [
        "PATCH_SOURCE_UNKNOWN", "DOMAIN_FACE_WITHOUT_PATCH"]
    assert config_codes(edited(hawt, "patches.3.source", {"kind": "REGION", "ref": "side"})
                        ) == ["PATCH_SOURCE_UNKNOWN", "DOMAIN_FACE_WITHOUT_PATCH"]
    assert config_codes(edited(duct, "patches.2.source.ref", "walls")) == [
        "PATCH_SOURCE_UNKNOWN", "REGION_WITHOUT_PATCH", "IMPORTED_SURFACES_NOT_CHECKED"]
    # Named regions are read in G2: any region name is accepted until then.
    assert config_codes(edited(francis, "patches.2.source.ref", "walls")) == [
        "IMPORTED_SURFACES_NOT_CHECKED"]


def test_each_source_has_one_patch(hawt: dict[str, Any]) -> None:
    data = copy.deepcopy(hawt)
    data["patches"].append(patch("side2", "WALL", "DOMAIN_FACE", "side"))

    assert config_codes(data) == ["PATCH_SOURCE_DUPLICATE"]


def test_patch_without_type_or_source(hawt: dict[str, Any]) -> None:
    data = copy.deepcopy(hawt)
    del data["patches"][3]  # side

    assert config_codes(data) == ["DOMAIN_FACE_WITHOUT_PATCH"]


@pytest.mark.parametrize("index, kind, codes", [
    (1, "WALL", ["NO_INLET"]), (2, "WALL", ["NO_OUTLET"]),
    (1, "OUTLET", ["NO_INLET"]),
])
def test_inlet_and_outlet_required(hawt: dict[str, Any], index: int, kind: str,
                                   codes: list[str]) -> None:
    assert config_codes(edited(hawt, f"patches.{index}.type", kind)) == codes


@pytest.mark.parametrize("motion, kind, code", [
    ("ROTATING", "WALL", "BODY_PATCH_TYPE_MISMATCH"),
    ("STATIONARY", "ROTATING_WALL", "BODY_PATCH_TYPE_MISMATCH"),
    ("SPLIT", "ROTATING_WALL", "BODY_PATCH_TYPE_MISMATCH"),
    ("SPLIT", "WALL", None),
])
def test_body_patch_type_follows_motion(tmp_path: Path, motion: str, kind: str,
                                        code: str | None) -> None:
    data = configs.pole(tmp_path, motion="SPLIT")
    data["bodies"][1]["motion"] = motion
    if motion == "STATIONARY":
        del data["bodies"][1]["zone"]
    data["patches"][1]["type"] = kind

    issues = check_config(MachineProjectConfig.model_validate(data))

    assert [(i.code, i.severity.value) for i in issues] == (
        [] if code is None else [(code, "ERROR")])


def test_rotating_wall_on_the_domain(hawt: dict[str, Any]) -> None:
    assert config_codes(edited(hawt, "patches.3.type", "ROTATING_WALL")) == [
        "ROTATING_WALL_ON_DOMAIN"]


def test_regions_need_a_patch_or_a_joint(duct: dict[str, Any]) -> None:
    data = copy.deepcopy(duct)
    del data["patches"][2]  # wall

    assert config_codes(data) == ["REGION_WITHOUT_PATCH", "IMPORTED_SURFACES_NOT_CHECKED"]


def test_joint_rules(francis: dict[str, Any], duct: dict[str, Any],
                     hawt: dict[str, Any]) -> None:
    joints = francis["joints"]
    itself = edited(francis, "joints", [*joints, {"first": "inlet", "second": "inlet"}])
    reused = edited(francis, "joints", [*joints, {"first": "guide_in", "second": "x"}])

    assert config_codes(edited(hawt, "joints", joints)) == ["JOINT_WITHOUT_IMPORTED_DOMAIN"]
    assert config_codes(itself) == ["JOINT_INVALID", "JOINT_REGION_IS_PATCH",
                                    "JOINT_REGION_REUSED", "IMPORTED_SURFACES_NOT_CHECKED"]
    assert config_codes(reused) == ["JOINT_REGION_REUSED", "IMPORTED_SURFACES_NOT_CHECKED"]
    assert config_codes(edited(duct, "joints", [{"first": "wall", "second": "nowhere"}])) == [
        "JOINT_REGION_UNKNOWN", "JOINT_REGION_IS_PATCH", "IMPORTED_SURFACES_NOT_CHECKED"]


def test_mesh_points(hawt: dict[str, Any]) -> None:
    zone_point = edited(hawt, "rotating_zones.0.location_in_mesh", vec(0.5, 0.0, 0.0))
    outer_in_zone = edited(hawt, "domain.location_in_mesh", vec(0.0, 0.3, 0.0))
    outer_outside = edited(hawt, "domain.location_in_mesh", vec(-3.0, 0.0, 0.0))

    assert config_codes(zone_point) == ["INNER_POINT_OUTSIDE_ZONE"]
    assert config_codes(outer_in_zone) == ["OUTER_POINT_INVALID"]
    assert config_codes(outer_outside) == ["OUTER_POINT_INVALID"]


def test_zone_against_the_domain(hawt: dict[str, Any], tmp_path: Path) -> None:
    assert config_codes(edited(hawt, "domain.diameter", 1.1)) == ["ZONE_OUTSIDE_DOMAIN"]
    # 0.05 m between the zone and the domain wall: below one 0.1 m domain cell.
    assert config_codes(edited(hawt, "domain.diameter", 1.3)) == ["ZONE_CLEARANCE_SMALL"]
    pole = configs.pole(tmp_path)
    assert config_codes(edited(pole, "domain.bounds.maximum", vec(7.28, 0.7, 2.16))) == [
        "ZONE_OUTSIDE_DOMAIN"]


def _second_zone(data: dict[str, Any], axis: str, shape: dict[str, Any],
                 point: dict[str, float]) -> dict[str, Any]:
    data = copy.deepcopy(data)
    data["rotating_zones"].append({"name": "second", "axis": axis,
                                   "shape": {"kind": "CYLINDER", **shape},
                                   "cell_size": 0.02, "location_in_mesh": point})
    return data


def test_zone_overlap(tmp_path: Path) -> None:
    pole = configs.pole(tmp_path, motion="STATIONARY", hole_diameter=0.16)
    inside_hole = {"centre_u": 0.0, "centre_v": 0.0, "axis_min": -0.5, "axis_max": 0.5,
                   "diameter": 0.1}
    beside = {**inside_hole, "centre_u": 0.9, "diameter": 0.4}
    above = {**beside, "centre_u": 0.0, "axis_min": 0.66, "axis_max": 1.0}
    crossing = {"centre_u": 0.0, "centre_v": 0.0, "axis_min": -1.0, "axis_max": 1.0,
                "diameter": 0.2}

    point = vec(0.0113, 0.0213, 0.0013)
    assert config_codes(_second_zone(pole, "z", inside_hole, point)) == []
    assert config_codes(_second_zone(pole, "z", beside, vec(0.9113, 0.0, 0.0))) == [
        "ZONES_OVERLAP"]
    assert config_codes(_second_zone(pole, "z", above, vec(0.0113, 0.0, 0.8))) == []
    assert config_codes(_second_zone(pole, "y", crossing, vec(0.0113, 0.5, 0.0013))) == [
        "ZONES_MAY_OVERLAP"]


def test_thresholds_are_configuration(hawt: dict[str, Any]) -> None:
    near = MachineProjectConfig.model_validate(edited(hawt, "domain.diameter", 1.3))

    assert [i.code for i in check_config(near)] == ["ZONE_CLEARANCE_SMALL"]
    assert check_config(near, MachineThresholds(min_clearance_cells=0.4)) == ()

