"""Fixes from the combined G2-G4 testing review (on g5-francis)."""

from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from machines.assembly import MachineCaseGenerator
from machines.config import MachineProjectConfig
from machines.domains import edge_use_counts, read_imported
from machines.operations import ASSEMBLE, CHECK_MESH, MACHINE_EXECUTABLES, mesh_operation
from machines.pipeline import MachinePipeline
from machines.preflight import estimate_cells
from machines.validation import check_all, check_config, check_imported
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from tests.fixtures.machines.configs import patch, stl

_FEATURE_ENTRY = re.compile(r"(\w+)\.stl\s*\{\s*extractionMethod[^\n]*\n\s*"
                            r"extractFromSurfaceCoeffs\s*\{\s*includedAngle\s+([0-9.]+);")
_RESOLVE = re.compile(r"resolveFeatureAngle\s+([0-9.]+);")


def render(data: dict[str, Any]):
    config = MachineProjectConfig.model_validate(data)
    checked = check_all(config)
    cases = MachineCaseGenerator().render(config, checked.surfaces, checked.meshes, typed=False)
    return {c.name: c for c in cases.all}


def included_angles(case) -> dict[str, str]:
    return dict(_FEATURE_ENTRY.findall(case.dictionaries["system/surfaceFeatureExtractDict"]))


def resolve_angle(case) -> str:
    match = _RESOLVE.search(case.dictionaries["system/snappyHexMeshDict"])
    assert match is not None
    return match[1]


def codes(issues) -> dict[str, str]:
    return {i.code: i.severity.value for i in issues}


# --- 1. a body's feature angle ---------------------------------------------------------------

def _hawt_features(directory: Path, angle: float | None) -> dict[str, Any]:
    data = configs.hawt(directory)
    refinement: dict[str, Any] = {"extract_features": True}
    if angle is not None:
        refinement["feature_angle_deg"] = angle
    data["bodies"][0]["refinement"] = refinement
    return data


def test_feature_angle_45_sets_included_and_resolve_angle(tmp_path: Path) -> None:
    cases = render(_hawt_features(tmp_path, 45.0))
    zone, domain = cases["zone_rotating"], cases["domain"]

    angles = included_angles(zone)
    assert angles["rotor"] == "135"  # 180 - 45, as the VAWT generator
    assert {a for stem, a in angles.items() if stem != "rotor"} == {"150"}  # zone surface
    assert resolve_angle(zone) == "45"
    assert resolve_angle(domain) == "30"  # the rotor is not in the domain mesh


def test_default_feature_angle_leaves_dictionaries_as_before(tmp_path: Path) -> None:
    zone = render(_hawt_features(tmp_path, None))["zone_rotating"]

    assert set(included_angles(zone).values()) == {"150"}
    assert resolve_angle(zone) == "30"
    assert "includedAngle   150;" in zone.dictionaries["system/surfaceFeatureExtractDict"]


def test_fractional_feature_angle(tmp_path: Path) -> None:
    zone = render(_hawt_features(tmp_path, 32.5))["zone_rotating"]

    assert included_angles(zone)["rotor"] == "147.5"
    assert resolve_angle(zone) == "32.5"


def test_bodies_with_different_angles_resolve_at_the_smallest(tmp_path: Path) -> None:
    data = configs.pole(tmp_path / "geo")  # blades (rotating) and a split pole
    data["bodies"][0]["refinement"] = {"feature_angle_deg": 40.0}
    data["bodies"][1]["refinement"] = {"feature_angle_deg": 50.0}

    cases = render(data)

    assert resolve_angle(cases["zone_rotating"]) == "40"  # blades and the pole's inner part
    assert resolve_angle(cases["domain"]) == "50"  # the pole's outer part only


# --- 2. preflight bands in an imported domain's part case --------------------------------------

def _duct_with_obstacle(directory: Path) -> dict[str, Any]:
    data = configs.duct(directory)
    # A stationary 0.06 m cube on the duct's centre line, 1.5 m along it, clear of the zone.
    angle = np.radians(20.0)
    cube = trimesh.creation.box(extents=(0.06, 0.06, 0.06))
    cube.apply_translation((1.5 * np.cos(angle), 1.5 * np.sin(angle), 0.0))
    cube.export(directory / "obstacle.stl")
    data["bodies"] = [{"name": "obstacle", "source": stl(directory / "obstacle.stl"),
                       "motion": "STATIONARY"}]
    data["patches"].append(patch("obstacle", "WALL", "BODY", "obstacle"))
    return data


