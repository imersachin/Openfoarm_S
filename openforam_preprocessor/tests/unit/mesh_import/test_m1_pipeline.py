"""M1-MSH: an imported-mesh run with a fake OpenFOAM (tests/fakes_msh.py).

The order of commands, what each change re-runs, the checks on what the
converter wrote (exit code 0 is not success), the scratch conversion's
promotion, and case setup on the imported mesh (invariant 14).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from case_setup.case import CASE_DIR
from machines.config import MachineProjectConfig
from machines.msh_import import detect
from machines.msh_import.cases import IMPORTED_CASE, SCRATCH_CASE
from machines.msh_import.detect import file_sha256, inspect_mesh_file
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT,
    IMPORT_EXECUTABLES,
    IMPORT_REPORT,
    convert_scratch,
)
from machines.operations import ASSEMBLE, CASE_SETUP, CHECK_MESH, VALIDATE, VALIDATE_MESH
from machines.pipeline import MESH_REPORT, MachinePipeline, MachineRunResult
from openfoam.runner import RunStatus
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.machines.configs import patch
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import mesh_file
from vawt.status import RunState

Data = dict[str, Any]
TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES)
FULL = [VALIDATE, IMPORT, ASSEMBLE, CHECK_MESH, VALIDATE_MESH, CASE_SETUP]
# 7 patch types, then 4 entries for each side of the AMI pair (M1c: in place).
ORDER = ["gmshToFoam", *["foamDictionary"] * (7 + 8), "checkMesh", "postProcess"]


class Project:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)

    def pipeline(self, runner: FakeMshRunner) -> MachinePipeline:
        return MachinePipeline(runner, environment=self.env, system_probe=lambda _: PLENTY)

    def run(self, data: Data, runner: FakeMshRunner | None = None,
            **kwargs: Any) -> tuple[MachineRunResult, FakeMshRunner]:
        runner = runner or FakeMshRunner()
        config = MachineProjectConfig.model_validate(copy.deepcopy(data))
        return self.pipeline(runner).run_sync(self.root, config, **kwargs), runner

    def json(self, relative: str) -> Any:
        return json.loads((self.root / relative).read_text("utf-8"))

    def digest(self, case: str) -> dict[str, str]:
        mesh = self.root / "cases" / case / "constant" / "polyMesh"
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(mesh.iterdir()) if p.is_file()}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


@pytest.fixture
def gmsh(tmp_path: Path) -> Data:
    return configs.rotating(mesh_file("rotating_41.msh", tmp_path / "inputs"))


@pytest.fixture
def fluent(tmp_path: Path) -> Data:
    return configs.rotating(mesh_file("fluent_rotating.msh", tmp_path / "inputs"))


def codes(result: MachineRunResult) -> dict[str, str]:
    return {i.code: i.severity.value for i in result.issues}


# --- the normal path --------------------------------------------------------------------

def test_a_gmsh_mesh_is_imported_paired_typed_checked_and_set_up(
        project: Project, gmsh: Data) -> None:
    result, runner = project.run(gmsh)

    assert result.succeeded and result.state is RunState.SUCCEEDED, result.message
    assert list(result.executed) == FULL
    assert runner.names() == ORDER
    assert runner.argv[0][:2] == ("gmshToFoam", gmsh["imported_mesh"]["source_path"])
    boundary = (project.root / "cases/merged/constant/polyMesh/boundary").read_text("utf-8")
    assert "_ami" not in boundary  # the AMI pair keeps the patches' own names (M1c)
    assert not (project.root / "cases/merged/system/createPatchDict").exists()
    assert ("foamDictionary", "constant/polyMesh/boundary", "-entry",
            "entry0/ami_stat/neighbourPatch", "-add", "ami_rot") in runner.argv
    report = project.json(IMPORT_REPORT)
    assert set(report["patches"]) >= {"ami_stat", "ami_rot", "rotor"}  # as converted
    assert report["names_used"]["ami_stat"] == "ami_stat"
    assert (project.root / CASE_DIR / "constant/dynamicMeshDict").is_file()
    assert result.mesh is not None and result.mesh.status.value == "SUCCESS"


def test_a_fluent_mesh_uses_fluent3d_only(project: Project, fluent: Data) -> None:
    result, runner = project.run(fluent)

    assert result.succeeded, result.message
    assert runner.names()[0] == "fluent3DMeshToFoam"
    assert "fluentMeshToFoam" not in runner.names()


def test_the_import_report_records_the_converter_and_the_openfoam_version(
        project: Project, gmsh: Data) -> None:
    project.run(gmsh)
    report = project.json(IMPORT_REPORT)

    assert report["converter"] == "gmshToFoam" and report["openfoam"]["version"] == "v2512"
    assert report["sha256"] == gmsh["imported_mesh"]["sha256"]
    assert report["units"] == "m" and report["scale_to_metres"] == 1.0
    assert report["cell_zones"] == ["rotating", "stationary"]
    assert "computed from the zone" in report["zone_radius_note"]
    assert project.json(MESH_REPORT)["imported"]["converter"] == "gmshToFoam"


def test_units_are_applied_after_conversion_with_transform_points(
        project: Project, tmp_path: Path) -> None:
    data = configs.rotating(mesh_file("rotating_41.msh", tmp_path / "inputs"), units="mm")
    data["max_global_cells"] = 10**6
    result, runner = project.run(data)

    scale = next(a for a in runner.argv if a[0] == "transformPoints")
    assert scale[-2:] == ("-scale", "(0.001 0.001 0.001)")
    assert runner.names().index("transformPoints") == 1  # right after the converter
    points = (project.root / "cases/imported/constant/polyMesh/points").read_text("utf-8")
    assert "(0.0030000000000000001 0.001 0.001)" in points
    assert codes(result)["MSH_UNITS_SUSPECT"] == "WARNING"  # 4 mm across


def test_cached_steps_are_reused(project: Project, gmsh: Data) -> None:
    project.run(gmsh)
    result, runner = project.run(gmsh)

    assert set(result.reused) == {IMPORT, ASSEMBLE, CHECK_MESH, CASE_SETUP}
    assert runner.calls == []


def test_a_patch_type_change_never_converts_again(project: Project, gmsh: Data) -> None:
    project.run(gmsh)
    imported = project.digest(IMPORTED_CASE)
    gmsh["patches"][2] = patch("y_min", "WALL", "MESH_PATCH", "y_min")
    result, runner = project.run(gmsh)

    assert result.succeeded and IMPORT in result.reused
    assert runner.names()[0] == "foamDictionary" and "gmshToFoam" not in runner.names()
    assert project.digest(IMPORTED_CASE) == imported


def test_a_physics_change_reruns_case_setup_only(project: Project, gmsh: Data) -> None:
    project.run(gmsh)
    merged = project.digest("merged")
    gmsh["physics"]["inlets"]["inlet"]["velocity"]["x"] = 7.0
    result, runner = project.run(gmsh)

    assert runner.calls == [] and CASE_SETUP in result.executed
    assert project.digest("merged") == merged


def test_case_setup_never_changes_the_imported_mesh(project: Project, gmsh: Data) -> None:
    """Invariant 14: the imported mesh is a verified artifact."""
    without = copy.deepcopy(gmsh)
    del without["physics"]
    project.run(without)
    imported, merged = project.digest(IMPORTED_CASE), project.digest("merged")
    result, _ = project.run(gmsh)

    assert result.succeeded and CASE_SETUP in result.executed
    assert project.digest(IMPORTED_CASE) == imported and project.digest("merged") == merged
    copied = project.root / CASE_DIR / "constant/polyMesh/boundary"
    assert copied.read_bytes() == (project.root / "cases/merged/constant/polyMesh/"
                                                  "boundary").read_bytes()


def test_a_renamed_patch_is_made_by_create_patch(project: Project, gmsh: Data) -> None:
    gmsh["patches"][0] = patch("intake", "INLET", "MESH_PATCH", "inlet")
    gmsh["physics"]["inlets"] = {"intake": gmsh["physics"]["inlets"]["inlet"]}
    result, runner = project.run(gmsh)

    assert result.succeeded, result.message
    patch_dict = (project.root / "cases/merged/system/createPatchDict").read_text("utf-8")
    assert "name intake;" in patch_dict and "patches (inlet);" in patch_dict
    retyped = [a[3] for a in runner.argv if a[0] == "foamDictionary"]
    assert "entry0/intake/type" not in retyped and "entry0/outlet/type" in retyped


# --- what stops a run -------------------------------------------------------------------

def test_a_changed_file_stops_before_any_command(project: Project, gmsh: Data) -> None:
    project.run(gmsh)
    path = Path(gmsh["imported_mesh"]["source_path"])
    path.write_bytes(path.read_bytes() + b"\n")
    result, runner = project.run(gmsh)

    assert not result.succeeded and runner.calls == []
    assert codes(result)["MSH_SOURCE_CHANGED"] == "BLOCKING"


def test_a_binary_file_without_gmsh_stops_before_any_command(
        project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "gmsh_available", lambda: False)
    data = configs.box(mesh_file("box_22_bin.msh", tmp_path))
    result, runner = project.run(data)

    assert runner.calls == [] and codes(result)["GMSH_BINARY_NEEDS_GMSH"] == "BLOCKING"


def test_a_cell_count_other_than_declared_stops_the_import(
        project: Project, fluent: Data) -> None:
    """E0: fluent3DMeshToFoam exits 0 on a damaged file with fewer cells."""
    declared = inspect_mesh_file(Path(fluent["imported_mesh"]["source_path"])).declared_cells
    result, runner = project.run(fluent, FakeMshRunner(cells=538))

    assert not result.succeeded and runner.names() == ["fluent3DMeshToFoam"]
    issue = next(i for i in result.issues if i.code == "MSH_CELL_COUNT_MISMATCH")
    assert issue.details == {"cells": 538, "declared": declared}
    result, runner = project.run(fluent)  # nothing of the bad conversion was kept
    assert result.succeeded and runner.names()[0] == "fluent3DMeshToFoam"


def test_faces_in_no_group_stop_the_import_with_their_count_and_box(
        project: Project, gmsh: Data) -> None:
    """Owner decision 3: defaultFaces is BLOCKING, with the face count and box."""
    result, _ = project.run(gmsh, FakeMshRunner(default_faces=872))
    issue = next(i for i in result.issues if i.code == "GMSH_UNGROUPED_FACES")

    assert issue.severity.value == "BLOCKING" and issue.details["faces"] == 872
    assert issue.details["box"] == [[-1.0, -1.0, -1.0], [-1.0, 1.0, 1.0]]  # the fake's face
    assert "872 boundary faces between (-1 -1 -1) and (-1 1 1) m" in issue.message
    assert "Physical Surface" in issue.suggested_action


def test_a_missing_cell_zone_after_conversion_is_blocking(
        project: Project, gmsh: Data) -> None:
    result, _ = project.run(gmsh, FakeMshRunner(zones={"stationary": 90}))
    assert codes(result)["MSH_NO_CELL_ZONE"] == "BLOCKING"


def test_failed_mesh_checks_fail_the_mesh_although_checkmesh_exits_0(
        project: Project, gmsh: Data) -> None:
    """Owner review: "Failed N mesh checks" is a failure whatever the exit code."""
    result, runner = project.run(gmsh, FakeMshRunner(failed_checks=2))

    assert not result.succeeded and "checkMesh" in runner.names()
    assert result.mesh is not None and result.mesh.status.value == "FAILED"
    assert codes(result)["CHECKMESH_FAILURE"] == "ERROR"
    assert result.case_setup is not None and result.case_setup.reason == "MESH_NOT_READY"
    assert project.json(MESH_REPORT)["assessment"]["mesh_validity"] == "INVALID"


def test_a_failing_converter_keeps_nothing(project: Project, gmsh: Data) -> None:
    result, _ = project.run(gmsh, FakeMshRunner(fail="gmshToFoam"))
    assert not result.succeeded and "gmshToFoam failed in imported" in result.message
    result, runner = project.run(gmsh)
    assert result.succeeded and runner.names()[0] == "gmshToFoam"


def test_baffles_are_reported(project: Project, gmsh: Data) -> None:
    result, _ = project.run(gmsh, FakeMshRunner(baffles=50))
    assert result.succeeded and codes(result)["MSH_BAFFLE_PAIR"] == "WARNING"


def test_low_ami_weights_are_a_warning(project: Project, gmsh: Data) -> None:
    result, _ = project.run(gmsh, FakeMshRunner(weights=(0.2, 1.0)))
    assert result.succeeded and codes(result)["AMI_WEIGHTS_OUT_OF_RANGE"] == "WARNING"


def test_cancelling_stops_the_run(project: Project, gmsh: Data) -> None:
    result, _ = project.run(gmsh, FakeMshRunner(outcome={"foamDictionary":
                                                         RunStatus.CANCELLED}))
    assert result.state is RunState.CANCELLED


# --- binary Gmsh and the scratch conversion ------------------------------------------------

def test_a_binary_gmsh_file_is_resaved_before_conversion(
        project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "gmsh_available", lambda: True)
    path = mesh_file("box_22_bin.msh", tmp_path)
    runner = FakeMshRunner()
    result = project.pipeline(runner).run_sync(
        project.root, MachineProjectConfig.model_validate(configs.box(path)))
    # The box has no rotating zone: validation stops; the scratch conversion below
    # is what a binary file goes through to show its box.
    assert codes(result)["MSH_NO_CELL_ZONE"] == "BLOCKING" and runner.calls == []
    info = inspect_mesh_file(path)
    import asyncio

    scratch = asyncio.run(convert_scratch(project.root, info, file_sha256(path),
                                          runner=runner, env=project.env, tool="t"))
    assert runner.names() == ["resave", "gmshToFoam"]
    assert runner.argv[0][1].endswith("resave.py") and runner.argv[1][1] == "resaved.msh"
    assert scratch.box == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) and scratch.issues == ()


def test_the_scratch_conversion_is_promoted_not_repeated(
        project: Project, gmsh: Data) -> None:
    """Owner answer 4: convert once at scale 1; after the units are confirmed,
    transformPoints acts on that same conversion."""
    import asyncio

    gmsh["imported_mesh"]["source_units"] = "cm"
    path = Path(gmsh["imported_mesh"]["source_path"])
    runner = FakeMshRunner()
    pipeline = project.pipeline(runner)
    config = MachineProjectConfig.model_validate(gmsh)
    tool = pipeline._tool(config, ("gmshToFoam",))
    asyncio.run(convert_scratch(project.root, inspect_mesh_file(path), file_sha256(path),
                                runner=runner, env=project.env, tool=tool))
    assert runner.names() == ["gmshToFoam"]
    result = pipeline.run_sync(project.root, config)

    assert result.succeeded, result.message
    assert runner.names().count("gmshToFoam") == 1  # no second conversion
    assert runner.names()[1] == "transformPoints"
    assert f"{IMPORT}:scratch" in result.reused
    assert not (project.root / "cases" / SCRATCH_CASE / "scratch.json").exists()
    log = project.root / "logs" / IMPORTED_CASE / "00_promote.log"
    assert "instead of converting again" in log.read_text("utf-8")


def test_a_scratch_conversion_of_another_file_is_not_used(
        project: Project, gmsh: Data, tmp_path: Path) -> None:
    import asyncio

    other = mesh_file("fluent_rotating.msh", tmp_path / "other")
    runner = FakeMshRunner()
    asyncio.run(convert_scratch(project.root, inspect_mesh_file(other), file_sha256(other),
                                runner=runner, env=project.env, tool="t"))
    result, runner = project.run(gmsh)
    assert result.succeeded and runner.names()[0] == "gmshToFoam"
    assert f"{IMPORT}:scratch" not in result.reused
