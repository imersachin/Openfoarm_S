"""M1c items 1-3 (fake OpenFOAM, tests/fakes_msh.py; real v2512 in
tests/integration/test_m1_msh_real.py).

1. A flow-rate inlet's area measured from the imported mesh (the sum of its
   face areas), so it no longer blocks: INFO before the import, the measured
   area in the reports after it, BLOCKING only when it cannot be measured.
2. An AMI pair keeps the patches' own names (foamDictionary in place).
3. AMI weights of an imported mesh: WARNING outside 0.95-1.05 "at rest
   (0 deg)" (STL meshes keep 0.85-1.5), and the optional check over one
   revolution (moveDynamicMesh -checkAMI).
Plus the VAWT message (owner decision 3).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from case_setup.validation import check_physics
from core.workflow.run_lock import RunLock
from machines.config import MachineProjectConfig
from machines.msh_import.cases import MERGED_CASE, import_cases, used_names
from machines.msh_import.checks import face_area, patch_areas
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    AT_REST,
    CHECK_EXECUTABLES,
    IMPORT_EXECUTABLES,
    IMPORT_REPORT,
)
from machines.msh_import.revolution import (
    REVOLUTION_CASE,
    REVOLUTION_REPORT,
    mesh_hash,
    over_revolution,
    revolution_files,
)
from machines.pipeline import CASE_SETUP_REPORT, MESH_REPORT, MachinePipeline
from machines.service import MachineService, _CheckCache
from machines.validation import MachineThresholds, check_all
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner, write_mesh
from tests.fixtures.machines.configs import patch
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1, mesh_file

Data = dict[str, Any]
TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES, "moveDynamicMesh")
# The fake's patches: 10 triangles of box corners 0, 1, 2, each half of the
# rotating box's y-z face (2 m x 2 m): 10 x 2 m^2.
FAKE_PATCH_AREA = 20.0


class Setup:
    def __init__(self, tmp_path: Path, runner: FakeMshRunner | None = None) -> None:
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)
        self.runner = runner or FakeMshRunner()
        self.service = MachineService(
            tmp_path / "project", env=self.env, system_probe=lambda _: PLENTY,
            pipeline=MachinePipeline(self.runner, environment=self.env,
                                     system_probe=lambda _: PLENTY),
            check_cache=_CheckCache())
        self.root = self.service.root

    def upload(self, name: str = "rotating_41.msh") -> Path:
        return self.service.store_mesh_file(name, (M1 / name).read_bytes())

    def run(self, data: Data) -> Any:
        self.service.save(copy.deepcopy(data))
        config = self.service.saved_config()
        assert config is not None
        return self.service.pipeline.run_sync(self.root, config)

    def json(self, relative: str) -> Any:
        return json.loads((self.root / relative).read_text("utf-8"))


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def flow_rate(data: Data, rate: float = 40.0) -> Data:
    data["physics"]["inlets"] = {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": rate}}
    return data


def model(data: Data) -> MachineProjectConfig:
    return MachineProjectConfig.model_validate(copy.deepcopy(data))


# --- 1. the inlet area measured from the mesh -------------------------------------------------

def test_a_face_area_is_the_magnitude_of_its_area_vector() -> None:
    points = np.array([[0, 0, 0], [2, 0, 0], [2, 3, 0], [0, 3, 0], [1, 1.5, 1.0]], float)
    assert face_area(points, np.array([0, 1, 2, 3])) == pytest.approx(6.0)  # a 2 x 3 quad
    assert face_area(points, np.array([0, 1, 2])) == pytest.approx(3.0)
    # A warped quad: its projected (vector) area, as OpenFOAM's magSf.
    warped = np.array([[0, 0, 0], [1, 0, 0.5], [1, 1, 0], [0, 1, 0.5]], float)
    assert face_area(warped, np.arange(4)) == pytest.approx(1.0)


def test_patch_areas_sum_the_faces_of_each_patch(tmp_path: Path) -> None:
    write_mesh(tmp_path / "constant/polyMesh", [("inlet", "patch", 4), ("outlet", "patch", 0)],
               {}, 10, ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)))
    found = patch_areas(tmp_path, ["inlet", "outlet", "nothing"])
    assert found == {"inlet": pytest.approx(4 * 0.5 * 2.0 * 3.0)}  # no faces: left out


def test_before_the_import_a_flow_rate_inlet_is_info_not_blocking(tmp_path: Path) -> None:
    config = model(flow_rate(configs.rotating(mesh_file("rotating_41.msh", tmp_path))))
    found = {i.code: i.severity.value for i in check_physics(config, ())}

    assert found == {"INLET_AREA_AFTER_IMPORT": "INFO"}
    assert "INLET_AREA_UNKNOWN" not in {i.code for i in check_all(config).issues}


def test_an_area_that_cannot_be_measured_from_the_mesh_is_blocking(tmp_path: Path) -> None:
    config = model(flow_rate(configs.rotating(mesh_file("rotating_41.msh", tmp_path))))
    found = check_physics(config, (), mesh_areas={})  # imported: the inlet has no faces
    issue = next(i for i in found if i.code == "INLET_AREA_UNKNOWN")
    assert issue.severity.value == "BLOCKING" and "from the mesh" in issue.message


def test_the_run_measures_the_area_and_sets_up_the_case(setup: Setup) -> None:
    result = setup.run(flow_rate(configs.rotating(setup.upload())))

    assert result.succeeded, [i.message for i in result.issues]
    report = setup.json(CASE_SETUP_REPORT)
    assert report["inlet_area_m2"] == {"inlet": pytest.approx(FAKE_PATCH_AREA)}
    assert report["inlet_area_note"].startswith("measured from the mesh")
    assert report["inlets"]["inlet"]["speed"] == pytest.approx(40.0 / FAKE_PATCH_AREA)
    imported = setup.json(IMPORT_REPORT)
    assert imported["patch_area_m2"]["inlet"] == pytest.approx(FAKE_PATCH_AREA)
    assert "flowRateInletVelocity" in (setup.root / "case/0/U").read_text("utf-8")


def test_the_area_of_a_renamed_inlet_is_measured_under_its_new_name(setup: Setup) -> None:
    data = flow_rate(configs.rotating(setup.upload()))
    data["patches"][0] = patch("intake", "INLET", "MESH_PATCH", "inlet")
    data["physics"]["inlets"] = {"intake": data["physics"]["inlets"]["inlet"]}
    result = setup.run(data)

    assert result.succeeded, [i.message for i in result.issues]
    assert setup.json(CASE_SETUP_REPORT)["inlet_area_m2"] == {
        "intake": pytest.approx(FAKE_PATCH_AREA)}
    assert setup.json(IMPORT_REPORT)["names_used"]["inlet"] == "intake"


def test_the_service_measures_from_the_box_conversion_in_the_chosen_units(
        setup: Setup) -> None:
    path = setup.upload()
    config = model(configs.rotating(path, units="mm"))
    assert setup.service.imported_patch_areas(config) is None  # nothing converted yet
    assert setup.service.convert_for_box(path).issues == ()
    areas = setup.service.imported_patch_areas(config)  # the scratch mesh, at scale 1

    assert areas is not None and areas["inlet"] == pytest.approx(FAKE_PATCH_AREA * 1e-6)
    issues = {i.code for i in setup.service.validate(
        flow_rate(configs.rotating(path, units="mm"))).issues}
    assert "INLET_AREA_AFTER_IMPORT" not in issues and "INLET_AREA_UNKNOWN" not in issues


def test_after_the_import_the_service_uses_the_imported_mesh(setup: Setup) -> None:
    data = configs.rotating(setup.upload(), units="cm")
    assert setup.run(data).succeeded
    areas = setup.service.imported_patch_areas(model(data))
    assert areas is not None and areas["inlet"] == pytest.approx(FAKE_PATCH_AREA * 1e-4)
    # Units changed after the import: scaled from the mesh as imported.
    again = setup.service.imported_patch_areas(model(configs.rotating(
        Path(data["imported_mesh"]["source_path"]), units="m")))
    assert again is not None and again["inlet"] == pytest.approx(FAKE_PATCH_AREA)


def test_another_file_s_mesh_is_never_measured(setup: Setup, tmp_path: Path) -> None:
    assert setup.run(configs.rotating(setup.upload())).succeeded
    other = mesh_file("fluent_rotating.msh", tmp_path / "other")
    assert setup.service.imported_patch_areas(model(configs.rotating(other))) is None


# --- 2. AMI patches keep their names ----------------------------------------------------------

def test_the_ami_pair_is_set_in_place(tmp_path: Path) -> None:
    config = model(configs.rotating(mesh_file("rotating_41.msh", tmp_path)))
    cases = import_cases(config)
    steps = [s.argv for s in cases.merged_steps() if s.kind == "run"]

    assert "system/createPatchDict" not in cases.merged  # no rename in this project
    assert ("foamDictionary", "constant/polyMesh/boundary", "-entry",
            "entry0/ami_rot/type", "-set", "cyclicAMI") in steps
    assert ("foamDictionary", "constant/polyMesh/boundary", "-entry",
            "entry0/ami_rot/transform", "-add", "noOrdering") in steps
    assert cases.expected["ami_stat"] == cases.expected["ami_rot"] == "cyclicAMI"
    assert used_names(config)["ami_stat"] == "ami_stat"


def test_a_rename_still_uses_create_patch(tmp_path: Path) -> None:
    data = configs.rotating(mesh_file("rotating_41.msh", tmp_path))
    data["patches"][0] = patch("intake", "INLET", "MESH_PATCH", "inlet")
    cases = import_cases(model(data))
    text = cases.merged["system/createPatchDict"]
    assert "name intake;" in text and "cyclicAMI" not in text


# --- 3. AMI weights ---------------------------------------------------------------------------

def test_imported_meshes_warn_outside_095_105_at_rest(tmp_path: Path) -> None:
    assert MachineThresholds().ami_weights_range == (0.85, 1.5)  # STL meshes unchanged
    assert MachineThresholds().imported_ami_weights_range == (0.95, 1.05)
    inside = Setup(tmp_path / "a", FakeMshRunner(weights=(0.96, 1.04)))
    result = inside.run(configs.rotating(inside.upload()))
    assert result.succeeded
    assert "AMI_WEIGHTS_OUT_OF_RANGE" not in {i.code for i in result.issues}
    assert inside.json(MESH_REPORT)["ami_weights_when"] == AT_REST == "at rest (0 deg)"

    outside = Setup(tmp_path / "b", FakeMshRunner(weights=(0.99, 1.07)))
    result = outside.run(configs.rotating(outside.upload()))
    issue = next(i for i in result.issues if i.code == "AMI_WEIGHTS_OUT_OF_RANGE")
    assert result.succeeded and issue.severity.value == "WARNING"
    assert "at rest (0 deg)" in issue.message and issue.details["range"] == [0.95, 1.05]


def test_the_revolution_turns_the_zone_once_in_36_steps(tmp_path: Path) -> None:
    config = model(configs.rotating(mesh_file("rotating_41.msh", tmp_path)))
    files = revolution_files(config)
    dynamic = files["constant/dynamicMeshDict"]

    assert "cellZone        rotating;" in dynamic and "axis            (1.0 0.0 0.0);" in dynamic
    assert f"omega           {2 * math.pi!r};" in dynamic  # one revolution per second
    assert "endTime         1;" in files["system/controlDict"]
    assert "deltaT          0.02777777" in files["system/controlDict"]
    assert over_revolution() == "over one revolution (36 x 10 deg)"


def test_the_revolution_reports_every_step(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(weights=(0.99, 1.01), turning=(0.97, 1.11)))
    assert setup.run(configs.rotating(setup.upload())).succeeded
    result = setup.service.check_ami_revolution()

    assert result.done and result.steps == 36 and result.failed_steps == 0
    (pair,) = result.weights
    assert (pair.minimum, pair.maximum) == (0.97, 1.11)  # half way round, not the first step
    issue = next(i for i in result.issues if i.code == "AMI_WEIGHTS_OUT_OF_RANGE")
    assert issue.severity.value == "WARNING" and over_revolution() in issue.message
    report = setup.json(REVOLUTION_REPORT)
    assert report["mesh_sha256"] == mesh_hash(setup.root) and report["steps"] == 36
    assert not (setup.root / "cases" / REVOLUTION_CASE).exists()  # its VTK files too
    assert setup.runner.argv[-1] == ("moveDynamicMesh", "-case", ".", "-checkAMI")


def test_a_revolution_inside_the_range_has_no_warning(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(weights=(0.99, 1.01), turning=(0.96, 1.04)))
    assert setup.run(configs.rotating(setup.upload())).succeeded
    result = setup.service.check_ami_revolution()
    assert result.done and result.issues == ()


def test_an_unfinished_revolution_is_an_error(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(turned_steps=10))
    assert setup.run(configs.rotating(setup.upload())).succeeded
    result = setup.service.check_ami_revolution()
    assert not result.done
    assert [i.code for i in result.issues] == ["AMI_REVOLUTION_FAILED"]


def test_the_revolution_needs_the_imported_mesh(setup: Setup) -> None:
    setup.service.save(configs.rotating(setup.upload()))
    result = setup.service.check_ami_revolution()
    assert [i.code for i in result.issues] == ["AMI_REVOLUTION_UNAVAILABLE"]
    assert "moveDynamicMesh" not in setup.runner.names()


def test_the_revolution_takes_the_run_lock(setup: Setup) -> None:
    assert setup.run(configs.rotating(setup.upload())).succeeded
    lock = RunLock(setup.root)
    assert lock.acquire() is None
    try:
        result = setup.service.check_ami_revolution()
    finally:
        lock.release()
    assert not result.done and "moveDynamicMesh" not in setup.runner.names()


def test_the_revolution_hash_is_the_assembled_mesh(setup: Setup) -> None:
    assert setup.run(configs.rotating(setup.upload())).succeeded
    before = mesh_hash(setup.root)
    points = setup.root / "cases" / MERGED_CASE / "constant/polyMesh/points"
    points.write_text(points.read_text("utf-8") + "\n", encoding="utf-8")
    assert mesh_hash(setup.root) != before


# --- VAWT --------------------------------------------------------------------------------------

def test_a_vawt_project_is_told_to_use_a_custom_project(tmp_path: Path) -> None:
    data = configs.rotating(mesh_file("rotating_41.msh", tmp_path))
    data["machine"] = "VAWT"
    found = next(i for i in check_all(model(data)).issues
                 if i.code == "MSH_MACHINE_NOT_SUPPORTED")
    assert found.suggested_action == "To use an imported VAWT mesh, create a Custom project."


# --- fixes after the M1c testing review -------------------------------------------------------

def test_a_revolution_check_is_of_the_mesh_and_the_motion(tmp_path: Path) -> None:
    """MEDIUM: a changed axis, origin or cell zone leaves the mesh as it was."""
    setup = Setup(tmp_path)
    data = configs.rotating(setup.upload())
    assert setup.run(data).succeeded
    assert setup.service.check_ami_revolution().done
    report = setup.json(REVOLUTION_REPORT)
    assert setup.service.revolution_is_current(report)
    data["imported_mesh"]["zones"][0]["axis"] = "z"
    setup.service.save(copy.deepcopy(data))  # saved, the mesh unchanged
    assert not setup.service.revolution_is_current(report)
    data["imported_mesh"]["zones"][0]["axis"] = "x"
    data["imported_mesh"]["zones"][0]["origin"] = {"x": 0.0, "y": 0.1, "z": 0.0}
    setup.service.save(copy.deepcopy(data))
    assert not setup.service.revolution_is_current(report)
    assert setup.service.check_ami_revolution().done
    assert setup.service.revolution_is_current(setup.json(REVOLUTION_REPORT))


def test_an_ami_patch_loses_its_converter_group(tmp_path: Path) -> None:
    """LOW: Fluent wall zones get inGroups 1(wall); an interface must not."""
    config = model(configs.rotating(mesh_file("rotating_41.msh", tmp_path)))
    steps = [s.argv for s in import_cases(config).merged_steps() if s.kind == "run"]
    for side in ("ami_stat", "ami_rot"):
        assert ("foamDictionary", "constant/polyMesh/boundary", "-entry",
                f"entry0/{side}/inGroups", "-remove") in steps


def test_only_the_rotor_walls_of_the_zone_are_measured(tmp_path: Path) -> None:
    """LOW: two zones, each with its own rotor wall."""
    from machines.tip_radius import zone_patch_points
    write_mesh(tmp_path / "constant/polyMesh", [("rotor_a", "wall", 2), ("rotor_b", "wall", 2)],
               {"a": 2, "b": 2}, 4, ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
    # Face i is owned by cell i: rotor_a's faces by zone a's cells 0 and 1.
    found = zone_patch_points(tmp_path, ["rotor_a", "rotor_b"], np.array([0, 1]))
    assert found is not None and found[0] == ("rotor_a",)
    assert zone_patch_points(tmp_path, ["rotor_b"], np.array([0, 1])) is None


def test_the_answered_area_info_is_not_left_in_the_run(setup: Setup) -> None:
    """LOW: INLET_AREA_AFTER_IMPORT before the import, gone once measured."""
    result = setup.run(flow_rate(configs.rotating(setup.upload())))
    assert result.succeeded
    assert "INLET_AREA_AFTER_IMPORT" not in {i.code for i in result.issues}