def test_preflight_bands_go_to_the_part_case(tmp_path: Path) -> None:
    data = _duct_with_obstacle(tmp_path / "geo")
    config = MachineProjectConfig.model_validate(data)
    checked = check_all(config)
    assert not [i for i in checked.issues if i.is_stopping], codes(checked.issues)
    real = set(render(data)) - {"merged"}

    estimate = estimate_cells(config, checked.meshes, checked.surfaces)

    assert set(estimate.per_case) == real == {"domain_duct", "zone_rotating"}
    without_body = estimate_cells(config.model_copy(update={"bodies": ()}), {}, checked.surfaces)
    body_band = 4.0 * checked.meshes["obstacle"].area / (0.05 / 2**2) ** 2  # part cell, level 2
    added = estimate.surface["domain_duct"] - without_body.surface["domain_duct"]
    assert added == pytest.approx(body_band, abs=1)
    assert without_body.surface["domain_duct"] > 0  # the zone's interface band is there too
    assert max(estimate.per_case.values()) == estimate.per_case["domain_duct"]


def test_box_and_cylinder_domains_keep_the_domain_case(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.pole(tmp_path / "geo"))
    checked = check_all(config)

    estimate = estimate_cells(config, checked.meshes, checked.surfaces)

    assert set(estimate.per_case) == {"domain", "zone_rotating"}


# --- 4. a body's layers change re-runs its zone mesh only ----------------------------------------

def test_body_layers_change_reruns_its_zone(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path / "geo")
    env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)

    def run(raw: dict[str, Any]):
        runner = FakeMachineRunner()
        pipeline = MachinePipeline(runner, environment=env, system_probe=lambda _: PLENTY)
        result = pipeline.run_sync(tmp_path / "project", MachineProjectConfig.model_validate(raw))
        return result, runner

    run(data)
    layered = copy.deepcopy(data)
    layered["bodies"][0]["layers"] = {"enabled": True, "count": 4}
    result, runner = run(layered)

    assert result.succeeded
    assert set(result.reused) == {mesh_operation("domain")}
    assert {mesh_operation("zone_rotating"), ASSEMBLE, CHECK_MESH} <= set(result.executed)
    assert {case for _, case in runner.calls} == {"zone_rotating", "merged"}

    more = copy.deepcopy(layered)
    more["bodies"][0]["layers"]["count"] = 5
    result, runner = run(more)
    assert set(result.reused) == {mesh_operation("domain")}


# --- 5. interface_level on an imported zone ------------------------------------------------------

def test_interface_level_on_imported_zone_warns(tmp_path: Path) -> None:
    data = configs.francis(tmp_path / "geo")
    data["rotating_zones"][0]["interface_level"] = 3

    found = codes(check_config(MachineProjectConfig.model_validate(data)))

    assert found["INTERFACE_LEVEL_IGNORED"] == "WARNING"


def test_interface_level_default_or_cylinder_zone_is_not_reported(tmp_path: Path) -> None:
    francis = MachineProjectConfig.model_validate(configs.francis(tmp_path / "f"))
    hawt = configs.hawt(tmp_path / "h")
    hawt["rotating_zones"][0]["interface_level"] = 3

    assert "INTERFACE_LEVEL_IGNORED" not in codes(check_config(francis))
    assert "INTERFACE_LEVEL_IGNORED" not in codes(
        check_config(MachineProjectConfig.model_validate(hawt)))


# --- 6. duplicate or overlapping regions ---------------------------------------------------------

def test_edge_use_counts() -> None:
    box = trimesh.creation.box()
    open_box = trimesh.Trimesh(box.vertices, box.faces[:-1], process=False)

    assert edge_use_counts([box]) == (0, 0)
    assert edge_use_counts([box, box.copy()]) == (0, 18)  # every edge used four times
    assert edge_use_counts([open_box])[0] == 3


def test_duplicate_region_warns(tmp_path: Path) -> None:
    data = configs.duct(tmp_path / "geo")
    copy_path = tmp_path / "geo" / "wall_copy.stl"
    shutil.copy(tmp_path / "geo" / "wall.stl", copy_path)  # the wall twice
    data["domain"]["parts"][0]["files"].append(stl(copy_path))
    config = MachineProjectConfig.model_validate(data)

    issues = check_imported(config, read_imported(config))

    found = [i for i in issues if i.code == "IMPORTED_REGIONS_OVERLAP"]
    assert len(found) == 1 and found[0].severity.value == "WARNING"
    assert found[0].details["overused_edges"] > 0
    assert "IMPORTED_SURFACE_OPEN" not in codes(issues)


def test_closed_parts_do_not_warn(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.francis(tmp_path / "geo"))

    assert "IMPORTED_REGIONS_OVERLAP" not in codes(check_imported(config, read_imported(config)))
