"""G5 without OpenFOAM: joints (K1, K2), imported zones (K3), the Francis preset
(K4, K5), the WALL-in-rotating-zone warning (K6), region refinement, and the
pipeline with a fake OpenFOAM. Geometry: the synthetic G0 R4 passage."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pydantic
import pytest
import trimesh

from machines.assembly import MachineCaseGenerator, joint_levels, meshing_steps
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.joints import JointLimits, measure_joint, propose_joints
from machines.operations import ASSEMBLE, CHECK_MESH, MACHINE_EXECUTABLES, mesh_operation
from machines.pipeline import MESH_REPORT, MachinePipeline, MachineRunResult
from machines.preflight import estimate_cells
from machines.presets.francis import (
    FRANCIS_VALUES,
    francis_draft,
    inner_point,
    with_types,
)
from machines.validation import MachineThresholds, load_bodies, validate_machine
from machines.zones import generated_names, interfaces
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from tests.fixtures.machines.configs import patch, stl, vec
from tests.fixtures.machines.g0.common.make_geometry import disc, side, write_ascii

GEN = MachineCaseGenerator()
PARTS = ("casing", "guide", "draft")
TYPES = {"inlet": "INLET", "outlet": "OUTLET", "casing_wall": "WALL", "guide_wall": "WALL",
         "vanes": "WALL", "runner_wall": "ROTATING_WALL", "draft_wall": "WALL",
         "runner_blades": "ROTATING_WALL"}


def render(data: dict[str, Any], typed: bool = True):  # type: ignore[no-untyped-def]
    config = MachineProjectConfig.model_validate(data)
    meshes, _ = load_bodies(config)
    return GEN.render(config, read_imported(config), meshes, typed=typed)


def found(data: dict[str, Any], thresholds: MachineThresholds | None = None
          ) -> list[tuple[str, str]]:
    return [(i.code, i.severity.value) for i in validate_machine(data, thresholds).issues
            if i.code != "MULTIPLE_COMPONENTS"]


def issue(data: dict[str, Any], code: str):  # type: ignore[no-untyped-def]
    return next(i for i in validate_machine(data).issues if i.code == code)


@pytest.fixture
def francis(tmp_path: Path) -> dict[str, Any]:
    return configs.francis(tmp_path / "geometry")


# --- joint names (K1) ------------------------------------------------------------------------

def test_joints_are_named_after_their_regions(francis: dict[str, Any]) -> None:
    config = MachineProjectConfig.model_validate(francis)
    names = generated_names(config)

    assert {"casing_out", "casing_out_src", "draft_in", "draft_in_src"} <= set(names)
    assert names["guide_in"] == "joint 'casing_out' / 'guide_in'"
    assert interfaces(config.rotating_zones[0]) == ()  # an imported zone has no interface


def test_patch_named_like_a_joint_region_is_refused(francis: dict[str, Any]) -> None:
    francis["patches"][2]["name"] = "guide_in"  # casing_wall's patch

    assert ("RESERVED_PATCH_NAME", "BLOCKING") in found(francis)


# --- the cases (K1, K3) ----------------------------------------------------------------------

def test_cases_mesh_joints_as_source_patches(francis: dict[str, Any]) -> None:
    cases = render(francis)

    assert [c.name for c in cases.domain] == [f"domain_{p}" for p in PARTS]
    assert [c.name for c in cases.zones] == ["zone_runner"]
    guide = cases.domain[1]
    stl_text = guide.surfaces["constant/triSurface/guide.stl"].decode()
    assert [line for line in stl_text.splitlines() if line.startswith("solid")] == [
        "solid guide_in_src", "solid guide_wall", "solid guide_out_src", "solid vanes"]
    assert guide.expected == {"guide_in_src": "patch", "guide_wall": "wall",
                              "guide_out_src": "patch", "vanes": "wall"}
    snappy = guide.dictionaries["system/snappyHexMeshDict"]
    assert "guide_in_src { level (1 1); patchInfo { type patch; } }" in snappy  # G0 R4
    assert "vanes { level (0 0); patchInfo { type wall; } }" in snappy
    runner = cases.zones[0]
    assert "name    runner;" in runner.dictionaries["system/topoSetDict"]
    assert "locationInMesh      (0.2013 0.0113 0.0013);" in runner.dictionaries[
        "system/snappyHexMeshDict"]
    assert runner.owner == "rotating_zones.0" and runner.cut


def test_merged_case_pairs_every_joint(francis: dict[str, Any]) -> None:
    cases = render(francis, typed=False)
    merged = cases.merged

    assert [(p.first, p.second) for p in cases.pairs] == [
        ("casing_out", "guide_in"), ("guide_out", "runner_in"), ("runner_out", "draft_in")]
    create = merged.dictionaries["system/createPatchDict"]
    assert "name casing_out;" in create and "patches (casing_out_src);" in create
    assert "neighbourPatch  guide_in;" in create
    assert {n for n, t in merged.expected.items() if t == "cyclicAMI"} == {
        "casing_out", "guide_in", "guide_out", "runner_in", "runner_out", "draft_in"}
    assert not any(n.endswith("_src") for n in merged.expected)
    assert cases.retype["runner_blades"] == "wall" and "runner_in" not in cases.retype
    steps = [s.name for s in meshing_steps(cases)]
    assert steps.index("zone_runner_topoSet") < steps.index("merged_copy")
    assert [s for s in steps if s.startswith("merged_add")] == [
        "merged_add_domain_guide", "merged_add_domain_draft", "merged_add_zone_runner"]


def test_coarser_side_of_a_joint_gets_the_finer_cells(francis: dict[str, Any]) -> None:
    francis["domain"]["parts"][1]["cell_size"] = 0.01  # the guide: half the casing's cells
    config = MachineProjectConfig.model_validate(francis)

    levels = joint_levels(config, read_imported(config))

    assert levels["guide_in"] == 1 and levels["casing_out"] == 2
    assert levels["guide_out"] == 1 and levels["runner_in"] == 2
    assert levels["runner_out"] == levels["draft_in"] == 1


def test_joint_level_is_configurable(francis: dict[str, Any]) -> None:
    francis["joints"][2]["level"] = 3
    config = MachineProjectConfig.model_validate(francis)

    assert joint_levels(config, read_imported(config))["draft_in"] == 3


def test_region_refinement(francis: dict[str, Any]) -> None:
    francis["domain"]["parts"][1]["refinement"] = {"vanes": 2}
    francis["rotating_zones"][0]["shape"]["refinement"] = {"runner_blades": 2}
    cases = render(francis)

    guide = cases.domain[1].dictionaries["system/snappyHexMeshDict"]
    assert "vanes { level (2 2); patchInfo { type wall; } }" in guide
    assert "level 2;" in guide  # feature edges at the finest region level
    assert "runner_blades { level (2 2);" in cases.zones[0].dictionaries[
        "system/snappyHexMeshDict"]


def test_imported_zone_needs_an_imported_domain(tmp_path: Path) -> None:
    hawt = configs.hawt(tmp_path / "h")
    hawt["rotating_zones"][0]["shape"] = configs.francis(tmp_path / "f")[
        "rotating_zones"][0]["shape"]

    assert ("IMPORTED_ZONE_NEEDS_IMPORTED_DOMAIN", "BLOCKING") in found(hawt)
    with pytest.raises(ValueError, match="imported domain"):
        render(hawt)


# --- joint coincidence (K2) --------------------------------------------------------------------

def test_coincident_joints_pass(francis: dict[str, Any]) -> None:
    assert "JOINT_REGIONS_DO_NOT_COINCIDE" not in [c for c, _ in found(francis)]


def test_moved_part_breaks_its_joints_and_reports_the_measures(
        francis: dict[str, Any]) -> None:
    francis["domain"]["parts"][1]["files"][0]["translation"] = vec(0.0, 0.0, 0.02)

    issues = [i for i in validate_machine(francis).issues
              if i.code == "JOINT_REGIONS_DO_NOT_COINCIDE"]

    assert [i.details["field"] for i in issues] == ["joints.0", "joints.1"]
    first = issues[0]
    assert first.severity.value == "BLOCKING"
    assert first.details["area_difference"] == pytest.approx(0.0, abs=1e-6)
    assert first.details["max_distance"] == pytest.approx(0.02, abs=0.002)
    assert first.details["max_distance_limit"] == pytest.approx(0.01)  # 0.5 cell of 0.02
    assert "0.02" in first.message and "limit" in first.message


def test_area_difference_breaks_a_joint(tmp_path: Path, francis: dict[str, Any]) -> None:
    # The draft tube's inlet disc r 0.29 against the runner's outlet r 0.3.
    write_ascii(tmp_path / "geometry/draft.stl", [
        ("draft_in", disc(0.29, 0.1, up=False)), ("draft_wall", side(0.3, 0.1, 0.45, 0.9)),
        ("outlet", disc(0.45, 0.9, up=True))])

    joint = issue(francis, "JOINT_REGIONS_DO_NOT_COINCIDE")

    assert joint.details["field"] == "joints.2"
    assert joint.details["area_difference"] == pytest.approx(1 - (0.29 / 0.3) ** 2, rel=0.02)
    assert "6.5" in joint.message or "6.6" in joint.message


def test_limits_are_configurable(francis: dict[str, Any]) -> None:
    francis["domain"]["parts"][1]["files"][0]["translation"] = vec(0.0, 0.0, 0.02)
    loose = MachineThresholds(joints=JointLimits(max_distance_cells=1.5))

    assert "JOINT_REGIONS_DO_NOT_COINCIDE" not in [c for c, _ in found(francis, loose)]


def test_measure_joint() -> None:
    a = disc(0.3, 0.0, up=True)
    smaller = disc(0.29, 0.0, up=False)

    same = measure_joint(a, disc(0.3, 0.0, up=False), 0.02)
    other = measure_joint(a, smaller, 0.02)

    assert same.area_difference == pytest.approx(0.0, abs=1e-9)
    assert same.max_distance < 0.25 * 0.02
    assert other.area_difference == pytest.approx(1 - (0.29 / 0.3) ** 2, rel=1e-2)
    assert other.max_distance == pytest.approx(0.01, abs=0.002)
    assert set(other.as_details()) == {"first_area", "second_area", "area_difference",
                                       "max_distance", "sample_spacing"}


def test_proposed_joints(francis: dict[str, Any]) -> None:
    config = MachineProjectConfig.model_validate(francis)
    surfaces = read_imported(config)

    proposed = propose_joints(surfaces, {s.owner: 0.02 for s in surfaces})

    # Zones are read first (read_imported), so the runner's regions come first.
    assert {frozenset((p.first, p.second)) for p in proposed} == {
        frozenset((j["first"], j["second"])) for j in francis["joints"]}


# --- bodies in imported zones and region checks --------------------------------------------------

def _with_body(francis: dict[str, Any], tmp_path: Path, centre: tuple[float, float, float],
               motion: str) -> dict[str, Any]:
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=0.005)
    sphere.apply_translation(centre)
    sphere.export(tmp_path / "pin.stl")
    body: dict[str, Any] = {"name": "pin", "source": stl(tmp_path / "pin.stl"),
                            "motion": motion}
    if motion != "STATIONARY":
        body["zone"] = "runner"
    data = copy.deepcopy(francis)
    data["bodies"] = [body]
    data["patches"].append(patch("pin", "ROTATING_WALL" if motion == "ROTATING" else "WALL",
                                 "BODY", "pin"))
    return data


def test_rotating_body_inside_its_imported_zone(tmp_path: Path, francis: dict[str, Any]) -> None:
    data = _with_body(francis, tmp_path, (0.15, 0.0, 0.09), "ROTATING")

    codes = [c for c, _ in found(data)]
    assert "ROTATING_BODY_OUTSIDE_ZONE" not in codes and "BODY_OUTSIDE_DOMAIN" not in codes
    zone = render(data).zones[0]
    assert "constant/triSurface/pin.stl" in zone.surfaces and zone.expected["pin"] == "wall"


def test_rotating_body_outside_its_imported_zone(tmp_path: Path,
                                                 francis: dict[str, Any]) -> None:
    data = _with_body(francis, tmp_path, (0.15, 0.0, 0.5), "ROTATING")  # in the draft tube

    assert ("ROTATING_BODY_OUTSIDE_ZONE", "BLOCKING") in found(data)


def test_stationary_body_in_an_imported_zone(tmp_path: Path, francis: dict[str, Any]) -> None:
    data = _with_body(francis, tmp_path, (0.15, 0.0, 0.09), "STATIONARY")

    assert ("STATIONARY_BODY_CROSSES_INTERFACE", "BLOCKING") in found(data)


def test_split_body_in_an_imported_zone(tmp_path: Path, francis: dict[str, Any]) -> None:
    data = _with_body(francis, tmp_path, (0.15, 0.0, 0.09), "SPLIT")

    assert ("BODY_MOTION_IN_IMPORTED_ZONE", "BLOCKING") in found(data)


def test_wall_in_rotating_zone_warns(francis: dict[str, Any]) -> None:
    warning = issue(francis, "WALL_IN_ROTATING_ZONE")

    assert warning.severity.value == "WARNING"
    assert warning.details["patch"] == "runner_wall" and warning.details["zone"] == "runner"
    rotating = copy.deepcopy(francis)
    rotating["patches"][5]["type"] = "ROTATING_WALL"  # runner_wall
    assert found(rotating) == []


def test_refinement_names_regions_of_its_surface(francis: dict[str, Any]) -> None:
    unknown = copy.deepcopy(francis)
    unknown["domain"]["parts"][1]["refinement"] = {"blades": 2}
    joint = copy.deepcopy(francis)
    joint["domain"]["parts"][1]["refinement"] = {"guide_in": 2}

    assert ("REFINEMENT_REGION_UNKNOWN", "BLOCKING") in found(unknown)
    assert ("REFINEMENT_ON_JOINT", "BLOCKING") in found(joint)


def test_preflight_counts_the_imported_zone(francis: dict[str, Any]) -> None:
    config = MachineProjectConfig.model_validate(francis)

    estimate = estimate_cells(config, {}, read_imported(config))

    assert set(estimate.per_case) == {"domain_casing", "domain_guide", "domain_draft",
                                      "zone_runner"}


# --- the preset (K4, K5) ---------------------------------------------------------------------

def _base(directory: Path) -> dict[str, Any]:
    def surface(name: str) -> dict[str, Any]:
        return {"format": "NAMED_REGIONS", "files": [stl(directory / f"{name}.stl")]}

    return {"project_name": "Francis", "axis": "z", "origin": vec(0.0, 0.0, 0.0),
            "runner": surface("runner"), "parts": [{"name": p, **surface(p)} for p in PARTS]}


def test_draft(tmp_path: Path, francis: dict[str, Any]) -> None:
    draft = francis_draft(_base(tmp_path / "geometry"), 0.6)
    data = draft.data

    assert [(j.first, j.second) for j in draft.joints] == [
        ("casing_out", "guide_in"), ("guide_out", "runner_in"), ("draft_in", "runner_out")]
    assert draft.outlet_diameters == pytest.approx({"runner_in": 0.6, "runner_out": 0.6})
    assert draft.suggested_types == {"inlet": "INLET", "outlet": "OUTLET"}
    zone, parts = data["rotating_zones"][0], data["domain"]["parts"]
    assert zone["cell_size"] == pytest.approx(0.025) and zone["axis"] == "z"
    assert all(p["cell_size"] == pytest.approx(0.025) for p in parts)
    assert zone["shape"]["refinement"] == {"runner_blades": 2}
    assert [p["refinement"] for p in parts] == [{}, {"vanes": 2}, {}]
    assert sorted(p["name"] for p in data["patches"]) == sorted(TYPES)
    assert all("type" not in p for p in data["patches"])  # E5: never typed by the preset
    assert "unverified" in FRANCIS_VALUES["cell"].source


def test_draft_is_blocking_until_types_are_chosen(tmp_path: Path) -> None:
    configs.francis(tmp_path / "geometry")
    draft = francis_draft(_base(tmp_path / "geometry"), 0.6)

    untyped = validate_machine(draft.data)
    typed = validate_machine(with_types(draft.data, TYPES))

    assert untyped.config is None
    assert {i.code for i in untyped.issues} == {"PATCH_TYPE_NOT_CHOSEN"}
    # Mesh points inside every surface, off the cell faces, outside the vanes and blades.
    assert [(i.code, i.severity.value) for i in typed.issues] == []


def test_mesh_point_is_inside_and_away_from_the_surface(francis: dict[str, Any]) -> None:
    config = MachineProjectConfig.model_validate(francis)
    guide = next(s for s in read_imported(config) if s.name == "guide")

    point = inner_point(guide, 0.025)

    assert -0.3 < point[2] < -0.1 and (point[0] ** 2 + point[1] ** 2) ** 0.5 < 0.3


def test_draft_needs_units_and_a_diameter(tmp_path: Path) -> None:
    configs.francis(tmp_path / "geometry")
    base = _base(tmp_path / "geometry")
    del base["parts"][0]["files"][0]["source_units"]

    with pytest.raises(pydantic.ValidationError):
        francis_draft(base, 0.6)
    with pytest.raises(ValueError, match="diameter"):
        francis_draft(_base(tmp_path / "geometry"), 0.0)


def test_draft_refuses_an_open_surface(tmp_path: Path) -> None:
    configs.francis(tmp_path / "geometry")
    path = tmp_path / "geometry/casing.stl"
    text = path.read_text()
    path.write_text(text[:text.index("solid casing_out")])  # drop the outlet disc

    with pytest.raises(ValueError, match="casing"):
        francis_draft(_base(tmp_path / "geometry"), 0.6)


# --- the pipeline with a fake OpenFOAM ---------------------------------------------------------

MESHES = {*(mesh_operation(f"domain_{p}") for p in PARTS), mesh_operation("zone_runner")}


class Project:
    """As test_machine_pipeline.Project: a project root and a fake environment."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)

    def run(self, data: dict[str, Any], runner: FakeMachineRunner | None = None
            ) -> tuple[MachineRunResult, FakeMachineRunner]:
        runner = runner or FakeMachineRunner()
        pipeline = MachinePipeline(runner, environment=self.env, system_probe=lambda _: PLENTY)
        return pipeline.run_sync(self.root, MachineProjectConfig.model_validate(data)), runner

    def report(self) -> dict[str, Any]:
        return dict(json.loads((self.root / MESH_REPORT).read_text("utf-8")))


