from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.config.manager import ChangeSet, ConfigurationManager
from core.config.models import ProjectConfig
from core.workflow.dependency_graph import (
    DIRECT_CONSUMERS,
    DOWNSTREAM,
    DependencyGraph,
    PipelineOperation,
)
from core.workflow.planner import INITIAL_BUILD, ExecutionPlanner
from tests.helpers import build_config

Op = PipelineOperation

MESH_RUN = (Op.GENERATE_BACKGROUND_MESH, Op.GENERATE_MESH, Op.CHECK_MESH, Op.VALIDATE_MESH)


def base(**surface: Any) -> ProjectConfig:
    return build_config(Path("part.stl"), source_units="mm", **surface)


def changed(config: ProjectConfig, path: str, value: Any) -> ProjectConfig:
    data = config.model_dump(mode="json")
    *parents, leaf = path.split(".")
    target = data
    for key in parents:
        target = target[key]
    target[leaf] = value
    return ProjectConfig.model_validate(data)


def plan_change(old: ProjectConfig, new: ProjectConfig):
    changes = ConfigurationManager(Path("."), DependencyGraph()).detect_changes(old, new)
    return ExecutionPlanner().plan(changes, new)


def leaf_paths(data: Any, prefix: str = "") -> set[str]:
    if isinstance(data, dict):
        return set().union(*(leaf_paths(v, f"{prefix}{k}.") for k, v in data.items()))
    return {prefix.rstrip(".")}


# --- graph structure -------------------------------------------------------

def test_declaration_order_is_topological() -> None:
    order = list(PipelineOperation)
    for upstream, downstream in DOWNSTREAM.items():
        for operation in downstream:
            assert order.index(upstream) < order.index(operation), (upstream, operation)


def test_every_configuration_field_has_a_dependency_rule() -> None:
    graph = DependencyGraph()
    paths = leaf_paths(base().model_dump(mode="json"))

    assert graph.unmapped(paths) == frozenset()


def test_every_rule_refers_to_a_real_configuration_field() -> None:
    paths = leaf_paths(base().model_dump(mode="json"))
    for rule in DIRECT_CONSUMERS:
        assert any(p == rule or p.startswith(f"{rule}.") for p in paths), rule


# --- documented invalidation scenarios ------------------------------------

def test_translation_change_invalidates_geometry_chain() -> None:
    old = base(extract_features=True)
    plan = plan_change(old, changed(old, "geometry.translation.x", 0.25))

    assert plan.operations == (
        Op.IMPORT_GEOMETRY, Op.VALIDATE_GEOMETRY, Op.GENERATE_BACKGROUND_MESH,
        Op.EXTRACT_FEATURES, Op.GENERATE_MESH, Op.CHECK_MESH, Op.VALIDATE_MESH,
    )
    assert plan.reasons[Op.EXTRACT_FEATURES] == frozenset({"geometry.translation.x"})


def test_translation_change_skips_feature_extraction_when_disabled() -> None:
    old = base()
    plan = plan_change(old, changed(old, "geometry.translation.x", 0.25))

    assert Op.EXTRACT_FEATURES not in plan.operations
    assert plan.skipped == (Op.EXTRACT_FEATURES,)


def test_surface_refinement_change_regenerates_snappy_and_mesh_only() -> None:
    old = base()
    plan = plan_change(old, changed(old, "mesh.surface.maximum_level", 5))

    assert plan.operations == (Op.GENERATE_SNAPPY_DICT, *MESH_RUN)
    assert Op.IMPORT_GEOMETRY not in plan.operations
    assert Op.GENERATE_BLOCK_MESH_DICT not in plan.operations


def test_quality_threshold_change_only_revalidates() -> None:
    old = base()
    plan = plan_change(old, changed(old, "mesh.quality.max_non_orthogonality", 50))

    assert plan.operations == (Op.VALIDATE_MESH,)
    assert plan.skipped == ()


def test_snappy_quality_change_invalidates_mesh() -> None:
    old = base()
    plan = plan_change(old, changed(old, "mesh.snappy_quality.max_non_orthogonality", 50))

    assert plan.operations == (Op.GENERATE_MESH_QUALITY_DICT, *MESH_RUN)


def test_background_change_does_not_touch_geometry_or_snappy_dict() -> None:
    old = base()
    plan = plan_change(old, changed(old, "mesh.background.base_cell_size", 0.25))

    assert plan.operations == (Op.GENERATE_BLOCK_MESH_DICT, *MESH_RUN)


def test_snappy_rerun_always_rebuilds_background_mesh() -> None:
    # snappyHexMesh -overwrite replaces the background mesh in place.
    old = base()
    plan = plan_change(old, changed(old, "mesh.layers.enabled", True))

    assert Op.GENERATE_BACKGROUND_MESH in plan.operations
    assert Op.GENERATE_BLOCK_MESH_DICT not in plan.operations


@pytest.mark.parametrize(
    ("path", "value"),
    [("project_name", "Renamed"), ("mesh.overwrite_existing_mesh", False)],
)
def test_unrelated_settings_cause_no_work(path: str, value: Any) -> None:
    old = base()
    plan = plan_change(old, changed(old, path, value))

    assert plan.is_empty
    assert plan.skipped == ()


