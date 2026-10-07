"""M6: environment check and resource preflight gate expensive meshing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.config.models import ProjectConfig
from core.workflow.pipeline import PREFLIGHT_REPORT
from mesh.estimator import ResourceEstimator, SystemResources
from tests.fakes import FakeOpenFOAMRunner, fake_pipeline, openfoam_env
from tests.helpers import build_config

GB = 10**9


def needed_ram(config: ProjectConfig) -> float:
    estimator = ResourceEstimator()
    return estimator.estimate_cells(config, 6.0).total * estimator.model.ram_bytes_per_cell


def run(tmp_path: Path, config: ProjectConfig, ram: float, **kwargs):
    runner = FakeOpenFOAMRunner()
    pipeline = fake_pipeline(
        runner, openfoam_env(tmp_path), SystemResources(int(ram), 1000 * GB, 8)
    )
    result = asyncio.run(pipeline.generate_mesh(tmp_path / "case", config, **kwargs))
    return result, runner


def codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_safe_preflight_runs_and_writes_report(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl)

    result, runner = run(tmp_path, config, ram=100 * GB)

    assert result.succeeded
    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    report = json.loads((tmp_path / "case" / PREFLIGHT_REPORT).read_text("utf-8"))
    assert report["status"] == "SAFE"


def test_blocked_preflight_runs_nothing(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl)

    result, runner = run(tmp_path, config, ram=needed_ram(config) / 2)

    assert not result.succeeded
    assert runner.calls == []
    assert "RAM_RISK" in codes(result)
    report = json.loads((tmp_path / "case" / PREFLIGHT_REPORT).read_text("utf-8"))
    assert report["status"] == "BLOCKED"


def test_high_risk_requires_acknowledgement(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl)
    ram = needed_ram(config) * 1.1

    refused, runner = run(tmp_path, config, ram=ram)
    assert not refused.succeeded
    assert runner.calls == []
    assert "HIGH_RESOURCE_RISK_NOT_ACKNOWLEDGED" in codes(refused)

    accepted, runner = run(tmp_path, config, ram=ram, allow_high_resource_risk=True)
    assert accepted.succeeded
    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    assert "RAM_RISK" in codes(accepted)  # still reported


def test_warning_preflight_proceeds_with_issue(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl)

    result, runner = run(tmp_path, config, ram=needed_ram(config) * 1.5)

    assert result.succeeded
    assert "RAM_RISK" in codes(result)


def test_preflight_skipped_when_mesh_is_reused(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl)
    run(tmp_path, config, ram=100 * GB)
    (tmp_path / "case" / PREFLIGHT_REPORT).unlink()

    # Even with "no RAM", a verified cached mesh needs no expensive meshing.
    result, runner = run(tmp_path, config, ram=1)

    assert result.succeeded
    assert runner.calls == []
    assert not (tmp_path / "case" / PREFLIGHT_REPORT).exists()


def test_missing_executables_stop_before_anything_runs(tmp_path: Path, cube_stl: Path) -> None:
    runner = FakeOpenFOAMRunner()
    env = openfoam_env(tmp_path, tools=("blockMesh",))
    pipeline = fake_pipeline(runner, env)

    result = asyncio.run(pipeline.generate_mesh(tmp_path / "case", build_config(cube_stl)))

    assert not result.succeeded
    assert runner.calls == []
    issue = next(i for i in result.issues if i.code == "OPENFOAM_EXECUTABLES_NOT_FOUND")
    assert issue.details["missing"] == ["checkMesh", "snappyHexMesh"]


def test_environment_warnings_are_carried_into_result(tmp_path: Path, cube_stl: Path) -> None:
    runner = FakeOpenFOAMRunner()
    pipeline = fake_pipeline(runner, openfoam_env(tmp_path, version="11"))

    result = asyncio.run(pipeline.generate_mesh(tmp_path / "case", build_config(cube_stl)))

    assert result.succeeded
    assert "OPENFOAM_PROFILE_MISMATCH" in codes(result)


def test_mesh_report_has_separate_assessments(tmp_path: Path, cube_stl: Path) -> None:
    run(tmp_path, build_config(cube_stl), ram=100 * GB)

    report = json.loads(
        (tmp_path / "case" / "reports" / "mesh_quality_report.json").read_text("utf-8")
    )
    assert report["assessment"]["mesh_validity"] == "VALID"
    assert report["assessment"]["cfd_accuracy"] == "NOT_ASSESSED"
