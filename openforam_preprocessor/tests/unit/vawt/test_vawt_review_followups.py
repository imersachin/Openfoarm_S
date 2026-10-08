"""V3 review follow-ups: the plan agrees with what runs in every layout (T1), the
status file's step number (T2), and the previous mesh report is set aside before
the meshes change (T3)."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.workflow.dependency_graph import DependencyGraph
from core.workflow.planner import ExecutionPlanner
from openfoam.runner import RunStatus
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_vawt import VAWT_TOOLS, FakeVawtRunner
from tests.unit.vawt.test_vawt_operations import candidates, full, leaves, set_leaf
from vawt import pipeline as pipeline_module
from vawt.config import VawtProjectConfig
from vawt.operations import CACHED, VawtOperation, plan_changes
from vawt.pipeline import MESH_REPORT, SUPERSEDED_MESH_REPORT, VawtPipeline, VawtRunResult
from vawt.status import read_status

Op = VawtOperation
LAYOUTS = {
    "ami": {"include_domain": True, "interface": "AMI"},
    "cell_zone_with_domain": {"include_domain": True, "interface": "CELL_ZONE"},
    "rotor_only": {"include_domain": False, "interface": "CELL_ZONE"},
}
# In a single mesh the zone cells are the domain cell size / 2^n (spec 9.1): a
# small change of rotating_zone.cell_size can leave n, and every dictionary,
# unchanged. The plan is then conservative: what runs is a subset of the plan.
QUANTIZED = {("cell_zone_with_domain", "rotating_zone.cell_size")}
NO_RUNNABLE_CHANGE = {"schema_version", "openfoam_profile", "geometry.source_path"}
# Without a domain these are null (domain, wake), and AMI needs a domain.
NO_RUNNABLE_CHANGE_ROTOR_ONLY = {"domain", "refinement.wake", "rotating_zone.interface"}


def layout_draft(tmp_path: Path, layout: str) -> dict[str, Any]:
    spec = LAYOUTS[layout]
    draft = full(tmp_path, include_domain=spec["include_domain"])
    draft["rotating_zone"]["interface"] = spec["interface"]
    return draft


def runner_for(tmp_path: Path, root: Path) -> Any:
    env = openfoam_env(tmp_path, "v2512", VAWT_TOOLS)

    def run(raw: dict[str, Any], runner: FakeVawtRunner | None = None) -> VawtRunResult:
        return VawtPipeline(runner or FakeVawtRunner(), environment=env,
                            system_probe=lambda _: PLENTY).run_sync(
            root, VawtProjectConfig.model_validate(raw))
    return run


# --- T1 ------------------------------------------------------------------------------

@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_plan_matches_what_runs_in_every_layout(tmp_path: Path, layout: str) -> None:
    run = runner_for(tmp_path, tmp_path / "project")
    base = VawtProjectConfig.model_validate(layout_draft(tmp_path, layout)).model_dump(
        mode="json")
    assert run(base).succeeded
    mismatches, skipped = [], set()
    for path, value in leaves(base):
        tried = False
        for candidate in candidates(value):
            raw = set_leaf(base, path, candidate)
            try:
                new = VawtProjectConfig.model_validate(raw)
            except ValidationError:
                continue
            result = run(raw)
            if not result.succeeded:  # refused by validation or generation; try another
                assert run(base).succeeded
                continue
            tried = True
            executed = set(result.executed) & CACHED
            planned = set(plan_changes(VawtProjectConfig.model_validate(base), new)
                          .operations) & CACHED
            agrees = (executed <= planned if (layout, path) in QUANTIZED
                      else executed == planned)
            if not agrees:
                mismatches.append((path, candidate, sorted(planned), sorted(executed)))
            assert run(base).succeeded
            break
        if not tried:
            skipped.add(path)

    assert mismatches == []
    assert skipped == NO_RUNNABLE_CHANGE | (
        NO_RUNNABLE_CHANGE_ROTOR_ONLY if layout == "rotor_only" else set())


def test_a_zone_cell_size_that_changes_the_level_re_meshes(tmp_path: Path) -> None:
    run = runner_for(tmp_path, tmp_path / "project")
    base = VawtProjectConfig.model_validate(
        layout_draft(tmp_path, "cell_zone_with_domain")).model_dump(mode="json")
    assert run(base).succeeded
    halved = set_leaf(base, "rotating_zone.cell_size",
                      base["rotating_zone"]["cell_size"] / 2)

    result = run(halved)

    planned = set(plan_changes(VawtProjectConfig.model_validate(base),
                               VawtProjectConfig.model_validate(halved)).operations) & CACHED
    assert set(result.executed) & CACHED == planned == {Op.SINGLE_MESH, Op.CHECK_MESH}


@pytest.mark.parametrize("layout,changes", [
    ("rotor_only", {"rotor.flow_axis": "y"}),
    ("rotor_only", {"refinement.interface_level": 2}),
    ("cell_zone_with_domain", {"rotating_zone.location_in_mesh.x": 0.01}),
])
def test_settings_unused_by_the_layout_plan_nothing(tmp_path: Path, layout: str,
                                                    changes: dict[str, Any]) -> None:
    base = VawtProjectConfig.model_validate(layout_draft(tmp_path, layout))
    raw = base.model_dump(mode="json")
    for path, value in changes.items():
        raw = set_leaf(raw, path, value)

    plan = plan_changes(base, VawtProjectConfig.model_validate(raw))

    assert set(plan.operations) & CACHED == set()


class _Toy(StrEnum):
    A = "a"
    B = "b"
    C = "c"


def test_propagation_through_inapplicable_operations_is_opt_in() -> None:
    graph = DependencyGraph(operations=list(_Toy),
                            downstream={_Toy.A: frozenset({_Toy.B}),
                                        _Toy.B: frozenset({_Toy.C}), _Toy.C: frozenset()},
                            direct_consumers={"x": frozenset({_Toy.A})})

    class Changes:
        changed_paths = frozenset({"x"})

    def a_absent(op: _Toy, _: object) -> bool:
        return op is not _Toy.A

    default = ExecutionPlanner(graph, active_when={}, applicable=a_absent)
    strict = ExecutionPlanner(graph, active_when={}, applicable=a_absent,
                              propagate_through_inapplicable=False)

    assert default.plan(Changes(), None).operations == (_Toy.B, _Toy.C)
    assert strict.plan(Changes(), None).operations == ()
    assert strict.plan(Changes(), None).skipped == (_Toy.A,)


# --- T2 ------------------------------------------------------------------------------

def test_status_step_is_where_the_run_was(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[dict[str, Any]] = []
    original = pipeline_module.write_status

    def spy(root: Path, **kwargs: Any) -> None:
        written.append(kwargs)
        original(root, **kwargs)

    monkeypatch.setattr(pipeline_module, "write_status", spy)
    root = tmp_path / "project"
    run = runner_for(tmp_path, root)

    result = run(layout_draft(tmp_path, "ami"),
                 FakeVawtRunner(outcome={"snappyHexMesh": RunStatus.FAILED}))

    assert not result.succeeded
    assert (written[0]["stage"], written[0]["step"]) == ("starting", 0)
    final = read_status(root)
    assert final is not None and final["state"] == "FAILED"
    assert (final["stage"], final["step"], final["total_steps"]) == ("vawt_outer_mesh", 4, 9)


# --- T3 ------------------------------------------------------------------------------

def test_previous_report_is_set_aside_before_the_meshes_change(tmp_path: Path) -> None:
    root = tmp_path / "project"
    run = runner_for(tmp_path, root)
    good = run(layout_draft(tmp_path, "ami"))
    assert good.succeeded
    report = json.loads((root / MESH_REPORT).read_text("utf-8"))
    assert report["run_id"] == good.run_id and len(report["config_sha256"]) == 64

    changed = layout_draft(tmp_path, "ami")
    changed["refinement"]["blade_max_level"] += 1
    failed = run(changed, FakeVawtRunner(wrong_region=["rotor"]))

    assert not failed.succeeded
    assert not (root / MESH_REPORT).exists()
    superseded = json.loads((root / SUPERSEDED_MESH_REPORT).read_text("utf-8"))
    assert superseded["run_id"] == good.run_id


def test_report_stays_when_nothing_runs_or_the_gate_stops(tmp_path: Path) -> None:
    root = tmp_path / "project"
    run = runner_for(tmp_path, root)
    draft = layout_draft(tmp_path, "ami")
    assert run(draft).succeeded

    again = run(draft)  # nothing changed: no OpenFOAM operation runs
    assert again.succeeded and not (root / SUPERSEDED_MESH_REPORT).exists()

    refused = layout_draft(tmp_path, "ami")
    refused["rotating_zone"]["location_in_mesh"] = refused["domain"]["location_in_mesh"]
    assert not run(refused).succeeded
    assert (root / MESH_REPORT).is_file() and not (root / SUPERSEDED_MESH_REPORT).exists()
