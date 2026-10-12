"""G6: case setup in the machine pipeline, with a fake OpenFOAM
(tests/fakes_machines.py). Spec section 11: a physics change re-runs case
setup only, and the mesh files stay byte-identical (invariant 14)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from case_setup.case import CASE_DIR
from machines.config import MachineProjectConfig
from machines.operations import (
    ASSEMBLE,
    CASE_SETUP,
    CHECK_MESH,
    GENERATE_CASES,
    MACHINE_EXECUTABLES,
    VALIDATE,
    VALIDATE_MESH,
    mesh_operation,
)
from machines.pipeline import CASE_SETUP_REPORT, MachinePipeline, MachineRunResult
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from vawt.status import RunState, read_status

DOMAIN, ZONE = mesh_operation("domain"), mesh_operation("zone_rotating")
MESHES = {DOMAIN, ZONE, ASSEMBLE, CHECK_MESH}


def physics() -> dict[str, Any]:
    return {
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "turbulence": {"length_scale": 0.1},
        "rotation": {"rotating": {"speed": {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 6.0,
                                            "radius": 0.5}, "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0.0, 0.0)}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
    }


class Project:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)

    def run(self, data: dict[str, Any]) -> tuple[MachineRunResult, FakeMachineRunner]:
        runner = FakeMachineRunner()
        pipeline = MachinePipeline(runner, environment=self.env, system_probe=lambda _: PLENTY)
        return pipeline.run_sync(self.root, MachineProjectConfig.model_validate(data)), runner

    def digest(self, directory: str) -> dict[str, str]:
        base = self.root / directory
        return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(base.rglob("*")) if p.is_file()}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return {**configs.hawt(tmp_path / "geometry"), "physics": physics()}


def codes(result: MachineRunResult) -> dict[str, str]:
    return {i.code: i.severity.value for i in result.issues}


def test_case_is_set_up_after_the_mesh(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt)

    assert result.succeeded and result.state is RunState.SUCCEEDED, codes(result)
    assert result.executed == (VALIDATE, GENERATE_CASES, DOMAIN, ZONE, ASSEMBLE, CHECK_MESH,
                               VALIDATE_MESH, CASE_SETUP)
    assert "case/" in result.message
    case = project.root / CASE_DIR
    assert sorted(p.name for p in (case / "0").iterdir()) == ["U", "k", "nut", "omega", "p"]
    assert (case / "constant/polyMesh/boundary").read_bytes() == (
        project.root / "cases/merged/constant/polyMesh/boundary").read_bytes()
    report = json.loads((project.root / CASE_SETUP_REPORT).read_text("utf-8"))
    assert report["rotation"]["omega_rad_s"] == pytest.approx(60.0)
    assert report["rotation"]["rpm"] == pytest.approx(60 * 60 / (2 * 3.141592653589793))
    assert "not evidence" in report["note"]
    status = read_status(project.root)
    assert status is not None and status["total_steps"] == 8 and status["stage"] == CASE_SETUP


def test_physics_change_reruns_case_setup_only(project: Project,
                                               hawt: dict[str, Any]) -> None:
    project.run(hawt)
    meshes = project.digest("cases")
    first = project.digest(CASE_DIR)

    changed = copy.deepcopy(hawt)
    changed["physics"]["fluid"] = {"density": 998.0, "kinematic_viscosity": 1e-6}
    changed["physics"]["rotation"]["rotating"]["direction"] = "NEGATIVE"
    result, runner = project.run(changed)

    assert result.succeeded, codes(result)
    assert runner.calls == []  # no OpenFOAM command
    assert result.executed == (VALIDATE, GENERATE_CASES, VALIDATE_MESH, CASE_SETUP)
    assert set(result.reused) == MESHES
    assert project.digest("cases") == meshes  # invariant 14: byte-identical meshes
    after = project.digest(CASE_DIR)
    assert after["constant/transportProperties"] != first["constant/transportProperties"]
    assert after["constant/dynamicMeshDict"] != first["constant/dynamicMeshDict"]
    assert after["constant/polyMesh/boundary"] == first["constant/polyMesh/boundary"]


def test_nothing_changed_reuses_case_setup(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    result, runner = project.run(hawt)
    assert runner.calls == [] and result.reused[-1] == CASE_SETUP
    assert CASE_SETUP not in result.executed

    (project.root / CASE_DIR / "0/U").unlink()  # an output is missing: not reusable
    result, _ = project.run(hawt)
    assert CASE_SETUP in result.executed and (project.root / CASE_DIR / "0/U").is_file()


def test_patch_type_change_reruns_assembly_checks_and_case_setup(
        project: Project, hawt: dict[str, Any]) -> None:
    """P2 (H3 kept): the meshes are reused; the assembly sets the new type."""
    project.run(hawt)
    walls = copy.deepcopy(hawt)
    walls["patches"][3]["type"] = "WALL"  # side: SLIP -> WALL

    result, runner = project.run(walls)

    assert result.succeeded, codes(result)
    assert set(result.reused) == {DOMAIN, ZONE}
    assert {ASSEMBLE, CHECK_MESH, CASE_SETUP} <= set(result.executed)
    assert {case for _, case in runner.calls} == {"merged"}
    velocity = (project.root / CASE_DIR / "0/U").read_text("utf-8")
    assert "side\n    {\n        type            noSlip;" in velocity


def test_physics_findings_never_stop_meshing(project: Project, hawt: dict[str, Any]) -> None:
    incomplete = copy.deepcopy(hawt)
    del incomplete["physics"]["rotation"]["rotating"]["direction"]

    result, runner = project.run(incomplete)

    assert not result.succeeded and result.state is RunState.FAILED
    assert codes(result)["ROTATION_DIRECTION_NOT_SET"] == "BLOCKING"
    assert {case for _, case in runner.calls} == {"domain", "zone_rotating", "merged"}
    assert "Case setup was not done" in result.message
    assert not (project.root / CASE_DIR).exists()

    fixed, runner = project.run(hawt)  # the meshes are reused
    assert fixed.succeeded and runner.calls == []
    assert fixed.executed[-1] == CASE_SETUP


def test_without_physics_nothing_changes(project: Project, hawt: dict[str, Any]) -> None:
    del hawt["physics"]
    result, _ = project.run(hawt)
    assert result.succeeded
    assert CASE_SETUP not in result.executed and not (project.root / CASE_DIR).exists()
    status = read_status(project.root)
    assert status is not None and status["total_steps"] == 7


def test_solver_output_survives_a_new_setup(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    output = project.root / CASE_DIR / "0.001"
    output.mkdir()
    (output / "U").write_text("solver output", "utf-8")
    hawt["physics"]["turbulence"] = {"model": "LAMINAR"}

    result, _ = project.run(hawt)

    assert result.succeeded and result.executed[-1] == CASE_SETUP
    assert sorted(p.name for p in (project.root / CASE_DIR / "0").iterdir()) == ["U", "p"]
    assert (output / "U").read_text("utf-8") == "solver output"
