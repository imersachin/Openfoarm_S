"""V3: the VAWT pipeline with a fake OpenFOAM (tests/fakes_vawt.py)."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from core.artifacts import MANIFEST_PATH
from core.issues import IssueSeverity
from core.workflow.run_lock import LOCK_PATH
from mesh.estimator import SystemResources
from openfoam.runner import RunStatus
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_vawt import VAWT_TOOLS, FakeVawtRunner
from tests.fixtures.vawt.drafts import preset_draft
from vawt import pipeline as pipeline_module
from vawt.config import VawtProjectConfig
from vawt.operations import VawtOperation
from vawt.pipeline import VawtPipeline, VawtRunResult
from vawt.status import RunState, read_status

Op = VawtOperation
AMI_ORDER = [("blockMesh", "outer"), ("snappyHexMesh", "outer"),
             ("blockMesh", "rotor"), ("snappyHexMesh", "rotor"), ("topoSet", "rotor"),
             ("mergeMeshes", "merged"), ("createPatch", "merged"), ("checkMesh", "merged")]


def draft_for(tmp_path: Path, mode: str = "AMI", **edits: Any) -> dict[str, Any]:
    draft = preset_draft(tmp_path, include_domain=mode != "ROTOR_ONLY")
    if mode == "CELL_ZONE":
        draft["rotating_zone"]["interface"] = "CELL_ZONE"
    for dotted, value in edits.items():
        *parents, leaf = dotted.split("__")
        target = draft
        for key in parents:
            target = target.setdefault(key, {})
        target[leaf] = value
    return draft


class Project:
    def __init__(self, tmp_path: Path, version: str = "v2512",
                 resources: SystemResources = PLENTY) -> None:
        self.tmp, self.root = tmp_path, tmp_path / "project"
        self.version, self.resources = version, resources

    def run(self, raw: dict[str, Any], runner: FakeVawtRunner | None = None,
            **kwargs: Any) -> tuple[VawtRunResult, FakeVawtRunner]:
        runner = runner or FakeVawtRunner(project_root=self.root)
        pipeline = VawtPipeline(runner, environment=openfoam_env(self.tmp, self.version,
                                                                 VAWT_TOOLS),
                                system_probe=lambda _: self.resources)
        return pipeline.run_sync(self.root, VawtProjectConfig.model_validate(raw), **kwargs), runner

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.root / MANIFEST_PATH).read_text("utf-8"))["records"]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


def codes(result: VawtRunResult) -> dict[str, IssueSeverity]:
    return {i.code: i.severity for i in result.issues}


def report(project: Project) -> dict[str, Any]:
    return json.loads((project.root / "reports/mesh_quality_report.json").read_text("utf-8"))


# --- order and layout ------------------------------------------------------------------

def test_ami_order_and_report(project: Project) -> None:
    result, runner = project.run(draft_for(project.tmp))

    assert result.succeeded and result.state is RunState.SUCCEEDED
    assert runner.calls == AMI_ORDER
    assert result.final_case == "merged"
    assert report(project)["assessment"]["mesh_validity"] == "VALID"
    assert report(project)["regions"] == {"expected": 2, "found": 2}


@pytest.mark.parametrize("mode,expected", [
    ("CELL_ZONE", [("surfaceFeatureExtract", "merged"), ("blockMesh", "merged"),
                   ("snappyHexMesh", "merged"), ("checkMesh", "merged")]),
    ("ROTOR_ONLY", [("surfaceFeatureExtract", "rotor"), ("blockMesh", "rotor"),
                    ("snappyHexMesh", "rotor"), ("topoSet", "rotor"), ("checkMesh", "rotor")]),
])
def test_cell_zone_orders(project: Project, mode: str, expected: list[tuple[str, str]]) -> None:
    result, runner = project.run(draft_for(project.tmp, mode,
                                           refinement__extract_features=True))

    assert result.succeeded
    assert runner.calls == expected
    assert report(project)["regions"] == {"expected": 1, "found": 1}


def test_features_run_before_the_rotor_mesh_in_the_rotor_case(project: Project) -> None:
    _, runner = project.run(draft_for(project.tmp, refinement__extract_features=True))

    assert runner.calls.index(("surfaceFeatureExtract", "rotor")) < runner.calls.index(
        ("snappyHexMesh", "rotor"))


def test_commands_run_from_their_case_and_log_per_sub_case(project: Project) -> None:
    _, runner = project.run(draft_for(project.tmp))

    for (command, case), cwd in zip(runner.calls, runner.cwds, strict=True):
        assert cwd == project.root / "cases" / case, command
    assert (project.root / "logs/outer/03_snappyHexMesh.log").is_file()
    assert (project.root / "logs/merged/05_mergeMeshes.log").is_file()
    assert (project.root / "logs/merged/04_checkMesh.log").is_file()
    assert not (project.root / "cases/outer/logs").exists()


# --- cache -----------------------------------------------------------------------

def test_nothing_changed_starts_no_command(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)

    result, runner = project.run(raw)

    assert result.succeeded and runner.calls == []
    assert set(result.reused) == {Op.IMPORT_GEOMETRY, Op.OUTER_MESH, Op.ROTOR_MESH,
                                  Op.ASSEMBLE, Op.CHECK_MESH}


def test_missing_output_reruns_that_mesh_and_what_follows(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)
    (project.root / "cases/outer/constant/polyMesh/points").unlink()

    result, runner = project.run(raw)

    # The outer mesh is rebuilt; it comes out byte-identical, so the assembly's
    # recorded inputs still verify and it is reused (same rule as the engine).
    assert runner.calls == [("blockMesh", "outer"), ("snappyHexMesh", "outer")]
    assert Op.OUTER_MESH in result.executed
    assert {Op.ROTOR_MESH, Op.ASSEMBLE, Op.CHECK_MESH} <= set(result.reused)


def test_missing_merged_output_reruns_the_assembly(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)
    (project.root / "cases/merged/constant/polyMesh/owner").unlink()

    result, runner = project.run(raw)

    # Rebuilt byte-identical, so the verified checkMesh result is reused.
    assert runner.calls == [("mergeMeshes", "merged"), ("createPatch", "merged")]
    assert Op.CHECK_MESH in result.reused


def test_missing_check_log_reruns_check_only(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)
    (project.root / "logs/merged/04_checkMesh.log").unlink()

    _, runner = project.run(raw)

    assert runner.calls == [("checkMesh", "merged")]


def test_corrupted_output_is_not_reused(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)
    (project.root / "cases/rotor/constant/polyMesh/faces").write_text("edited", "utf-8")

    _, runner = project.run(raw)

    assert ("snappyHexMesh", "rotor") in runner.calls
    assert ("snappyHexMesh", "outer") not in runner.calls


def test_tool_identity_change_reruns_every_openfoam_operation(tmp_path: Path) -> None:
    raw = draft_for(tmp_path)
    Project(tmp_path).run(raw)

    result, runner = Project(tmp_path, version="v2506").run(raw)

    assert runner.calls == AMI_ORDER
    assert Op.IMPORT_GEOMETRY in result.reused  # geometry does not depend on OpenFOAM


def test_force_reruns_everything(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)

    result, runner = project.run(raw, force=True)

    assert runner.calls == AMI_ORDER and Op.IMPORT_GEOMETRY in result.executed


# --- spec section 9.2: what re-runs and what is reused -------------------------------

ROWS = [
    ("wake box", {"refinement__wake__level": 2},
     {("blockMesh", "outer"), ("snappyHexMesh", "outer")}, {"rotor"}),
    ("blade levels", {"refinement__blade_max_level": 3},
     {("snappyHexMesh", "rotor"), ("topoSet", "rotor")}, {"outer"}),
    ("layers", {"layers__enabled": True},
     {("snappyHexMesh", "rotor")}, {"outer"}),
    ("domain bounds", {"domain__bounds__maximum__x": 7.5},
     {("snappyHexMesh", "outer")}, {"rotor"}),
    ("patch names", {"domain__patches": {"inlet": "in_1"}},
     {("snappyHexMesh", "outer")}, {"rotor"}),
    ("cylinder", {"rotating_zone__diameter": 1.6},
     {("snappyHexMesh", "outer"), ("snappyHexMesh", "rotor")}, set()),
]


@pytest.mark.parametrize("name,edit,reruns,reused_cases", ROWS, ids=[r[0] for r in ROWS])
def test_section_9_2_rows(project: Project, name: str, edit: dict[str, Any],
                          reruns: set[tuple[str, str]], reused_cases: set[str]) -> None:
    project.run(draft_for(project.tmp))

    result, runner = project.run(draft_for(project.tmp, **edit))

    assert result.succeeded, [i.code for i in result.issues]
    assert reruns <= set(runner.calls)
    assert {("mergeMeshes", "merged"), ("checkMesh", "merged")} <= set(runner.calls)
    assert not {case for _, case in runner.calls} & reused_cases


def test_geometry_transform_reruns_the_rotor_only(project: Project) -> None:
    project.run(draft_for(project.tmp))

    moved = {"x": 0.01, "y": 0.0, "z": 0.0}
    result, runner = project.run(draft_for(project.tmp, geometry__translation=moved))

    assert Op.IMPORT_GEOMETRY in result.executed
    assert ("snappyHexMesh", "rotor") in runner.calls
    assert ("snappyHexMesh", "outer") not in runner.calls


@pytest.mark.parametrize("edit", [{"quality__max_non_orthogonality": 30.0},
                                  {"project_name": "Renamed"},
                                  {"export__fluent_msh": False}])
def test_settings_that_start_no_command(project: Project, edit: dict[str, Any]) -> None:
    project.run(draft_for(project.tmp))

    result, runner = project.run(draft_for(project.tmp, **edit))

    assert result.succeeded and runner.calls == []


# --- gates: validation, environment, resources --------------------------------------

def test_validation_gate_stops_before_any_command(project: Project) -> None:
    raw = draft_for(project.tmp, geometry__patch_name="AMI1")  # reserved name

    result, runner = project.run(raw)

    assert not result.succeeded and runner.calls == []
    assert codes(result)["RESERVED_PATCH_NAME"] is IssueSeverity.BLOCKING
    assert "no OpenFOAM command was started" in result.message


def test_mesh_point_in_the_rotor_stops_before_any_command(project: Project) -> None:
    raw = draft_for(project.tmp, rotating_zone__location_in_mesh={"x": 0.5, "y": 0.0113,
                                                                  "z": 0.0137})
    result, runner = project.run(raw)

    assert runner.calls == [] and "INNER_POINT_IN_ROTOR" in codes(result)


def test_missing_executable_stops_before_any_command(tmp_path: Path) -> None:
    runner = FakeVawtRunner()
    pipeline = VawtPipeline(runner, environment=openfoam_env(tmp_path, "v2512",
                                                            ("blockMesh", "snappyHexMesh")),
                            system_probe=lambda _: PLENTY)

    result = pipeline.run_sync(tmp_path / "p", VawtProjectConfig.model_validate(
        draft_for(tmp_path)))

    assert runner.calls == [] and "OPENFOAM_EXECUTABLES_NOT_FOUND" in codes(result)


LOW_RAM = SystemResources(available_ram_bytes=10**6, available_disk_bytes=10**12, cpu_count=8)
TIGHT_RAM = SystemResources(available_ram_bytes=10**9, available_disk_bytes=10**12,
                            cpu_count=8)


def test_blocked_resources_always_stop(tmp_path: Path) -> None:
    result, runner = Project(tmp_path, resources=LOW_RAM).run(
        draft_for(tmp_path), allow_high_resource_risk=True)

    assert runner.calls == [] and not result.succeeded
    assert "RAM_RISK" in codes(result)
    assert (tmp_path / "project/reports/resource_preflight.json").is_file()


def test_high_resource_risk_needs_acknowledgement(tmp_path: Path) -> None:
    raw = draft_for(tmp_path)
    blocked, runner = Project(tmp_path, resources=TIGHT_RAM).run(raw)
    estimate = json.loads((tmp_path / "project/reports/resource_preflight.json").read_text())
    assert estimate["status"] == "HIGH RESOURCE RISK", estimate["status"]
    assert runner.calls == [] and "HIGH_RESOURCE_RISK_NOT_ACKNOWLEDGED" in codes(blocked)

    allowed, runner = Project(tmp_path, resources=TIGHT_RAM).run(
        raw, allow_high_resource_risk=True)
    assert allowed.succeeded and runner.calls == AMI_ORDER


# --- failures, timeout, cancellation ---------------------------------------------------

@pytest.mark.parametrize("failing", AMI_ORDER, ids=[f"{c}-{s}" for c, s in AMI_ORDER])
def test_each_failing_command_stops_the_run(project: Project,
                                            failing: tuple[str, str]) -> None:
    result, runner = project.run(draft_for(project.tmp),
                                 FakeVawtRunner(fail=failing, project_root=project.root))

    assert not result.succeeded and result.state is RunState.FAILED
    assert runner.calls == AMI_ORDER[:AMI_ORDER.index(failing) + 1]
    assert "COMMAND_FAILED" in codes(result)
    assert read_status(project.root)["state"] == "FAILED"


def test_timeout_is_a_failure(project: Project) -> None:
    result, runner = project.run(draft_for(project.tmp), FakeVawtRunner(
        outcome={"snappyHexMesh": RunStatus.TIMEOUT}))

    assert not result.succeeded and "COMMAND_TIMEOUT" in codes(result)
    assert runner.calls == AMI_ORDER[:2]


def test_cancel_between_steps(project: Project) -> None:
    event = asyncio.Event()
    runner = FakeVawtRunner(cancel_after=("blockMesh", event), project_root=project.root)
    pipeline = VawtPipeline(runner, environment=openfoam_env(project.tmp, "v2512", VAWT_TOOLS),
                            system_probe=lambda _: PLENTY)
    config = VawtProjectConfig.model_validate(draft_for(project.tmp))

    result = asyncio.run(pipeline.run(project.root, config, cancel_event=event))

    assert result.state is RunState.CANCELLED and not result.succeeded
    assert runner.calls == [("blockMesh", "outer")]  # snappyHexMesh never starts
    assert read_status(project.root)["state"] == "CANCELLED"
    assert "vawt_outer_mesh" not in project.manifest()


def test_cancel_before_start_runs_nothing(project: Project) -> None:
    event = asyncio.Event()
    event.set()
    runner = FakeVawtRunner()
    pipeline = VawtPipeline(runner, environment=openfoam_env(project.tmp, "v2512", VAWT_TOOLS),
                            system_probe=lambda _: PLENTY)

    result = asyncio.run(pipeline.run(
        project.root, VawtProjectConfig.model_validate(draft_for(project.tmp)),
        cancel_event=event))

    assert result.state is RunState.CANCELLED and runner.calls == []
    assert "RUN_CANCELLED" in codes(result)


# --- result checks (V0 R3, R4) and the manifest -----------------------------------------

def test_wrong_region_is_an_error_and_is_never_recorded(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)  # a good rotor mesh is recorded first
    assert "vawt_rotor_mesh" in project.manifest()
    changed = draft_for(project.tmp, refinement__blade_max_level=3)

    result, _ = project.run(changed, FakeVawtRunner(wrong_region=["rotor"]))

    issue = next(i for i in result.issues if i.code == "MESHED_WRONG_REGION")
    assert issue.severity is IssueSeverity.ERROR
    assert issue.log_reference and issue.log_reference.endswith("logs/rotor/03_snappyHexMesh.log")
    assert issue.details["missing"] == ["AMI_rotor"]
    assert "vawt_rotor_mesh" not in project.manifest()  # old record invalidated, new not made

    # Even the configuration whose good mesh was recorded before must re-mesh now.
    again, runner = project.run(raw)
    assert again.succeeded and ("snappyHexMesh", "rotor") in runner.calls


def test_outer_point_inside_the_cylinder_is_caught(project: Project) -> None:
    result, runner = project.run(draft_for(project.tmp), FakeVawtRunner(wrong_region=["outer"]))

    assert "MESHED_WRONG_REGION" in codes(result)
    assert runner.calls == AMI_ORDER[:2]
    assert "vawt_outer_mesh" not in project.manifest()


def test_empty_ami_patches_are_an_error_and_not_recorded(project: Project) -> None:
    result, runner = project.run(draft_for(project.tmp), FakeVawtRunner(empty_ami=True))

    issue = next(i for i in result.issues if i.code == "AMI_PATCH_EMPTY")
    assert issue.severity is IssueSeverity.ERROR
    assert issue.log_reference and issue.log_reference.endswith("logs/merged/06_createPatch.log")
    assert ("checkMesh", "merged") not in runner.calls
    assert "vawt_assemble" not in project.manifest()


@pytest.mark.parametrize("mode,case", [("AMI", "rotor"), ("CELL_ZONE", "merged")])
def test_missing_rotating_zone_is_an_error(project: Project, mode: str, case: str) -> None:
    result, _ = project.run(draft_for(project.tmp, mode), FakeVawtRunner(no_zone=[case]))

    assert codes(result)["ROTATING_ZONE_MISSING"] is IssueSeverity.ERROR


# --- region rule -----------------------------------------------------------------

def test_ami_with_two_regions_is_valid(project: Project) -> None:
    result, _ = project.run(draft_for(project.tmp), FakeVawtRunner(regions=2))

    assert result.succeeded and report(project)["assessment"]["mesh_validity"] == "VALID"


@pytest.mark.parametrize("mode,regions", [("AMI", 1), ("AMI", 3), ("CELL_ZONE", 2),
                                          ("ROTOR_ONLY", 2)])
def test_unexpected_region_count_is_invalid(project: Project, mode: str, regions: int) -> None:
    result, _ = project.run(draft_for(project.tmp, mode), FakeVawtRunner(regions=regions))

    assert not result.succeeded
    assert codes(result)["REGION_COUNT_UNEXPECTED"] is IssueSeverity.ERROR
    assert report(project)["assessment"]["mesh_validity"] == "INVALID"


def test_ami_with_two_regions_and_a_failed_check_is_still_invalid(project: Project) -> None:
    result, _ = project.run(draft_for(project.tmp),
                            FakeVawtRunner(regions=2, checkmesh_failure=True))

    assert not result.succeeded
    assert "CHECKMESH_FAILURE" in codes(result)
    assert "REGION_COUNT_UNEXPECTED" not in codes(result)
    assert report(project)["assessment"]["mesh_validity"] == "INVALID"


# --- status file, run record, lock ----------------------------------------------------

def test_status_file_tracks_each_stage(project: Project) -> None:
    result, runner = project.run(draft_for(project.tmp))

    seen = [(s["stage"], s["step"], s["state"]) for s in runner.statuses]
    assert seen[0] == ("vawt_outer_mesh", 4, "RUNNING")
    assert ("vawt_assemble", 6, "RUNNING") in seen
    assert all(s["run_id"] == result.run_id and s["total_steps"] == 8 for s in runner.statuses)
    final = read_status(project.root)
    assert final is not None and final["state"] == "SUCCEEDED" and final["step"] == 8


def test_run_record_lists_commands_and_outcome(project: Project) -> None:
    result, _ = project.run(draft_for(project.tmp))

    assert result.run_record_path is not None
    record = json.loads(result.run_record_path.read_text("utf-8"))
    assert record["kind"] == "vawt_mesh" and record["state"] == "SUCCEEDED"
    assert [c["argv"][0] for c in record["commands"]] == [c for c, _ in AMI_ORDER]


def test_a_second_run_is_refused_and_does_not_touch_the_status(project: Project) -> None:
    raw = draft_for(project.tmp)
    project.run(raw)
    before = read_status(project.root)
    lock = project.root / LOCK_PATH
    lock.write_text(json.dumps({"pid": 1, "host": "elsewhere", "run_id": "x"}), "utf-8")

    result, runner = project.run(raw)

    assert not result.succeeded and runner.calls == []
    assert read_status(project.root) == before


# --- geometry artifact: written once, copied ------------------------------------------

def test_artifact_is_copied_not_regenerated_when_the_layout_changes(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[Path] = []
    original = pipeline_module.write_stl_artifact

    def counting(mesh: Any, path: Path, name: str) -> bool:
        writes.append(path)
        return original(mesh, path, name)

    monkeypatch.setattr(pipeline_module, "write_stl_artifact", counting)
    project.run(draft_for(project.tmp))  # AMI: the rotor case reads the surface

    result, _ = project.run(draft_for(project.tmp, "CELL_ZONE"))  # now the merged case does

    assert result.succeeded
    assert writes == [project.root / "cases/rotor/constant/triSurface/rotor.stl"]
    rotor = project.root / "cases/rotor/constant/triSurface/rotor.stl"
    merged = project.root / "cases/merged/constant/triSurface/rotor.stl"
    assert merged.read_bytes() == rotor.read_bytes()
    assert Op.IMPORT_GEOMETRY in result.reused


def test_changed_geometry_is_written_again(project: Project,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    original = pipeline_module.write_stl_artifact
    monkeypatch.setattr(pipeline_module, "write_stl_artifact",
                        lambda m, p, n: calls.append(p) or original(m, p, n))
    project.run(draft_for(project.tmp))
    project.run(draft_for(project.tmp, geometry__translation={"x": 0.01, "y": 0.0, "z": 0.0}))

    assert len(calls) == 2


def test_raw_drafts_are_not_modified(project: Project) -> None:
    raw = draft_for(project.tmp)
    snapshot = copy.deepcopy(raw)
    project.run(raw)
    assert raw == snapshot
