"""G4 end to end on real OpenFOAM v2512: the HAWT preset on the G0 HAWT rotor
through the machine pipeline, then reuse after changes (spec section 11).

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_hawt_pipeline_real.py -v
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from geometry.importer import import_stl
from machines.config import MachineProjectConfig
from machines.operations import ASSEMBLE, CHECK_MESH, MACHINE_EXECUTABLES, mesh_operation
from machines.pipeline import MESH_REPORT, MachinePipeline
from machines.presets.hawt import hawt_draft, hawt_rotor
from machines.project_store import read_last_meshed
from tests.fixtures.machines.configs import stl
from tests.fixtures.machines.g0.common import make_geometry
from vawt.config import Axis
from visualization.foam_reader import read_boundary

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in MACHINE_EXECUTABLES),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
DOMAIN, ZONE = mesh_operation("domain"), mesh_operation("zone_rotating")


def hawt_config(directory: Path) -> MachineProjectConfig:
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.hawt(directory)
    mesh = import_stl(directory / "hawt_rotor.stl").mesh
    assert mesh is not None
    rotor = hawt_rotor(mesh, Axis.X)
    return hawt_draft({"project_name": "HAWT", "source": stl(directory / "hawt_rotor.stl")},
                      rotor)


def edited(config: MachineProjectConfig, **changes: object) -> MachineProjectConfig:
    data = config.model_dump(mode="json")
    for path, value in changes.items():
        *parents, leaf = path.split("__")
        target: object = data
        for key in parents:
            target = target[int(key)] if isinstance(target, list) else target[key]  # type: ignore[index]
        target[int(leaf) if isinstance(target, list) else leaf] = value  # type: ignore[index]
    return MachineProjectConfig.model_validate(data)


def test_hawt_preset_end_to_end_and_reuse(tmp_path: Path) -> None:
    config = hawt_config(tmp_path / "geometry")
    root = tmp_path / "project"
    pipeline = MachinePipeline()

    first = pipeline.run_sync(root, config)

    assert first.succeeded, [(i.code, i.message) for i in first.issues if i.is_stopping]
    assert {DOMAIN, ZONE, ASSEMBLE, CHECK_MESH} <= set(first.executed)
    report = json.loads((root / MESH_REPORT).read_text("utf-8"))
    assert report["assessment"]["mesh_validity"] == "VALID"
    assert report["regions"] == {"expected": 2, "found": 2}
    (weights,) = report["ami_weights"]
    assert 0.85 <= weights["minimum"] <= weights["maximum"] <= 1.5
    boundary = {p.name: (p.patch_type, p.n_faces)
                for p in read_boundary(root / "cases/merged/constant/polyMesh/boundary")}
    assert boundary["rotor"][0] == "wall" and boundary["rotor"][1] > 0  # set by the assembly
    assert boundary["rotating_outer_stat"][0] == "cyclicAMI"
    # The domain and zone meshes themselves are untyped (H3).
    zone = {p.name: p.patch_type
            for p in read_boundary(root / "cases/zone_rotating/constant/polyMesh/boundary")}
    assert zone["rotor"] == "patch"
    last = read_last_meshed(root)
    assert last is not None and last.run_id == first.run_id

    # Nothing changed: every OpenFOAM operation is reused.
    again = pipeline.run_sync(root, config)
    assert again.succeeded and set(again.reused) == {DOMAIN, ZONE, ASSEMBLE, CHECK_MESH}

    # Patch type only: the meshes are reused; assembly and checks re-run (H3).
    walls = edited(config, patches__3__type="WALL")  # side: SLIP -> WALL
    typed = pipeline.run_sync(root, walls)
    assert typed.succeeded
    assert set(typed.reused) == {DOMAIN, ZONE}
    assert {ASSEMBLE, CHECK_MESH} <= set(typed.executed)
    boundary = {p.name: p.patch_type
                for p in read_boundary(root / "cases/merged/constant/polyMesh/boundary")}
    assert boundary["side"] == "wall"

    # One body's refinement: its zone mesh, the assembly and checks; the domain is reused.
    finer = edited(walls, bodies__0__refinement={"min_level": 1, "max_level": 3})
    refined = pipeline.run_sync(root, finer)
    assert refined.succeeded
    assert DOMAIN in refined.reused and ZONE in refined.executed
    assert {ASSEMBLE, CHECK_MESH} <= set(refined.executed)
