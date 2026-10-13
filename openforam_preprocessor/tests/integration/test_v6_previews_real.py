"""V6 on real OpenFOAM v2512 meshes: the HAWT and Francis test meshes through
the display data (docs/rotating_machinery.md section 26).

Builds each mesh with the machine pipeline, then the setup and mesh scenes:
every patch of the merged mesh is shown with its configured type, both cuts
cross the mesh, region sets are left out, and a second view loads the stored
file without reading the mesh. A copy checked with tightened quality limits
gives real problem faces, capped at the marker limit. Timings are printed
(-s) for the spec; the assertions on time are generous.

    pytest -m openfoam tests/integration/test_v6_previews_real.py -v -s
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from machines import previews
from machines.config import MachineProjectConfig
from machines.operations import MACHINE_EXECUTABLES
from machines.pipeline import MachinePipeline
from machines.previews import (
    INTERFACE,
    PreviewLimits,
    build_setup_scene,
    cut_planes,
    mesh_preview,
    patch_kinds,
)
from tests.integration.test_francis_pipeline_real import francis_config
from tests.integration.test_hawt_pipeline_real import hawt_config
from visualization.foam_reader import read_boundary

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in MACHINE_EXECUTABLES),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
MERGED = "cases/merged/constant/polyMesh"
TIGHT = ("maxNonOrtho 25; maxBoundarySkewness 1; maxInternalSkewness 0.8; maxConcave 80; "
         "minVol 1e-13; minTetQuality 1e-15; minArea -1; minTwist 0.05; "
         "minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.01; minTriangleTwist -1;")


def _timed(action: Callable[[], object]) -> tuple[float, object]:
    start = time.perf_counter()
    result = action()
    return time.perf_counter() - start, result


@pytest.mark.parametrize("name, factory, regions", [
    ("HAWT", hawt_config, 2), ("Francis", francis_config, 4)])
def test_real_mesh_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
                           factory: Callable[[Path], MachineProjectConfig],
                           regions: int) -> None:
    config = factory(tmp_path / "geometry")
    root = tmp_path / "project"
    result = MachinePipeline().run_sync(root, config)
    assert result.succeeded, [(i.code, i.message) for i in result.issues if i.is_stopping]
    poly = root / MERGED
    kinds, planes = patch_kinds(config), cut_planes(config)

    setup_s, setup = _timed(lambda: build_setup_scene(config))
    first_s, first = _timed(lambda: mesh_preview(root, poly, kinds, planes))
    reads: list[Path] = []
    original = previews.read_points
    monkeypatch.setattr(previews, "read_points", lambda p: reads.append(p) or original(p))
    again_s, again = _timed(lambda: mesh_preview(root, poly, kinds, planes))

    assert first.built and not again.built and not again.hashed and reads == []
    scene = first.scene
    boundary = {p.name: (p.patch_type, p.n_faces) for p in read_boundary(poly / "boundary")}
    shown = {s.name.split(" (")[0]: s for s in scene.surfaces}
    assert set(shown) == {n for n, (_, count) in boundary.items() if count}
    for patch, surface in shown.items():
        foam_type, count = boundary[patch]
        assert surface.total == count
        expected = INTERFACE if foam_type == "cyclicAMI" else kinds.get(patch)
        assert surface.kind == expected, patch
    assert len(scene.lines) == 2 and all(line.total > 100 for line in scene.lines)
    assert scene.markers == ()  # a valid mesh: only region sets, which are not problems
    assert sorted(p.name for p in (poly / "sets").iterdir()) == [
        f"region{i}" for i in range(regions)]

    # Tightened limits on a copy give real problem faces (tens of thousands).
    copy = tmp_path / "tight"
    shutil.copytree(root / "cases" / "merged" / "system", copy / "system")
    shutil.copytree(poly, copy / "constant" / "polyMesh", ignore=shutil.ignore_patterns("sets"))
    (copy / "system" / "meshQualityDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object meshQualityDict; }\n"
        + TIGHT + "\nnSmoothScale 4; errorReduction 0.75;\n")
    subprocess.run(["checkMesh", "-case", str(copy), "-meshQuality"], check=True,
                   capture_output=True)
    tight_s, tight = _timed(lambda: mesh_preview(copy, copy / "constant" / "polyMesh",
                                                 kinds, planes, PreviewLimits()))
    (problems,) = tight.scene.markers
    assert problems.name == "checkMesh: meshQualityFaces"
    assert problems.total > PreviewLimits().max_markers == problems.shown
    assert any("checkMesh problem locations (display limit 5,000)" in n
               for n in tight.scene.notes)

    print(f"\n{name}: boundary faces "
          f"{sum(c for _, c in boundary.values()):,}; setup scene {setup_s:.2f} s "
          f"({sum(s.total for s in setup.surfaces):,} triangles); mesh scene built "
          f"{first_s:.2f} s, loaded {again_s * 1000:.0f} ms; with {problems.total:,} "
          f"problem faces {tight_s:.2f} s; cut edges "
          f"{[line.total for line in scene.lines]}")
    assert first_s < 30 and again_s < 2
