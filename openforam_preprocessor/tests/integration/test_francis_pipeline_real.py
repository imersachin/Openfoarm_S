"""G5 end to end on real OpenFOAM v2512: the Francis preset on the G0 R4
passage (synthetic geometry only) through the machine pipeline, then reuse
after changes (spec section 11), and a non-conformal joint.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_francis_pipeline_real.py -v
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from machines.config import MachineProjectConfig
from machines.operations import ASSEMBLE, CHECK_MESH, MACHINE_EXECUTABLES, mesh_operation
from machines.pipeline import MESH_REPORT, MachinePipeline
from machines.presets.francis import francis_draft, with_types
from tests.fixtures.machines.configs import stl
from tests.fixtures.machines.g0.common import make_geometry
from visualization.foam_reader import read_boundary

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in MACHINE_EXECUTABLES),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
PARTS = ("casing", "guide", "draft")
MESHES = {*(mesh_operation(f"domain_{p}") for p in PARTS), mesh_operation("zone_runner")}
TYPES = {"inlet": "INLET", "outlet": "OUTLET", "casing_wall": "WALL", "guide_wall": "WALL",
         "vanes": "WALL", "runner_wall": "WALL", "draft_wall": "WALL",
         "runner_blades": "ROTATING_WALL"}


def francis_config(directory: Path) -> MachineProjectConfig:
    """The preset's draft on G0 R4, D 0.6 confirmed, types chosen as G0 R4."""
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.francis(directory)

    def surface(name: str) -> dict[str, Any]:
        return {"format": "NAMED_REGIONS", "files": [stl(directory / f"{name}.stl")]}

    draft = francis_draft({
        "project_name": "Francis", "axis": "z", "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "runner": surface("runner"), "parts": [{"name": p, **surface(p)} for p in PARTS]},
        0.6)
    assert [(j.first, j.second) for j in draft.joints] == [
        ("casing_out", "guide_in"), ("guide_out", "runner_in"), ("draft_in", "runner_out")]
    return MachineProjectConfig.model_validate(with_types(draft.data, TYPES))


def edited(config: MachineProjectConfig, change: Any) -> MachineProjectConfig:
    data = config.model_dump(mode="json")
    change(data)
    return MachineProjectConfig.model_validate(data)


def assert_valid(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads((root / MESH_REPORT).read_text("utf-8"))
    assert report["assessment"]["mesh_validity"] == "VALID"
    assert report["regions"] == {"expected": 4, "found": 4}
    weights = report["ami_weights"]
    assert {frozenset((w["source"], w["target"])) for w in weights} == {
        frozenset(("casing_out", "guide_in")), frozenset(("guide_out", "runner_in")),
        frozenset(("draft_in", "runner_out"))}
    for w in weights:
        assert 0.85 <= w["minimum"] <= w["maximum"] <= 1.5, w
    return report


def test_francis_preset_end_to_end_and_reuse(tmp_path: Path) -> None:
    config = francis_config(tmp_path / "geometry")
    root = tmp_path / "project"
    pipeline = MachinePipeline()

    first = pipeline.run_sync(root, config)

    assert first.succeeded, [(i.code, i.message) for i in first.issues if i.is_stopping]
    assert MESHES | {ASSEMBLE, CHECK_MESH} <= set(first.executed)
    assert [i.code for i in first.issues if i.severity.value == "WARNING"].count(
        "WALL_IN_ROTATING_ZONE") == 1
    report = assert_valid(root)
    print("G5 conformal (synthetic geometry only):", report["metrics"]["cells"],
          report["ami_weights"])
    boundary = {p.name: (p.patch_type, p.n_faces)
                for p in read_boundary(root / "cases/merged/constant/polyMesh/boundary")}
    for name in ("casing_out", "guide_in", "guide_out", "runner_in", "runner_out", "draft_in"):
        assert boundary[name][0] == "cyclicAMI" and boundary[name][1] > 0, name
    assert boundary["runner_blades"][0] == "wall" and boundary["inlet"][0] == "patch"
    assert not any(name.endswith("_src") for name in boundary)

    # Nothing changed: every OpenFOAM operation is reused.
    again = pipeline.run_sync(root, config)
    assert again.succeeded and set(again.reused) == MESHES | {ASSEMBLE, CHECK_MESH}

    # The draft tube only: its mesh, the assembly and the checks re-run.
    def refine_draft_wall(data: dict[str, Any]) -> None:
        data["domain"]["parts"][2]["refinement"] = {"draft_wall": 1}

    changed = pipeline.run_sync(root, edited(config, refine_draft_wall))
    assert changed.succeeded
    draft = mesh_operation("domain_draft")
    assert set(changed.reused) == MESHES - {draft}
    assert {draft, ASSEMBLE, CHECK_MESH} <= set(changed.executed)
    assert_valid(root)


def test_non_conformal_joints(tmp_path: Path) -> None:
    # As G0 R4's guide_nc: the guide's cells (0.0225 m) do not match its
    # neighbours' (0.025 m), so neither joint of the guide is conformal.
    config = francis_config(tmp_path / "geometry")

    def finer_guide(data: dict[str, Any]) -> None:
        data["domain"]["parts"][1]["cell_size"] = 0.0225

    root = tmp_path / "project"
    result = MachinePipeline().run_sync(root, edited(config, finer_guide))

    assert result.succeeded, [(i.code, i.message) for i in result.issues if i.is_stopping]
    report = assert_valid(root)
    print("G5 non-conformal (synthetic geometry only):", report["metrics"]["cells"],
          report["ami_weights"])
