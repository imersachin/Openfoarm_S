"""G6 follow-up (review findings): user files survive a new setup, a case/
that no longer matches the project is marked stale, zero inflow is BLOCKING,
the mesh and case setup are reported separately, and the paths the G6 tests
did not reach (case setup failure, force, every Francis field against G0,
a write interval in revolutions)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest

import case_setup.validation
import machines.pipeline
from case_setup.case import CASE_DIR, GENERATED, STALE_MARKER, render, write_case
from case_setup.dictionaries import dictionaries
from case_setup.fields import fields
from case_setup.physics import rad_s_to_rpm
from case_setup.setup import CaseSetupError, resolve
from case_setup.validation import check_physics
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.operations import ASSEMBLE, CASE_SETUP, CHECK_MESH
from machines.pipeline import CASE_SETUP_REPORT, StageResult, StageStatus
from tests.fakes import PLENTY
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from tests.unit.case_setup.test_g6_case_setup import (
    G0,
    HAWT_MESH,
    entries,
    hawt_physics,
    keyword,
    normalised,
)
from tests.unit.case_setup.test_g6_case_setup_pipeline import DOMAIN, ZONE, Project, codes
from vawt.status import RunState

SUCCESS = StageResult(StageStatus.SUCCESS, message="The case is set up in case/.")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return {**configs.hawt(tmp_path / "geometry"), "physics": hawt_physics()}


def model(data: dict[str, Any]) -> MachineProjectConfig:
    return MachineProjectConfig.model_validate(data)


def report(project: Project) -> dict[str, Any]:
    return dict(json.loads((project.root / CASE_SETUP_REPORT).read_text("utf-8")))


def fake_mesh(tmp_path: Path) -> Path:
    mesh = tmp_path / "mesh/constant/polyMesh"
    mesh.mkdir(parents=True)
    rows = "".join(f"    {n}\n    {{\n        type {t};\n        nFaces 1;\n        startFace 0;\n"
                   "    }\n" for n, t in HAWT_MESH)
    (mesh / "boundary").write_text(f"{len(HAWT_MESH)}\n(\n{rows})\n", "utf-8")
    (mesh / "points").write_text("points", "utf-8")
    return mesh


# --- 1. only generated files are removed --------------------------------------------------------

def test_user_files_survive_a_new_setup(tmp_path: Path, hawt: dict[str, Any]) -> None:
    mesh, root = fake_mesh(tmp_path), tmp_path / "project"
    _, files = render(model(hawt), [], mesh)
    case = write_case(root, files, mesh)
    assert json.loads((case / GENERATED).read_text("utf-8"))["files"] == sorted(files)
    user = {"system/decomposeParDict": "numberOfSubdomains 4;",
            "constant/fvOptions": "options {}", "0/U.orig": "user copy"}
    for relative, text in user.items():
        (case / relative).write_text(text, "utf-8")

    hawt["physics"]["turbulence"] = {"model": "LAMINAR"}
    _, laminar = render(model(hawt), [], mesh)
    write_case(root, laminar, mesh)

    for relative, text in user.items():
        assert (case / relative).read_text("utf-8") == text, relative
    assert sorted(p.name for p in (case / "0").iterdir()) == ["U", "U.orig", "p"]
    assert json.loads((case / GENERATED).read_text("utf-8"))["files"] == sorted(laminar)


def test_without_a_generated_list_nothing_is_removed(tmp_path: Path,
                                                     hawt: dict[str, Any]) -> None:
    mesh, root = fake_mesh(tmp_path), tmp_path / "project"
    _, files = render(model(hawt), [], mesh)
    case = write_case(root, files, mesh)
    (case / GENERATED).write_text("not json", "utf-8")  # unreadable: never guess
    hawt["physics"]["turbulence"] = {"model": "LAMINAR"}
    _, laminar = render(model(hawt), [], mesh)
    write_case(root, laminar, mesh)
    assert (case / "0/k").is_file()
    (case / GENERATED).write_text(json.dumps({"files": ["../outside", "constant/polyMesh/points",
                                                        "0/k"]}), "utf-8")
    (root / "outside").write_text("kept", "utf-8")
    write_case(root, laminar, mesh)
    assert not (case / "0/k").exists()  # listed: removed
    assert (root / "outside").read_text("utf-8") == "kept"  # outside case/: never
    assert (case / "constant/polyMesh/points").is_file()


def test_user_files_survive_in_the_pipeline(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    case = project.root / CASE_DIR
    (case / "system/decomposeParDict").write_text("numberOfSubdomains 4;", "utf-8")
    (case / "constant/fvOptions").write_text("options {}", "utf-8")
    hawt["physics"]["turbulence"] = {"model": "LAMINAR"}

    result, _ = project.run(hawt)

    assert result.succeeded and result.executed[-1] == CASE_SETUP
    assert (case / "system/decomposeParDict").read_text("utf-8") == "numberOfSubdomains 4;"
    assert (case / "constant/fvOptions").read_text("utf-8") == "options {}"
    assert not (case / "0/k").exists()


# --- 2. and 4. stale case, separate outcomes ----------------------------------------------------

def test_success_reports_both_outcomes(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt)

    assert result.mesh is not None and result.mesh.status is StageStatus.SUCCESS
    assert result.case_setup == SUCCESS
    assert report(project)["status"] == "SUCCESS" and report(project)["stale"] is False
    assert result.run_record_path is not None
    record = json.loads(result.run_record_path.read_text("utf-8"))
    assert record["mesh"]["status"] == "SUCCESS"
    assert record["case_setup"] == SUCCESS.as_dict()


def test_invalid_physics_marks_the_case_stale(project: Project, hawt: dict[str, Any]) -> None:
    first, _ = project.run(hawt)
    case = project.root / CASE_DIR
    broken = copy.deepcopy(hawt)
    del broken["physics"]["rotation"]["rotating"]["direction"]

    result, _ = project.run(broken)

    assert result.state is RunState.FAILED
    assert result.mesh is not None and result.mesh.status is StageStatus.SUCCESS
    assert result.case_setup is not None
    assert result.case_setup.status is StageStatus.SKIPPED
    assert result.case_setup.reason == "PHYSICS_INVALID"
    assert "ROTATION_DIRECTION_NOT_SET" in result.case_setup.message
    assert (case / STALE_MARKER).is_file() and (case / "0/U").is_file()  # kept
    assert "marked stale" in result.message
    skipped = report(project)
    assert skipped["status"] == "SKIPPED" and skipped["reason"] == "PHYSICS_INVALID"
    assert skipped["stale"] is True and "does not match" in skipped["stale_note"]
    assert skipped["previous"]["run_id"] == first.run_id  # the last setup's values

    again, _ = project.run(broken)  # still skipped: the previous report is kept
    assert report(project)["previous"]["run_id"] == first.run_id
    assert again.case_setup is not None and again.case_setup.reason == "PHYSICS_INVALID"

    fixed, _ = project.run(hawt)  # same inputs as the first setup: reused, not stale
    assert fixed.succeeded and fixed.case_setup == SUCCESS
    assert fixed.reused[-1] == CASE_SETUP
    assert not (case / STALE_MARKER).exists()
    assert report(project)["status"] == "SUCCESS" and report(project)["stale"] is False


def test_removed_physics_marks_the_case_stale(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    without = {k: v for k, v in hawt.items() if k != "physics"}

    result, _ = project.run(without)

    assert result.succeeded  # meshing only, as before G6
    assert result.case_setup is not None and result.case_setup.reason == "NO_PHYSICS"
    assert result.case_setup.status is StageStatus.SKIPPED
    assert (project.root / CASE_DIR / STALE_MARKER).is_file()
    assert report(project)["reason"] == "NO_PHYSICS" and report(project)["stale"] is True
    assert "marked stale" in result.message


def test_invalid_mesh_skips_case_setup_and_marks_the_case_stale(
        project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)

    result, _ = _run_forced(project, hawt, regions=1)  # checkMesh finds one region

    assert not result.succeeded
    assert result.mesh is not None and result.mesh.status is StageStatus.FAILED
    assert result.mesh.reason == "MESH_VALIDATION_FAILED"
    assert result.case_setup is not None and result.case_setup.reason == "MESH_NOT_READY"
    assert (project.root / CASE_DIR / STALE_MARKER).is_file()
    assert report(project)["reason"] == "MESH_NOT_READY"


def test_a_run_that_stops_reports_both_outcomes(project: Project,
                                                hawt: dict[str, Any]) -> None:
    gate = copy.deepcopy(hawt)
    gate["patches"] = [p for p in gate["patches"] if p["name"] != "outlet"]
    result, _ = project.run(gate)
    assert result.mesh == StageResult(StageStatus.SKIPPED, "VALIDATION_FAILED",
                                      "Validation found problems; the mesh was not built.")
    assert result.case_setup is not None and result.case_setup.reason == "MESH_NOT_READY"
    assert not (project.root / CASE_DIR).exists()
    assert report(project)["stale"] is False  # physics present: the report says why

    failed, _ = _run_forced(project, hawt, fail="snappyHexMesh")
    assert failed.mesh is not None and failed.mesh.status is StageStatus.FAILED
    assert failed.mesh.reason == "COMMAND_FAILED"
    assert failed.case_setup is not None and failed.case_setup.reason == "MESH_NOT_READY"


def test_no_physics_and_no_case_writes_no_report(project: Project,
                                                 hawt: dict[str, Any]) -> None:
    del hawt["physics"]
    result, _ = project.run(hawt)
    assert result.succeeded and result.mesh is not None
    assert result.mesh.status is StageStatus.SUCCESS
    assert result.case_setup is not None and result.case_setup.reason == "NO_PHYSICS"
    assert not (project.root / CASE_SETUP_REPORT).exists()
    assert not (project.root / CASE_DIR).exists()


def _run_forced(project: Project, data: dict[str, Any], **runner: Any) -> Any:
    fake = FakeMachineRunner(**runner)
    pipeline = machines.pipeline.MachinePipeline(fake, environment=project.env,
                                                 system_probe=lambda _: PLENTY)
    return pipeline.run_sync(project.root, model(data), force=True), fake


# --- 3. zero inflow ----------------------------------------------------------------------------

@pytest.mark.parametrize("inlet", [
    {"kind": "VELOCITY", "velocity": configs.vec(0.0, 0.0, 0.0)},
    {"kind": "FLOW_RATE", "volumetric_flow_rate": 0.0},
])
def test_zero_inflow_is_blocking(hawt: dict[str, Any], inlet: dict[str, Any]) -> None:
    hawt["physics"]["inlets"]["inlet"] = inlet
    hawt["physics"]["rotation"]["rotating"]["speed"] = {"kind": "RPM", "rpm": 573.0}
    found = {i.code: i for i in check_physics(model(hawt), [])}
    assert found["INLET_VALUE_ZERO"].severity.value == "BLOCKING"
    assert found["INLET_VALUE_ZERO"].details["field"] == "physics.inlets.inlet"
    with pytest.raises(CaseSetupError, match="no inflow"):
        resolve(model(hawt), [], HAWT_MESH)


def test_a_small_inflow_is_not_zero(hawt: dict[str, Any]) -> None:
    hawt["physics"]["inlets"]["inlet"] = {"kind": "VELOCITY",
                                          "velocity": configs.vec(0.0, 0.0, 1e-3)}
    assert "INLET_VALUE_ZERO" not in {i.code for i in check_physics(model(hawt), [])}


# --- 5. coverage --------------------------------------------------------------------------------

def test_case_setup_failure(project: Project, hawt: dict[str, Any],
                            monkeypatch: pytest.MonkeyPatch) -> None:
    """A mesh patch without a configured type (render raises): FAILED, the mesh
    is kept for reuse, and an existing case/ is marked stale."""
    project.run(hawt)

    def untyped(*_: Any) -> Any:
        raise CaseSetupError("The mesh has patches without a configured type: stray.")

    monkeypatch.setattr(machines.pipeline, "render", untyped)
    hawt["physics"]["fluid"] = {"density": 998.0, "kinematic_viscosity": 1e-6}
    result, runner = project.run(hawt)

    assert not result.succeeded and runner.calls == []
    assert codes(result)["CASE_SETUP_FAILED"] == "BLOCKING"
    assert "mesh is valid" in result.message
    assert result.mesh is not None and result.mesh.status is StageStatus.SUCCESS
    assert result.case_setup == StageResult(
        StageStatus.FAILED, "CASE_SETUP_FAILED",
        "The mesh has patches without a configured type: stray.")
    assert (project.root / CASE_DIR / STALE_MARKER).is_file()
    assert report(project)["status"] == "FAILED"


def test_unresolved_physics_is_blocking(hawt: dict[str, Any],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    def unresolved(*_: Any) -> Any:
        raise CaseSetupError("The mesh needs at least one inlet and one outlet.")

    monkeypatch.setattr(case_setup.validation, "resolve", unresolved)
    found = {i.code: i for i in check_physics(model(hawt), [])}
    assert found["CASE_SETUP_UNRESOLVED"].severity.value == "BLOCKING"
    assert "at least one inlet" in found["CASE_SETUP_UNRESOLVED"].message


def test_force_reruns_everything(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    case = project.root / CASE_DIR
    (case / "0/U").write_text("edited", "utf-8")  # would be noticed anyway; the cache is not
    (case / "system/decomposeParDict").write_text("user", "utf-8")

    result, runner = _run_forced(project, hawt)

    assert result.succeeded and result.reused == ()
    assert {DOMAIN, ZONE, ASSEMBLE, CHECK_MESH, CASE_SETUP} <= set(result.executed)
    assert runner.calls
    assert (case / "0/U").read_text("utf-8") != "edited"
    assert (case / "system/decomposeParDict").read_text("utf-8") == "user"


def test_francis_fields_match_the_g0_run(tmp_path: Path) -> None:
    """G0 R4's inputs: water, U (0 0 1) at the inlet, I 5 %, L 0.03 m, 20 rad/s
    about +z, outlet 0 Pa. Every field equals G0 R4's 0.sst files."""
    data = configs.francis(tmp_path / "geometry")
    data["physics"] = {
        "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
        "turbulence": {"model": "K_OMEGA_SST", "intensity_percent": 5.0, "length_scale": 0.03},
        "rotation": {"runner": {"speed": {"kind": "RPM", "rpm": rad_s_to_rpm(20.0)},
                                "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(0.0, 0.0, 1.0)}},
        "outlets": {"outlet": {"pressure_pa": 0.0}}}
    config = model(data)
    mesh = [(p.name, "wall" if p.type in ("WALL", "ROTATING_WALL") else "patch")
            for p in config.patches]
    setup = resolve(config, read_imported(config), mesh)
    generated = fields(setup)
    solve = G0 / "R4_francis/solve_ami"
    for name in ("U", "p", "k", "omega", "nut"):
        g0, text = (solve / "0.sst" / name).read_text("utf-8"), generated[f"0/{name}"]
        g0_internal, internal = keyword(g0, "internalField"), keyword(text, "internalField")
        assert normalised(internal, "") == normalised(g0_internal, ""), name
        expected = {p: {k: normalised(v, g0_internal) for k, v in e.items()}
                    for p, e in entries(g0).items()}
        mine = {p: {k: normalised(v, internal) for k, v in e.items()}
                for p, e in entries(text).items()}
        assert mine == expected, name
        assert keyword(text, "dimensions") == keyword(g0, "dimensions"), name
    files = dictionaries(setup)
    for name in ("dynamicMeshDict", "transportProperties"):
        g0, mine_text = (solve / "constant" / name).read_text("utf-8"), files[f"constant/{name}"]
        for key in ("transportModel", "nu") if name == "transportProperties" else (
                "dynamicFvMesh", "motionSolver", "solidBodyMotionFunction", "omega"):
            assert normalised(keyword(mine_text, key), "") == normalised(
                keyword(g0, key), ""), key
    # The zone is named after the project's zone (G0's hand-made case used "rotating").
    assert keyword(files["constant/dynamicMeshDict"], "cellZone") == "runner"


def test_write_interval_in_revolutions(hawt: dict[str, Any]) -> None:
    hawt["physics"]["time"] = {"end_revolutions": 3, "write_interval": 0.5,
                               "write_unit": "REVOLUTIONS"}
    setup = resolve(model(hawt), [], HAWT_MESH)
    period = 2 * math.pi / 60
    assert setup.time.write_interval == pytest.approx(0.5 * period)
    control = dictionaries(setup)["system/controlDict"]
    assert float(keyword(control, "writeInterval")) == pytest.approx(0.5 * period)
    assert float(keyword(control, "endTime")) == pytest.approx(3 * period)
    assert keyword(control, "writeControl") == "adjustable"
