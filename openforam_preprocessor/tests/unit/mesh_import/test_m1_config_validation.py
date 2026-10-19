"""M1-MSH: the imported_mesh section and its checks before any conversion.

Owner answer 3: existing projects are saved and meshed byte-identically
(compared with main's fingerprints, tests/fixtures/msh/m1/stl_baseline.json);
an imported mesh and STL bodies are exclusive; a changed file is found from
its hash.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from machines.config import MACHINE_SCHEMA_VERSION, MachineProjectConfig
from machines.msh_import import detect
from machines.msh_import.validation import MshThresholds, check_imported_mesh
from machines.validation import check_all, parse_config
from tests.fixtures.machines import configs as stl_configs
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1, mesh_file
from tests.fixtures.msh.m1.stl_baseline import fingerprints

Data = dict[str, Any]


def model(data: Data) -> MachineProjectConfig:
    config, issues = parse_config(copy.deepcopy(data))
    assert config is not None, [i.message for i in issues]
    return config


def codes(data: Data, thresholds: MshThresholds | None = None) -> dict[str, str]:
    config = model(data)
    found = check_all(config).issues if thresholds is None else check_imported_mesh(
        config, thresholds)
    return {i.code: i.severity.value for i in found}


@pytest.fixture
def rotating(tmp_path: Path) -> Data:
    return configs.rotating(mesh_file("rotating_41.msh", tmp_path))


# --- the model --------------------------------------------------------------------------

def test_existing_projects_are_saved_and_meshed_byte_identically(tmp_path: Path) -> None:
    baseline = json.loads((M1 / "stl_baseline.json").read_text("utf-8"))
    assert fingerprints(tmp_path) == baseline


def test_an_absent_imported_mesh_is_left_out_of_the_saved_file(tmp_path: Path) -> None:
    config = model(stl_configs.hawt(tmp_path))
    assert "imported_mesh" not in config.model_dump(mode="json")
    assert "imported_mesh" not in json.loads(config.model_dump_json())
    assert config.imported_mesh is None and config.schema_version == MACHINE_SCHEMA_VERSION


def test_an_imported_mesh_round_trips(rotating: Data) -> None:
    config = model(rotating)
    assert model(config.model_dump(mode="json")) == config
    mesh = config.imported_mesh
    assert mesh is not None and mesh.zones[0].cell_zone == "rotating"
    assert config.zone_names() == ("rotor_zone",)


def test_without_an_imported_mesh_a_rotating_zone_is_still_needed(rotating: Data) -> None:
    del rotating["imported_mesh"]
    config, issues = parse_config(rotating)
    assert config is None and any("rotating zone" in i.message for i in issues)


@pytest.mark.parametrize("path, value", [
    ("source_units", None), ("sha256", "abc"), ("zones.0.cell_zone", "has space"),
    ("zones.0.axis", None), ("ami_pairs.0.first", "a(b)")])
def test_imported_mesh_fields_are_strict(rotating: Data, path: str, value: Any) -> None:
    target = rotating["imported_mesh"]
    *parents, last = path.split(".")
    for key in parents:
        target = target[int(key)] if key.isdigit() else target[key]
    target[last] = value
    assert parse_config(rotating)[0] is None


def test_units_are_never_assumed(rotating: Data) -> None:
    del rotating["imported_mesh"]["source_units"]
    assert parse_config(rotating)[0] is None


# --- the checks -------------------------------------------------------------------------

def test_a_complete_imported_project_has_no_finding(rotating: Data) -> None:
    assert codes(rotating) == {}


def test_imported_mesh_and_stl_bodies_are_exclusive(rotating: Data, tmp_path: Path) -> None:
    hawt = stl_configs.hawt(tmp_path / "stl")
    rotating["bodies"] = hawt["bodies"]
    rotating["domain"] = hawt["domain"]
    found = model(rotating)
    issue = next(i for i in check_all(found).issues if i.code == "MSH_WITH_STL")
    assert issue.severity.value == "BLOCKING" and issue.details["fields"] == ["bodies",
                                                                              "domain"]


def test_a_changed_file_is_found_from_its_hash(rotating: Data) -> None:
    path = Path(rotating["imported_mesh"]["source_path"])
    path.write_bytes(path.read_bytes() + b"\n")
    assert codes(rotating)["MSH_SOURCE_CHANGED"] == "BLOCKING"


def test_detection_findings_stop_the_project(tmp_path: Path) -> None:
    data = configs.box(mesh_file("box_22_nogroups.msh", tmp_path))
    assert codes(data) == {"GMSH_NO_PHYSICAL_GROUPS": "BLOCKING"}


def test_binary_gmsh_without_the_package(monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Path) -> None:
    monkeypatch.setattr(detect, "gmsh_available", lambda: False)
    data = configs.box(mesh_file("box_22_bin.msh", tmp_path))
    assert codes(data)["GMSH_BINARY_NEEDS_GMSH"] == "BLOCKING"


def test_no_rotating_zone_is_blocking_with_the_gmsh_fix(tmp_path: Path) -> None:
    """Owner answer 5: BLOCKING only, with the fix."""
    config = model(configs.box(mesh_file("box_22.msh", tmp_path)))
    issue = next(i for i in check_all(config).issues if i.code == "MSH_NO_CELL_ZONE")
    assert issue.severity.value == "BLOCKING"
    assert "Physical Volume for the rotating region" in issue.suggested_action
    assert issue.details["found"] == ["fluid"]


def test_no_rotating_zone_in_fluent_gives_the_fluent_fix(tmp_path: Path) -> None:
    data = configs.box(mesh_file("fluent_box.msh", tmp_path))
    issue = next(i for i in check_all(model(data)).issues if i.code == "MSH_NO_CELL_ZONE")
    assert "fluid cell zone" in issue.suggested_action


def test_the_only_cell_zone_cannot_rotate(tmp_path: Path) -> None:
    data = configs.box(mesh_file("fluent_box.msh", tmp_path))
    data["imported_mesh"]["zones"] = [{"name": "rotor_zone", "cell_zone": "fluid-1",
                                       "axis": "x", "origin": stl_configs.vec(0, 0, 0)}]
    issue = next(i for i in check_all(model(data)).issues if i.code == "MSH_NO_CELL_ZONE")
    assert "whole mesh" in issue.message


def test_an_unknown_cell_zone(rotating: Data) -> None:
    rotating["imported_mesh"]["zones"][0]["cell_zone"] = "spinning"
    issue = next(i for i in check_all(model(rotating)).issues if i.code == "MSH_NO_CELL_ZONE")
    assert issue.details["field"] == "imported_mesh.zones.0.cell_zone"
    assert issue.details["found"] == ["stationary", "rotating"]


def test_a_rotating_zone_needs_an_interface(rotating: Data) -> None:
    rotating["imported_mesh"]["ami_pairs"] = []
    rotating["patches"] += [stl_configs.patch(n, "WALL", "MESH_PATCH", n)
                            for n in ("ami_stat", "ami_rot")]
    assert codes(rotating) == {"MSH_NO_INTERFACE": "BLOCKING"}


@pytest.mark.parametrize("first, second, code", [
    ("ami_stat", "ami_stat", "MSH_AMI_PAIR_INVALID"),
    ("ami_stat", "nothing", "MSH_AMI_PATCH_UNKNOWN"),
])
def test_ami_pair_checks(rotating: Data, first: str, second: str, code: str) -> None:
    rotating["imported_mesh"]["ami_pairs"] = [{"first": first, "second": second}]
    assert code in codes(rotating)


def test_an_interface_patch_is_not_typed(rotating: Data) -> None:
    rotating["patches"].append(stl_configs.patch("ami_rot", "WALL", "MESH_PATCH", "ami_rot"))
    assert codes(rotating)["MSH_AMI_PATCH_TYPED"] == "BLOCKING"


def test_every_mesh_patch_needs_a_type(rotating: Data) -> None:
    rotating["patches"] = [p for p in rotating["patches"] if p["name"] != "y_min"]
    found = check_all(model(rotating)).issues
    issue = next(i for i in found if i.code == "MESH_PATCH_WITHOUT_TYPE")
    assert issue.details["patch"] == "y_min"


@pytest.mark.parametrize("change, code", [
    (lambda d: d["patches"].append(stl_configs.patch("x", "WALL", "MESH_PATCH", "nothing")),
     "PATCH_SOURCE_UNKNOWN"),
    (lambda d: d["patches"].append(stl_configs.patch("x", "WALL", "DOMAIN_FACE", "x_min")),
     "PATCH_SOURCE_UNKNOWN"),
    (lambda d: d["patches"].append(stl_configs.patch("again", "WALL", "MESH_PATCH", "rotor")),
     "PATCH_SOURCE_DUPLICATE"),
    (lambda d: d["patches"].__setitem__(0, stl_configs.patch("ami_stat", "INLET",
                                                             "MESH_PATCH", "inlet")),
     "RESERVED_PATCH_NAME"),  # a rename onto another patch of the mesh
    (lambda d: d["patches"].__setitem__(1, stl_configs.patch("outlet", "WALL",
                                                             "MESH_PATCH", "outlet")),
     "NO_OUTLET"),
    (lambda d: d["patches"].__setitem__(1, stl_configs.patch("inlet", "OUTLET",
                                                             "MESH_PATCH", "outlet")),
     "PATCH_NAME_DUPLICATE"),
])
def test_patch_checks(rotating: Data, change: Any, code: str) -> None:
    change(rotating)
    assert codes(rotating)[code] == "BLOCKING"


def test_a_patch_may_be_renamed(rotating: Data) -> None:
    rotating["patches"][0] = stl_configs.patch("intake", "INLET", "MESH_PATCH", "inlet")
    rotating["physics"]["inlets"] = {"intake": rotating["physics"]["inlets"]["inlet"]}
    assert codes(rotating) == {}


def test_units_far_from_a_machine_size_are_a_warning(rotating: Data) -> None:
    rotating["imported_mesh"]["source_units"] = "um"
    found = codes(rotating)
    assert found == {"MSH_UNITS_SUSPECT": "WARNING"}
    assert codes(rotating, MshThresholds(min_extent_m=1e-6)) == {}


def test_a_vawt_project_cannot_import_a_mesh(rotating: Data) -> None:
    rotating["machine"] = "VAWT"
    rotating["flow_axis"] = "x"
    assert codes(rotating)["MSH_MACHINE_NOT_SUPPORTED"] == "BLOCKING"


def test_physics_is_checked_against_the_imported_zones(rotating: Data) -> None:
    from case_setup.validation import check_physics

    config = model(rotating)
    assert check_physics(config, ()) == ()
    rotating["physics"]["rotation"] = {"other": rotating["physics"]["rotation"]["rotor_zone"]}
    found = {i.code for i in check_physics(model(rotating), ())}
    assert {"ROTATION_UNKNOWN_ZONE", "ROTATION_NOT_SET"} <= found
