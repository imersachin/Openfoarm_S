"""M5: verified artifact reuse in the meshing pipeline (with a fake OpenFOAM)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from core.artifacts import MANIFEST_PATH
from core.config.manager import ConfigurationManager
from core.config.models import ProjectConfig
from core.workflow.dependency_graph import DependencyGraph, PipelineOperation
from core.workflow.pipeline import MeshPipeline, PipelineResult
from core.workflow.planner import ExecutionPlanner
from tests.fakes import FakeOpenFOAMRunner
from tests.helpers import build_config

Op = PipelineOperation

# Operations whose outputs are cached (the rest are cheap and always run).
CACHED = {
    Op.IMPORT_GEOMETRY, Op.VALIDATE_GEOMETRY, Op.EXTRACT_FEATURES,
    Op.GENERATE_BACKGROUND_MESH, Op.GENERATE_MESH, Op.CHECK_MESH,
}
MESH_OPS = {Op.GENERATE_BACKGROUND_MESH, Op.GENERATE_MESH, Op.CHECK_MESH}


def changed(config: ProjectConfig, path: str, value: Any) -> ProjectConfig:
    data = config.model_dump(mode="json")
    *parents, leaf = path.split(".")
    target = data
    for key in parents:
        target = target[key]
    target[leaf] = value
    return ProjectConfig.model_validate(data)


class Harness:
    def __init__(self, tmp_path: Path, version: str = "v2312") -> None:
        self.case = tmp_path / "case"
        self.no_bin = tmp_path / "no-bin"
        self.version = version

    def run(
        self, config: ProjectConfig, *, version: str | None = None, force: bool = False,
        runner: FakeOpenFOAMRunner | None = None,
    ) -> tuple[PipelineResult, FakeOpenFOAMRunner]:
        runner = runner or FakeOpenFOAMRunner()
        env = {
            "PATH": str(self.no_bin), "WM_PROJECT": "OpenFOAM",
            "WM_PROJECT_VERSION": version or self.version,
        }
        pipeline = MeshPipeline(runner, environment=env)
        result = asyncio.run(pipeline.generate_mesh(self.case, config, force=force))
        assert result.succeeded, result.issues
        return result, runner


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.fixture
def config(cube_stl: Path) -> ProjectConfig:
    return build_config(cube_stl)


def cached_executed(result: PipelineResult) -> set[PipelineOperation]:
    return set(result.executed) & CACHED


# --- the seven cache behaviours from docs/testing_strategy.md --------------

def test_identical_inputs_reuse_everything(harness: Harness, config: ProjectConfig) -> None:
    first, _ = harness.run(config)
    second, runner = harness.run(config)

    assert cached_executed(first) == CACHED - {Op.EXTRACT_FEATURES}
    assert runner.calls == []
    assert cached_executed(second) == set()
    assert set(second.reused) == CACHED - {Op.EXTRACT_FEATURES}
    assert Op.VALIDATE_MESH in second.executed  # cheap, always re-run


def test_changed_input_rebuilds_affected_steps(harness: Harness, config: ProjectConfig) -> None:
    harness.run(config)
    result, runner = harness.run(changed(config, "mesh.surface.maximum_level", 4))

    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    assert Op.IMPORT_GEOMETRY in result.reused


def test_quality_threshold_change_only_revalidates(
    harness: Harness, config: ProjectConfig
) -> None:
    harness.run(config)
    stricter = changed(config, "mesh.quality.max_non_orthogonality", 30)

    result, runner = harness.run(stricter)

    assert runner.calls == []
    assert cached_executed(result) == set()
    report = json.loads((harness.case / "reports" / "mesh_quality_report.json").read_text())
    assert report["status"] == "review"  # 40 > 30 with the new limit


def test_openfoam_version_change_reruns_openfoam_only(
    harness: Harness, config: ProjectConfig
) -> None:
    harness.run(config)
    result, runner = harness.run(config, version="v2406")

    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    assert {Op.IMPORT_GEOMETRY, Op.VALIDATE_GEOMETRY} <= set(result.reused)


def test_missing_mesh_file_is_rejected(harness: Harness, config: ProjectConfig) -> None:
    harness.run(config)
    (harness.case / "constant" / "polyMesh" / "owner").unlink()

    _, runner = harness.run(config)

    # The rebuilt mesh is identical, so the checkMesh result verifies and is reused.
    assert runner.calls == ["blockMesh", "snappyHexMesh"]
    assert (harness.case / "constant" / "polyMesh" / "owner").is_file()


def test_corrupted_mesh_file_is_rejected(harness: Harness, config: ProjectConfig) -> None:
    harness.run(config)
    points = harness.case / "constant" / "polyMesh" / "points"
    original = points.read_text("utf-8")
    points.write_text("garbage", "utf-8")

    _, runner = harness.run(config)

    assert runner.calls == ["blockMesh", "snappyHexMesh"]
    assert points.read_text("utf-8") == original


def test_tampered_input_dictionary_is_a_dependency_mismatch(
    harness: Harness, config: ProjectConfig
) -> None:
    harness.run(config)
    store_before = json.loads((harness.case / MANIFEST_PATH).read_text())
    mesh_inputs = store_before["records"]["generate_mesh"]["inputs"]

    assert "system/snappyHexMeshDict" in mesh_inputs
    assert "constant/triSurface/part.stl" in mesh_inputs

    # A changed transformed geometry (dependency) must invalidate the mesh.
    result, runner = harness.run(changed(config, "geometry.translation.x", 0.25))
    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    assert Op.IMPORT_GEOMETRY in result.executed


# --- further failure paths ------------------------------------------------

def test_corrupt_manifest_reruns_everything_with_warning(
    harness: Harness, config: ProjectConfig
) -> None:
    harness.run(config)
    (harness.case / MANIFEST_PATH).write_text("{broken", encoding="utf-8")

    result, runner = harness.run(config)

    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    assert "ARTIFACT_MANIFEST_UNREADABLE" in {i.code for i in result.issues}
    assert json.loads((harness.case / MANIFEST_PATH).read_text())["version"] == 1


def test_source_file_edited_in_place_rebuilds_geometry(
    harness: Harness, config: ProjectConfig, cube_stl: Path
) -> None:
    import trimesh

    harness.run(config)
    trimesh.creation.box(extents=(1.0, 1.0, 1.5)).export(cube_stl)  # same path

    result, runner = harness.run(config)

    assert Op.IMPORT_GEOMETRY in result.executed
    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]


def test_deleted_geometry_artifact_is_rebuilt(harness: Harness, config: ProjectConfig) -> None:
    harness.run(config)
    (harness.case / "constant" / "triSurface" / "part.stl").unlink()

    result, _ = harness.run(config)

    assert Op.IMPORT_GEOMETRY in result.executed
    assert (harness.case / "constant" / "triSurface" / "part.stl").is_file()


def test_checkmesh_diagnostic_sets_do_not_invalidate_mesh(
    harness: Harness, config: ProjectConfig
) -> None:
    harness.run(config)
    assert (harness.case / "constant" / "polyMesh" / "sets" / "skewFaces").is_file()

    _, runner = harness.run(config)

    assert runner.calls == []


def test_failed_meshing_is_never_reused(harness: Harness, config: ProjectConfig) -> None:
    harness.run(config)
    finer = changed(config, "mesh.surface.maximum_level", 4)
    failing = FakeOpenFOAMRunner(fail="snappyHexMesh")
    pipeline = MeshPipeline(failing, environment={"PATH": str(harness.no_bin)})
    assert not asyncio.run(pipeline.generate_mesh(harness.case, finer)).succeeded

    # Back to the original config: its old record was invalidated before the
    # failed run, and the polyMesh on disk is the partial one.
    _, runner = harness.run(config)

    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]


def test_feature_extraction_is_cached_and_invalidated(
    harness: Harness, cube_stl: Path
) -> None:
    config = build_config(cube_stl, extract_features=True)
    harness.run(config)

    _, runner = harness.run(config)
    assert runner.calls == []

    _, runner = harness.run(changed(config, "mesh.surface.feature_angle_deg", 45))
    assert runner.calls == ["surfaceFeatureExtract", "blockMesh", "snappyHexMesh", "checkMesh"]


def test_missing_emesh_after_success_is_an_error(harness: Harness, cube_stl: Path) -> None:
    runner = FakeOpenFOAMRunner(no_output=("surfaceFeatureExtract",))
    pipeline = MeshPipeline(runner, environment={"PATH": str(harness.no_bin)})

    result = asyncio.run(
        pipeline.generate_mesh(harness.case, build_config(cube_stl, extract_features=True))
    )

    assert not result.succeeded
    assert result.issues[-1].code == "FEATURE_OUTPUT_MISSING"
    assert "blockMesh" not in runner.calls


def test_missing_mesh_output_after_success_is_an_error(
    harness: Harness, config: ProjectConfig
) -> None:
    runner = FakeOpenFOAMRunner(no_output=("blockMesh", "snappyHexMesh"))
    pipeline = MeshPipeline(runner, environment={"PATH": str(harness.no_bin)})

    result = asyncio.run(pipeline.generate_mesh(harness.case, config))

    assert not result.succeeded
    assert result.issues[-1].code == "MESH_OUTPUT_MISSING"
    assert "checkMesh" not in runner.calls


def test_force_ignores_cache(harness: Harness, config: ProjectConfig) -> None:
    harness.run(config)

    result, runner = harness.run(config, force=True)

    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    assert Op.IMPORT_GEOMETRY in result.executed


# --- the planner and the cache agree --------------------------------------

@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("project_name", "Renamed"),
        ("openfoam_profile", "openfoam_foundation"),
        ("geometry.source_units", "cm"),
        ("geometry.scale", 2.0),
        ("geometry.rotation_deg.z", 90.0),
        ("geometry.translation.y", 0.1),
        ("geometry.patch_name", "body"),
        ("mesh.background.base_cell_size", 0.25),
        ("mesh.background.expansion_ratio", 1.1),
        ("mesh.surface.minimum_level", 1),
        ("mesh.surface.maximum_level", 4),
        ("mesh.surface.feature_angle_deg", 45.0),
        ("mesh.surface.feature_refinement_level", 2),
        ("mesh.surface.extract_features", True),
        ("mesh.layers.enabled", True),
        ("mesh.layers.number_of_layers", 5),
        ("mesh.layers.expansion_ratio", 1.3),
        ("mesh.snappy_quality.max_non_orthogonality", 60.0),
        ("mesh.quality.max_non_orthogonality", 60.0),
        ("mesh.location_in_mesh.y", 0.25),
        ("mesh.max_global_cells", 1_000_000),
        ("mesh.overwrite_existing_mesh", False),
    ],
)
def test_executed_work_matches_the_dependency_plan(
    harness: Harness, config: ProjectConfig, path: str, value: Any
) -> None:
    new = changed(config, path, value)
    changes = ConfigurationManager(Path("."), DependencyGraph()).detect_changes(config, new)
    plan = ExecutionPlanner().plan(changes, new)

    harness.run(config)
    result, _ = harness.run(new)

    assert cached_executed(result) == set(plan.operations) & CACHED, plan.as_dict()
