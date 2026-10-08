"""Additive engine extensions used by the VAWT workflow (integration points 1, 2, 3, 5)."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

from core.issues import IssueStage
from core.workflow.dependency_graph import DependencyGraph, PipelineOperation
from core.workflow.planner import INITIAL_BUILD, ExecutionPlanner
from mesh.estimator import CellEstimate, ResourceEstimator, ResourceStatus, SystemResources
from openfoam.commands import create_patch_step, merge_meshes_step, topo_set_step
from openfoam.runner import OpenFOAMRunner


class Op(StrEnum):
    A = "a"
    B = "b"
    C = "c"
    D = "d"


@dataclass(frozen=True)
class Config:
    d_enabled: bool


def graph() -> DependencyGraph[Op]:
    return DependencyGraph(
        operations=list(Op),
        downstream={Op.A: frozenset({Op.B}), Op.B: frozenset({Op.D}), Op.C: frozenset({Op.D}),
                    Op.D: frozenset()},
        direct_consumers={"x": frozenset({Op.A}), "y": frozenset({Op.C}),
                          "z.level": frozenset({Op.C}), "quiet": frozenset()},
        requires_fresh={Op.C: frozenset({Op.A})},
    )


# --- 1. graph and planner with injected tables ------------------------------------------

def test_injected_graph_follows_its_own_tables() -> None:
    g = graph()

    assert g.operations_for(frozenset({"x"})) == {Op.A, Op.B, Op.D}
    assert g.operations_for(frozenset({"y"})) == {Op.C, Op.D, Op.A, Op.B}  # requires fresh A
    assert g.operations_for(frozenset({"quiet"})) == frozenset()
    assert g.unmapped({"nope"}) == {"nope"}
    assert g.operations_for(frozenset({"nope"})) == set(Op)  # unmapped: everything


def test_injected_planner_uses_active_when_and_applicability() -> None:
    planner: ExecutionPlanner[Op, Config] = ExecutionPlanner(
        graph(),
        active_when={"z.level": lambda c: c.d_enabled},
        applicable=lambda op, c: op is not Op.D or c.d_enabled,
    )

    @dataclass(frozen=True)
    class Changes:
        changed_paths: frozenset[str]

    off = planner.plan(Changes(frozenset({"x", "z.level"})), Config(d_enabled=False))
    assert off.operations == (Op.A, Op.B)  # in the graph's order
    assert off.skipped == (Op.D,)
    assert off.inactive_paths == {"z.level"}

    full = planner.plan_full(Config(d_enabled=True))
    assert full.operations == tuple(Op)
    assert all(paths == {INITIAL_BUILD} for paths in full.reasons.values())


def test_default_graph_and_planner_are_the_generic_workflow() -> None:
    assert DependencyGraph().operations == tuple(PipelineOperation)
    assert ExecutionPlanner().graph.operations == tuple(PipelineOperation)


def test_custom_graph_needs_its_tables() -> None:
    with pytest.raises(ValueError):
        DependencyGraph(operations=list(Op), downstream=None,  # type: ignore[call-overload]
                        direct_consumers=None)


# --- 2. and 3. stages and command builders ---------------------------------------------------

def test_assembly_command_builders() -> None:
    master, add = Path("/p/cases/merged"), Path("/p/cases/rotor")

    assert topo_set_step(add).argv == ("topoSet", "-case", str(add))
    assert topo_set_step(add).stage is IssueStage.ZONE_CREATION
    merge = merge_meshes_step(master, add)
    assert merge.argv == ("mergeMeshes", "-overwrite", str(master), str(add))
    assert merge.stage is IssueStage.MERGE_MESHES
    assert create_patch_step(master).argv == ("createPatch", "-case", str(master), "-overwrite")
    assert create_patch_step(master).stage is IssueStage.PATCH_CREATION
    assert topo_set_step(add, "rotor_topoSet.log").log_name == "rotor_topoSet.log"


def test_runner_writes_logs_to_logs_dir_and_runs_in_the_case(tmp_path: Path) -> None:
    case, logs = tmp_path / "cases" / "rotor", tmp_path / "logs" / "rotor"
    case.mkdir(parents=True)
    script = "import os; print(os.getcwd())"

    result = asyncio.run(OpenFOAMRunner().run(
        (sys.executable, "-c", script), case_root=case, log_name="probe.log", logs_dir=logs,
    ))

    assert result.succeeded
    assert Path(result.log_path) == logs / "probe.log"
    assert (logs / "probe.log").read_text("utf-8").strip() == str(case)
    assert not (case / "logs").exists()


def test_runner_default_logs_dir_is_unchanged(tmp_path: Path) -> None:
    result = asyncio.run(OpenFOAMRunner().run(
        (sys.executable, "-c", "print(1)"), case_root=tmp_path, log_name="probe.log",
    ))

    assert Path(result.log_path) == tmp_path / "logs" / "probe.log"


# --- 5. assessment from a cell estimate ------------------------------------------------

SYSTEM = SystemResources(available_ram_bytes=10 * 10**9, available_disk_bytes=10**13,
                         cpu_count=8)


def test_assess_cells_uses_peak_cells_for_ram_and_all_cells_for_disk() -> None:
    estimator = ResourceEstimator()
    cells = CellEstimate(background=3_500_000, surface=1_000_000, feature=0, layers=0)

    together = estimator.assess_cells(cells, SYSTEM, max_global_cells=10**8)
    in_turn = estimator.assess_cells(cells, SYSTEM, max_global_cells=10**8,
                                     peak_cells=3_500_000)

    assert together.ram_bytes == 4_500_000 * 2000
    assert in_turn.ram_bytes == 3_500_000 * 2000
    assert together.disk_bytes == in_turn.disk_bytes == 4_500_000 * 1500
    assert together.status is ResourceStatus.HIGH_RESOURCE_RISK  # 9 GB of 10 GB (> 0.8)
    assert in_turn.status is ResourceStatus.WARNING  # 7 GB of 10 GB (> 0.5)


def test_assess_cells_reports_max_global_cells() -> None:
    cells = CellEstimate(background=1000, surface=0, feature=0, layers=0)

    found = ResourceEstimator().assess_cells(cells, SYSTEM, max_global_cells=500)

    assert "MAX_GLOBAL_CELLS_EXCEEDED" in {i.code for i in found.issues}