def test_feature_angle_change_with_extraction_enabled() -> None:
    old = base(extract_features=True)
    plan = plan_change(old, changed(old, "mesh.surface.feature_angle_deg", 45))

    assert plan.operations == (
        Op.GENERATE_FEATURE_DICT, Op.GENERATE_SNAPPY_DICT, Op.GENERATE_BACKGROUND_MESH,
        Op.EXTRACT_FEATURES, Op.GENERATE_MESH, Op.CHECK_MESH, Op.VALIDATE_MESH,
    )


def test_disabling_feature_extraction_syncs_dictionaries_without_extracting() -> None:
    old = base(extract_features=True)
    plan = plan_change(old, changed(old, "mesh.surface.extract_features", False))

    assert Op.GENERATE_FEATURE_DICT in plan.operations  # removes stale dictionary
    assert Op.GENERATE_SNAPPY_DICT in plan.operations
    assert plan.skipped == (Op.EXTRACT_FEATURES,)


def test_patch_name_change_regenerates_artifact_and_references() -> None:
    old = base()
    plan = plan_change(old, changed(old, "geometry.patch_name", "body"))

    assert {Op.IMPORT_GEOMETRY, Op.GENERATE_FEATURE_DICT, Op.GENERATE_SNAPPY_DICT} <= set(
        plan.operations
    )
    assert Op.GENERATE_BLOCK_MESH_DICT not in plan.operations


def test_profile_change_reruns_openfoam_but_not_geometry() -> None:
    old = base()
    plan = plan_change(old, changed(old, "openfoam_profile", "openfoam_foundation"))

    assert Op.IMPORT_GEOMETRY not in plan.operations
    assert Op.GENERATE_SNAPPY_DICT not in plan.operations
    assert set(MESH_RUN) <= set(plan.operations)


# --- planner behaviour ----------------------------------------------------

def test_combined_changes_merge_and_keep_reasons() -> None:
    old = base()
    new = changed(changed(old, "mesh.quality.max_non_orthogonality", 50),
                  "mesh.background.base_cell_size", 0.25)

    plan = plan_change(old, new)

    assert plan.operations == (Op.GENERATE_BLOCK_MESH_DICT, *MESH_RUN)
    assert plan.reasons[Op.VALIDATE_MESH] == frozenset({
        "mesh.quality.max_non_orthogonality", "mesh.background.base_cell_size",
    })
    assert plan.reasons[Op.GENERATE_BLOCK_MESH_DICT] == frozenset({
        "mesh.background.base_cell_size",
    })


def test_parent_path_change_covers_nested_rules() -> None:
    plan = ExecutionPlanner().plan(
        ChangeSet(changed_paths=frozenset({"mesh.surface"}), affected_operations=frozenset()),
        base(extract_features=True),
    )

    assert Op.GENERATE_FEATURE_DICT in plan.operations
    assert Op.GENERATE_SNAPPY_DICT in plan.operations


def test_unmapped_path_invalidates_everything_and_is_reported() -> None:
    changes = ChangeSet(
        changed_paths=frozenset({"mesh.future_setting"}), affected_operations=frozenset()
    )

    plan = ExecutionPlanner().plan(changes, base())


    assert plan.unmapped_paths == frozenset({"mesh.future_setting"})
    assert set(plan.operations) | set(plan.skipped) == set(PipelineOperation)


def test_full_plan_for_new_project() -> None:
    plan = ExecutionPlanner().plan_full(base())

    assert plan.operations == tuple(op for op in Op if op is not Op.EXTRACT_FEATURES)
    assert plan.skipped == (Op.EXTRACT_FEATURES,)
    assert all(paths == frozenset({INITIAL_BUILD}) for paths in plan.reasons.values())
    assert ExecutionPlanner().plan_full(base(extract_features=True)).skipped == ()


def test_plan_is_deterministic() -> None:
    old = base()
    new = changed(changed(old, "geometry.scale", 2), "mesh.layers.enabled", True)

    first, second = plan_change(old, new), plan_change(old, new)

    assert first == second
    assert first.as_dict() == second.as_dict()
    assert first.as_dict()["operations"][0] == "import_geometry"


def test_inactive_settings_are_ignored_and_reported() -> None:
    old = base()  # feature extraction and layers disabled
    new = changed(changed(old, "mesh.surface.feature_refinement_level", 5),
                  "mesh.layers.number_of_layers", 7)

    plan = plan_change(old, new)

    assert plan.is_empty
    assert plan.inactive_paths == frozenset({
        "mesh.surface.feature_refinement_level", "mesh.layers.number_of_layers",
    })


def test_inactive_setting_counts_when_enabled_in_same_change() -> None:
    old = base()
    new = changed(changed(old, "mesh.surface.feature_refinement_level", 5),
                  "mesh.surface.extract_features", True)

    plan = plan_change(old, new)

    assert plan.inactive_paths == frozenset()
    assert Op.EXTRACT_FEATURES in plan.operations
    assert "mesh.surface.feature_refinement_level" in plan.reasons[Op.GENERATE_SNAPPY_DICT]