def test_pipeline_meshes_francis(tmp_path: Path, francis: dict[str, Any]) -> None:
    project = Project(tmp_path)

    result, runner = project.run(francis)

    assert result.succeeded, [(i.code, i.message) for i in result.issues if i.is_stopping]
    assert [c for c, case in runner.calls if case == "zone_runner"] == [
        "blockMesh", "surfaceFeatureExtract", "snappyHexMesh", "topoSet"]
    assert [c for c, case in runner.calls if case == "merged"].count("mergeMeshes") == 3
    report = project.report()
    assert report["regions"] == {"expected": 4, "found": 4}
    assert [(w["source"], w["target"]) for w in report["ami_weights"]] == [
        ("casing_out", "guide_in"), ("guide_out", "runner_in"), ("runner_out", "draft_in")]


def test_pipeline_reuses_every_untouched_part(tmp_path: Path, francis: dict[str, Any]) -> None:
    project = Project(tmp_path)
    project.run(francis)

    francis["domain"]["parts"][2]["refinement"] = {"draft_wall": 1}
    draft, _ = project.run(francis)
    francis["joints"][1]["level"] = 2  # guide_out / runner_in
    joint, _ = project.run(francis)

    assert set(draft.reused) == MESHES - {mesh_operation("domain_draft")}
    assert {mesh_operation("domain_draft"), ASSEMBLE, CHECK_MESH} <= set(draft.executed)
    assert set(joint.reused) == {mesh_operation("domain_casing"),
                                 mesh_operation("domain_draft")}


def test_pipeline_reports_a_missing_joint_patch(tmp_path: Path, francis: dict[str, Any]) -> None:
    project = Project(tmp_path)
    result, _ = project.run(francis, FakeMachineRunner(wrong_region=["domain_guide"]))

    assert not result.succeeded
    assert any(i.code == "DOMAIN_PATCH_MISSING" and i.details.get("patch") == "guide_in_src"
               for i in result.issues)
