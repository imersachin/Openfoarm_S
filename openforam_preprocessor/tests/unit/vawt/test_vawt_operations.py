"""V3: VAWT operations and dependency tables, and agreement with what actually runs."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fakes import PLENTY, openfoam_env
from tests.fakes_vawt import VAWT_TOOLS, FakeVawtRunner
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import InterfaceType, VawtProjectConfig
from vawt.operations import (
    CACHED,
    DIRECT_CONSUMERS,
    DOWNSTREAM,
    GRAPH,
    LAYOUT,
    PLANNER,
    VawtOperation,
    applicable,
    detect_changes,
    operations_for,
    plan_changes,
)
from vawt.pipeline import VawtPipeline

Op = VawtOperation


def full(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    """Every optional setting active: features, absolute-capable layers, wake."""
    draft = preset_draft(tmp_path, **kwargs)
    draft["refinement"]["extract_features"] = True
    draft["layers"].update(enabled=True, min_thickness_m=1e-4)
    return draft


def leaves(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(data, dict):
        return [leaf for k, v in data.items() for leaf in leaves(v, f"{prefix}{k}.")]
    return [(prefix[:-1], data)]


def test_declaration_order_is_an_execution_order() -> None:
    order = list(VawtOperation)
    for upstream, downstream in DOWNSTREAM.items():
        for op in downstream:
            assert order.index(upstream) < order.index(op), (upstream, op)


@pytest.mark.parametrize("include_domain", [True, False])
def test_every_configuration_field_has_a_rule(tmp_path: Path, include_domain: bool) -> None:
    config = VawtProjectConfig.model_validate(full(tmp_path, include_domain=include_domain))

    unmapped = GRAPH.unmapped(path for path, _ in leaves(config.model_dump(mode="json")))

    assert unmapped == frozenset()


def test_every_rule_names_a_real_field_or_the_layout(tmp_path: Path) -> None:
    paths = {p for p, _ in leaves(VawtProjectConfig.model_validate(
        full(tmp_path)).model_dump(mode="json"))}
    for rule in DIRECT_CONSUMERS:
        assert rule == LAYOUT or any(p == rule or p.startswith(f"{rule}.") for p in paths), rule


def test_layout_change_is_detected(tmp_path: Path) -> None:
    ami = VawtProjectConfig.model_validate(full(tmp_path))
    single = ami.model_copy(update={"rotating_zone": ami.rotating_zone.model_copy(
        update={"interface": InterfaceType.CELL_ZONE})})
    no_domain = VawtProjectConfig.model_validate(full(tmp_path, include_domain=False))

    assert LAYOUT in detect_changes(ami, single).changed_paths
    assert LAYOUT in detect_changes(single, no_domain).changed_paths
    assert LAYOUT not in detect_changes(ami, ami).changed_paths


def test_operations_per_layout(tmp_path: Path) -> None:
    ami = VawtProjectConfig.model_validate(full(tmp_path))
    rotor_only = VawtProjectConfig.model_validate(full(tmp_path, include_domain=False))

    assert operations_for(ami) == (
        Op.VALIDATE, Op.IMPORT_GEOMETRY, Op.GENERATE_CASES, Op.OUTER_MESH, Op.ROTOR_FEATURES,
        Op.ROTOR_MESH, Op.ASSEMBLE, Op.CHECK_MESH, Op.VALIDATE_MESH)
    assert not applicable(Op.ASSEMBLE, rotor_only)
    assert not applicable(Op.OUTER_MESH, rotor_only)


def test_inactive_layer_settings_are_ignored(tmp_path: Path) -> None:
    base = VawtProjectConfig.model_validate(full(tmp_path))  # RELATIVE sizing
    changed = base.model_copy(update={"layers": base.layers.model_copy(
        update={"first_layer_thickness": 0.123})})

    plan = plan_changes(base, changed)

    assert plan.is_empty and plan.inactive_paths == {"layers.first_layer_thickness"}


def test_plan_explains_why(tmp_path: Path) -> None:
    base = VawtProjectConfig.model_validate(full(tmp_path))
    raw = base.model_dump(mode="json")
    raw["refinement"]["wake"]["level"] = 2
    plan = PLANNER.plan(detect_changes(base, VawtProjectConfig.model_validate(raw)),
                        VawtProjectConfig.model_validate(raw))

    assert set(plan.operations) & CACHED == {Op.OUTER_MESH, Op.ASSEMBLE, Op.CHECK_MESH}
    assert plan.reasons[Op.OUTER_MESH] == {"refinement.wake.level"}


# --- the plan agrees with what the pipeline actually re-runs, for every field -----------

def candidates(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [value + 1, value - 1]
    if isinstance(value, float):
        step = 0.011 * max(abs(value), 1e-3)
        return [value + step, value - step]
    if isinstance(value, str) and value.endswith(".stl"):
        return []  # another file: covered by the geometry tests
    if isinstance(value, str):
        options = ["CELL_ZONE", "AMI", "ABSOLUTE", "RELATIVE", "y", "x", "m", "cm"]
        return [o for o in options if o != value] + [value + "b"]
    return []


def set_leaf(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    result = copy.deepcopy(data)
    *parents, leaf = path.split(".")
    target = result
    for key in parents:
        target = target[key]
    target[leaf] = value
    return result


# Fields with no change that passes validation and generation in one step.
NO_RUNNABLE_CHANGE = {
    "schema_version": "only the current version is valid",
    "openfoam_profile": "only openfoam.com is supported (generation refuses the other)",
    "geometry.source_path": "another file: covered by the geometry tests",
}


def test_plan_matches_what_runs_for_every_field(tmp_path: Path) -> None:
    root = tmp_path / "project"
    env = openfoam_env(tmp_path, "v2512", VAWT_TOOLS)

    def run(raw: dict[str, Any]) -> tuple[bool, set[VawtOperation]]:
        result = VawtPipeline(FakeVawtRunner(), environment=env,
                              system_probe=lambda _: PLENTY).run_sync(
            root, VawtProjectConfig.model_validate(raw))
        return result.succeeded, set(result.executed) & CACHED

    base = VawtProjectConfig.model_validate(full(tmp_path)).model_dump(mode="json")
    assert run(base)[0]
    mismatches, skipped = [], set()
    for path, value in leaves(base):
        tried = False
        for candidate in candidates(value):
            raw = set_leaf(base, path, candidate)
            try:
                new = VawtProjectConfig.model_validate(raw)
            except ValidationError:
                continue
            succeeded, executed = run(raw)
            if not succeeded:  # validation or generation refused it; restore and try another
                run(base)
                continue
            tried = True
            plan = plan_changes(VawtProjectConfig.model_validate(base), new)
            planned = set(plan.operations) & CACHED
            if executed != planned:
                mismatches.append((path, candidate, sorted(planned), sorted(executed)))
            assert run(base)[0]
            break
        if not tried:
            skipped.add(path)

    assert mismatches == []
    assert skipped == set(NO_RUNNABLE_CHANGE)
