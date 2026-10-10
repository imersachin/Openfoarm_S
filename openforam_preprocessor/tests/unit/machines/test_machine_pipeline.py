"""G4: the machine pipeline with a fake OpenFOAM (tests/fakes_machines.py)."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from core.workflow.run_lock import LOCK_PATH, RunLock
from machines.config import MachineProjectConfig
from machines.operations import (
    ASSEMBLE,
    CHECK_MESH,
    GENERATE_CASES,
    MACHINE_EXECUTABLES,
    VALIDATE,
    VALIDATE_MESH,
    mesh_operation,
)
from machines.pipeline import MESH_REPORT, MachinePipeline, MachineRunResult
from machines.project_store import read_last_meshed
from machines.vawt_migration import from_vawt
from mesh.estimator import SystemResources
from openfoam.runner import RunStatus
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import VawtProjectConfig
from vawt.status import RunState, read_status

DOMAIN, ZONE = mesh_operation("domain"), mesh_operation("zone_rotating")
MESHES = {DOMAIN, ZONE, ASSEMBLE, CHECK_MESH}
HAWT_ORDER = [
    ("blockMesh", "domain"), ("surfaceFeatureExtract", "domain"), ("snappyHexMesh", "domain"),
    ("blockMesh", "zone_rotating"), ("surfaceFeatureExtract", "zone_rotating"),
    ("snappyHexMesh", "zone_rotating"), ("topoSet", "zone_rotating"),
    ("mergeMeshes", "merged"), ("createPatch", "merged"), ("foamDictionary", "merged"),
    ("checkMesh", "merged"), ("postProcess", "merged")]
ASSEMBLY_ONLY = [c for c in HAWT_ORDER if c[1] == "merged"]


class Project:
    def __init__(self, tmp_path: Path, resources: SystemResources = PLENTY) -> None:
        self.tmp, self.root, self.resources = tmp_path, tmp_path / "project", resources
        self.env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)

    def run(self, data: dict[str, Any], runner: FakeMachineRunner | None = None,
            **kwargs: Any) -> tuple[MachineRunResult, FakeMachineRunner]:
        runner = runner or FakeMachineRunner()
        pipeline = MachinePipeline(runner, environment=self.env,
                                   system_probe=lambda _: self.resources)
        config = MachineProjectConfig.model_validate(data)
        return pipeline.run_sync(self.root, config, **kwargs), runner

    def report(self) -> dict[str, Any]:
        return json.loads((self.root / MESH_REPORT).read_text("utf-8"))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return configs.hawt(tmp_path / "geometry")


def codes(result: MachineRunResult) -> dict[str, str]:
    return {i.code: i.severity.value for i in result.issues}


# --- order, layout, report ---------------------------------------------------------------

def test_order_logs_and_report(project: Project, hawt: dict[str, Any]) -> None:
    result, runner = project.run(hawt)

    assert result.succeeded and result.state is RunState.SUCCEEDED, codes(result)
    assert runner.calls == HAWT_ORDER
    for (command, case), cwd in zip(runner.calls, runner.cwds, strict=True):
        assert cwd == project.root / "cases" / case, command
    assert result.executed == (VALIDATE, GENERATE_CASES, DOMAIN, ZONE, ASSEMBLE, CHECK_MESH,
                               VALIDATE_MESH)
    report = project.report()
    assert report["assessment"]["mesh_validity"] == "VALID"
    assert report["regions"] == {"expected": 2, "found": 2}
    assert report["ami_weights"][0]["source"] == "rotating_outer_stat"
    assert (project.root / "logs/domain/03_snappyHexMesh.log").is_file()
    assert (project.root / "logs/merged/checkMesh.log").is_file()
    assert (project.root / "logs/merged/postProcess.log").is_file()
    last = read_last_meshed(project.root)
    assert last is not None and last.run_id == result.run_id and last.mesh_status == "passed"


def test_patch_types_are_set_on_the_merged_mesh(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    boundary = (project.root / "cases/merged/constant/polyMesh/boundary").read_text()
    zone = (project.root / "cases/zone_rotating/constant/polyMesh/boundary").read_text()

    assert "rotor\n    {\n        type wall;" in boundary
    assert "rotor\n    {\n        type patch;" in zone  # meshed untyped (H3)


# --- reuse after changes (spec section 11) ---------------------------------------------------

def test_nothing_changed_starts_no_command(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)

    result, runner = project.run(hawt)

    assert result.succeeded and runner.calls == []
    assert set(result.reused) == MESHES


def test_patch_type_only_reruns_the_assembly(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    changed = copy.deepcopy(hawt)
    changed["patches"][3]["type"] = "WALL"  # side: SLIP -> WALL

    result, runner = project.run(changed)

    assert result.succeeded
    assert set(result.reused) == {DOMAIN, ZONE}
    assert runner.calls == [*ASSEMBLY_ONLY[:3], ("foamDictionary", "merged"), *ASSEMBLY_ONLY[3:]]


def test_body_refinement_reruns_its_zone(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    changed = copy.deepcopy(hawt)
    changed["bodies"][0]["refinement"] = {"min_level": 1, "max_level": 3}

    result, runner = project.run(changed)

    assert set(result.reused) == {DOMAIN}
    assert {case for _, case in runner.calls} == {"zone_rotating", "merged"}


def test_domain_size_reruns_the_domain(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    changed = copy.deepcopy(hawt)
    changed["domain"]["axis_max"] = 6.0

    result, runner = project.run(changed)

    assert set(result.reused) == {ZONE}
    assert {case for _, case in runner.calls} == {"domain", "merged"}


def test_patch_name_reruns_the_domain(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    changed = copy.deepcopy(hawt)
    changed["patches"][1]["name"] = "upstream"

    result, _ = project.run(changed)

    assert set(result.reused) == {ZONE}


def test_body_geometry_reruns_the_meshes_holding_it(project: Project,
                                                   hawt: dict[str, Any]) -> None:
    project.run(hawt)
    changed = copy.deepcopy(hawt)
    changed["bodies"][0]["source"]["translation"] = {"x": 0.001, "y": 0.0, "z": 0.0}

    result, runner = project.run(changed)

    assert set(result.reused) == {DOMAIN}
    assert ("snappyHexMesh", "zone_rotating") in runner.calls


def test_missing_output_reruns_that_mesh(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)
    (project.root / "cases/zone_rotating/constant/polyMesh/points").unlink()

    result, runner = project.run(hawt)

    assert result.succeeded and DOMAIN in result.reused
    assert ("snappyHexMesh", "zone_rotating") in runner.calls


def test_force_reruns_everything(project: Project, hawt: dict[str, Any]) -> None:
    project.run(hawt)

    _, runner = project.run(hawt, force=True)

    assert runner.calls == HAWT_ORDER


# --- stops ---------------------------------------------------------------------------------

def test_validation_stop_runs_nothing(project: Project, hawt: dict[str, Any]) -> None:
    hawt["patches"] = [p for p in hawt["patches"] if p["name"] != "outlet"]

    result, runner = project.run(hawt)

    assert not result.succeeded and runner.calls == []
    assert codes(result)["NO_OUTLET"] == "BLOCKING"
    assert result.executed == (VALIDATE,)


def test_vawt_only_settings_need_the_vawt_workflow(project: Project) -> None:
    rotor_only = from_vawt(VawtProjectConfig.model_validate(
        preset_draft(project.tmp, include_domain=False)))

    result, runner = project.run(rotor_only.model_dump(mode="json"))

    assert codes(result)["USE_VAWT_WORKFLOW"] == "BLOCKING" and runner.calls == []


def test_imported_zone_without_imported_domain_is_refused(project: Project,
                                                          hawt: dict[str, Any]) -> None:
    # G5 changed this test: it asserted that every imported zone was refused
    # (CASE_GENERATION_UNSUPPORTED); now only one without imported parts is.
    francis = configs.francis(project.tmp / "f")
    hawt["rotating_zones"][0]["shape"] = francis["rotating_zones"][0]["shape"]

    result, runner = project.run(hawt)

    assert codes(result)["IMPORTED_ZONE_NEEDS_IMPORTED_DOMAIN"] == "BLOCKING"
    assert runner.calls == []


def test_command_failure_stops_and_keeps_no_record(project: Project,
                                                    hawt: dict[str, Any]) -> None:
    result, runner = project.run(hawt, FakeMachineRunner(fail=("snappyHexMesh",
                                                               "zone_rotating")))

    assert not result.succeeded and codes(result)["COMMAND_FAILED"] == "ERROR"
    assert runner.calls[-1] == ("snappyHexMesh", "zone_rotating")
    again, runner = project.run(hawt)
    assert again.succeeded and DOMAIN in again.reused and ZONE in again.executed


def test_wrong_region_is_not_kept(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt, FakeMachineRunner(wrong_region=("zone_rotating",)))

    assert not result.succeeded
    assert codes(result)["WRONG_REGION_KEPT"] == "ERROR"
    assert codes(result)["DOMAIN_PATCH_MISSING"] == "ERROR"
    again, runner = project.run(hawt)
    assert ("snappyHexMesh", "zone_rotating") in runner.calls  # never reused


def test_missing_cell_zone_stops(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt, FakeMachineRunner(no_zone=("zone_rotating",)))

    assert not result.succeeded and codes(result)["CELL_ZONE_MISSING"] == "ERROR"


def test_cancellation(project: Project, hawt: dict[str, Any]) -> None:
    event = asyncio.Event()
    result, runner = project.run(hawt, FakeMachineRunner(cancel_after=("topoSet", event)),
                                 cancel_event=event)

    assert result.state is RunState.CANCELLED
    assert runner.calls[-1] == ("topoSet", "zone_rotating")
    assert codes(result)["RUN_CANCELLED"] == "ERROR"


def test_timeout(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt, FakeMachineRunner(outcome={"mergeMeshes": RunStatus.TIMEOUT}))

    assert not result.succeeded and result.state is RunState.FAILED


@pytest.mark.parametrize("fraction, allow, ran, status", [
    (0.9, False, False, "HIGH RESOURCE RISK"), (0.9, True, True, "HIGH RESOURCE RISK"),
    (1.2, True, False, "BLOCKED")])
def test_resource_preflight(tmp_path: Path, hawt: dict[str, Any], fraction: float,
                            allow: bool, ran: bool, status: str) -> None:
    # RAM follows the largest case (the meshes are built one after another).
    probe = Project(tmp_path / "probe")
    probe.run(hawt)
    estimate = json.loads((probe.root / "reports/resource_preflight.json").read_text())
    peak = max(estimate["estimated_cells_by_case"].values())
    ram = int(peak * 2000.0 / fraction)  # the estimator's 2 kB per cell
    project = Project(tmp_path, SystemResources(available_ram_bytes=ram,
                                                available_disk_bytes=10**12, cpu_count=8))

    result, runner = project.run(hawt, allow_high_resource_risk=allow)

    assert bool(runner.calls) is ran
    report = json.loads((project.root / "reports/resource_preflight.json").read_text())
    assert report["status"] == status

def test_missing_executables_stop(project: Project, hawt: dict[str, Any]) -> None:
    project.env = openfoam_env(project.tmp / "few", "v2512", ("blockMesh",))

    result, runner = project.run(hawt)

    assert not result.succeeded and runner.calls == []


# --- results ----------------------------------------------------------------------------------

def test_low_ami_weights_warn(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt, FakeMachineRunner(weights=(0.5, 1.0)))

    assert result.succeeded
    assert codes(result)["AMI_WEIGHTS_OUT_OF_RANGE"] == "WARNING"


def test_wrong_region_count_is_invalid(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt, FakeMachineRunner(regions=1))

    assert not result.succeeded
    assert codes(result)["REGION_COUNT_UNEXPECTED"] == "ERROR"
    assert project.report()["assessment"]["mesh_validity"] == "INVALID"


def test_status_record_and_lock(project: Project, hawt: dict[str, Any]) -> None:
    result, _ = project.run(hawt)

    status = read_status(project.root)
    assert status is not None and status["state"] == "SUCCEEDED"
    assert status["stage"] == VALIDATE_MESH and status["total_steps"] == 7
    assert result.run_record_path is not None
    record = json.loads(result.run_record_path.read_text("utf-8"))
    assert record["kind"] == "machine_mesh" and record["machine"] == "HAWT"
    assert not (project.root / LOCK_PATH).exists()


def test_busy_project_is_refused(project: Project, hawt: dict[str, Any]) -> None:
    lock = RunLock(project.root)
    assert lock.acquire() is None
    try:
        result, runner = project.run(hawt)
    finally:
        lock.release()

    assert not result.succeeded and runner.calls == []
    assert "in progress" in result.message
