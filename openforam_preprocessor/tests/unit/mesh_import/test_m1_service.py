"""M1-MSH: what MachineService offers for an imported mesh (fake OpenFOAM).

Upload (with the 200 MB limit), inspection and suggestions, the scratch
conversion for a box under the run lock, plan and preflight, the zone radius
computed from the zone, check reuse by file identity, case setup's files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from case_setup.case import CASE_DIR
from core.workflow.run_lock import RunLock
from machines import service as service_module
from machines.config import PatchType
from machines.msh_import import detect
from machines.msh_import.inspection import BOX_FROM_CONVERSION, BOX_FROM_FILE
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT,
    IMPORT_EXECUTABLES,
)
from machines.pipeline import CASE_SETUP_REPORT, MachinePipeline
from machines.service import MachineService, _CheckCache
from mesh.estimator import ResourceStatus
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1

TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES)


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)
        self.runner = FakeMshRunner()
        self.checks = _CheckCache()
        self.service = MachineService(
            tmp_path / "project", env=self.env, system_probe=lambda _: PLENTY,
            pipeline=MachinePipeline(self.runner, environment=self.env,
                                     system_probe=lambda _: PLENTY),
            check_cache=self.checks)

    def upload(self, name: str) -> Path:
        return self.service.store_mesh_file(name, (M1 / name).read_bytes())


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_an_upload_goes_to_inputs_mesh_under_any_name(setup: Setup) -> None:
    path = setup.service.store_mesh_file("../../rotor.cas", b"(0 \"x\")\n")
    assert path == setup.service.root / "inputs" / "mesh" / "rotor.cas"


def test_an_upload_above_the_limit_is_refused(setup: Setup,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "MAX_MESH_UPLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="give its path"):
        setup.service.store_mesh_file("big.msh", b"x" * 11)


def test_inspection_reads_the_box_from_an_ascii_file(setup: Setup) -> None:
    found = setup.service.inspect_mesh(setup.upload("rotating_41.msh"))

    assert found.box == ((-1.0, -1.0, -1.0), (3.0, 1.0, 1.0))
    assert found.box_source == BOX_FROM_FILE
    assert found.suggested_types == {"inlet": PatchType.INLET, "outlet": PatchType.OUTLET}
    assert found.suggested_pairs == (("ami_stat", "ami_rot",
                                      "names ending in '_stat' and '_rot'"),)
    assert found.sha256 is not None and setup.runner.calls == []


def test_fluent_types_are_suggested_only_when_distinctive(setup: Setup) -> None:
    found = setup.service.inspect_mesh(setup.upload("fluent_box.msh"))
    # walls: Fluent "wall"; inlet and outlet by name; pressure-outlet suggests nothing.
    assert found.suggested_types == {"inlet": PatchType.INLET, "outlet": PatchType.OUTLET,
                                     "walls": PatchType.WALL}


def test_two_fluent_interface_zones_are_offered_as_a_pair(setup: Setup,
                                                          tmp_path: Path) -> None:
    text = (M1 / "fluent_rotating.msh").read_text("latin-1")
    text = (text.replace("pressure-outlet ami_stat)", "interface side_a)")
            .replace("pressure-outlet ami_rot)", "interface side_b)"))
    path = setup.service.store_mesh_file("interfaces.msh", text.encode("latin-1"))
    found = setup.service.inspect_mesh(path)
    assert found.suggested_pairs == (("side_a", "side_b", "the two Fluent interface zones"),)


def test_a_binary_file_gets_its_box_from_one_scratch_conversion(
        setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "gmsh_available", lambda: True)
    path = setup.upload("box_22_bin.msh")
    before = setup.service.inspect_mesh(path)
    assert before.box is None and before.box_source == ""

    scratch = setup.service.convert_for_box(path)
    after = setup.service.inspect_mesh(path)

    assert scratch.issues == () and scratch.converter == "gmshToFoam"
    # The box of the converted mesh's boundary surface (M2a: the mesh is binary).
    assert setup.runner.names() == ["resave", "gmshToFoam", "topoSet", "surfaceMeshExtract"]
    assert after.box == scratch.box and after.box_source == BOX_FROM_CONVERSION


def test_the_scratch_conversion_takes_the_run_lock(setup: Setup) -> None:
    path = setup.upload("box_22.msh")
    lock = RunLock(setup.service.root)
    assert lock.acquire() is None
    try:
        result = setup.service.convert_for_box(path)
    finally:
        lock.release()
    assert result.box is None and setup.runner.calls == []
    assert [i.code for i in result.issues] == ["RUN_IN_PROGRESS"]


def test_an_unreadable_file_is_not_converted_for_its_box(setup: Setup) -> None:
    path = setup.upload("box_22_nogroups.msh")
    result = setup.service.convert_for_box(path)
    assert [i.code for i in result.issues] == ["GMSH_NO_PHYSICAL_GROUPS"]
    assert setup.runner.calls == []


def test_plan_and_preflight_follow_the_imported_mesh(setup: Setup) -> None:
    data = configs.rotating(setup.upload("fluent_rotating.msh"))
    plan = setup.service.plan(data)
    preflight = setup.service.preflight(data)

    assert plan.operations[1] == IMPORT and plan.case_setup
    assert preflight.assessment is not None
    assert preflight.assessment.status is ResourceStatus.SAFE
    assert preflight.estimate is not None
    assert preflight.estimate.background == {"imported": 3383}  # the file's declared cells


def test_a_binary_gmsh_file_is_not_assessed_before_converting(setup: Setup) -> None:
    data = configs.box(setup.upload("box_22_bin.msh"))
    found = setup.service.preflight(data)
    assert found.assessment is None
    assert [i.code for i in found.issues] == ["RESOURCE_NOT_ASSESSED"]


def test_the_checks_are_reused_until_the_file_changes(setup: Setup) -> None:
    path = setup.upload("rotating_41.msh")
    data = configs.rotating(path)
    setup.service.validate(data)
    setup.service.validate(data)
    assert setup.checks.runs == 1
    path.write_bytes(path.read_bytes() + b"\n")
    found = setup.service.validate(data)
    assert setup.checks.runs == 2
    assert "MSH_SOURCE_CHANGED" in {i.code for i in found.issues}


def test_the_zone_radius_is_computed_from_the_zone_once_imported(setup: Setup) -> None:
    data = configs.rotating(setup.upload("rotating_41.msh"))
    assert setup.service.zone_radius(data, "rotor_zone") is None  # not imported yet
    setup.service.save(data)
    config = setup.service.saved_config()
    assert config is not None
    result = setup.service.pipeline.run_sync(setup.service.root, config)
    assert result.succeeded, result.message

    radius = setup.service.zone_radius(data, "rotor_zone")
    # The fake's points are the box corners: distance from the x axis sqrt(2).
    assert radius == pytest.approx(2**0.5)
    report = json.loads((setup.service.root / CASE_SETUP_REPORT).read_text("utf-8"))
    assert report["rotation"]["zone"] == "rotating"  # the mesh's cell zone
    other = configs.rotating(setup.upload("fluent_rotating.msh"))
    assert setup.service.zone_radius(other, "rotor_zone") is None  # another file


def test_case_setup_writes_the_cell_zone_of_the_mesh(setup: Setup) -> None:
    data = configs.rotating(setup.upload("rotating_41.msh"))
    setup.service.save(data)
    config = setup.service.saved_config()
    assert config is not None
    setup.service.pipeline.run_sync(setup.service.root, config)
    dynamic = (setup.service.root / CASE_DIR / "constant/dynamicMeshDict").read_text("utf-8")

    assert "cellZone        rotating;" in dynamic
    assert "origin          (0.0 0.0 0.0);" in dynamic
    assert "axis            (1.0 0.0 0.0);" in dynamic


def test_patch_geometry_is_the_evidence_for_a_pair(setup: Setup) -> None:
    data: dict[str, Any] = configs.rotating(setup.upload("rotating_41.msh"))
    digest = data["imported_mesh"]["sha256"]
    assert setup.service.mesh_patch_geometry(["ami_stat"], digest) == {}
    setup.service.save(data)
    config = setup.service.saved_config()
    assert config is not None
    setup.service.pipeline.run_sync(setup.service.root, config)
    found = setup.service.mesh_patch_geometry(["ami_stat", "ami_rot", "nothing"], digest)
    assert setup.service.mesh_patch_geometry(["ami_stat"], "0" * 64) == {}  # another file

    assert set(found) == {"ami_stat", "ami_rot"}
    assert found["ami_stat"].faces == 10 and found["ami_stat"].area > 0
