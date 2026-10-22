"""M2a on real OpenFOAM v2512: binary imported meshes read through the surface
the tools extract (machines.msh_import.surface).

1. The M1 test meshes (synthetic): the areas, zone radius and tip radius from
   the surface, and the mesh view of an imported project with its cuts.
2. Opt-in, large real meshes: with MACHINES_E0_DIR pointing at a folder of
   .msh files (the owner's Francis turbine, E0; never committed), each file
   is read and converted for its box as the app does, and its interface
   radii are measured from the surface. Skipped otherwise.

Run explicitly with:

    pytest -m openfoam tests/integration/test_m2a_real.py -v -s
    MACHINES_E0_DIR=/root/meshes pytest -m openfoam tests/integration/test_m2a_real.py -v -s
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from machines.msh_import import surface
from machines.msh_import.cases import IMPORTED_CASE, SCRATCH_CASE
from machines.service import MachineService
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from vawt.service import PreviewView
from visualization.foam_reader import read_boundary

TOOLS = ("gmshToFoam", "fluent3DMeshToFoam", "transformPoints", "topoSet",
         "surfaceMeshExtract", "createPatch", "foamDictionary", "checkMesh", "postProcess")
E0_DIR = os.environ.get("MACHINES_E0_DIR")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


def radius(folder: Path, patch: str) -> float:
    """The largest distance from the z axis of a patch's points, on the surface."""
    found = surface.load(folder)
    assert found is not None
    points = found.points[found.labels(found.faces(patch))]
    return float(np.hypot(points[:, 0], points[:, 1]).max())


@pytest.mark.parametrize("name", ["rotating_41.msh", "fluent_rotating.msh"])
def test_the_surface_measures_the_rotating_mesh(tmp_path: Path, name: str) -> None:
    """The rotating box x -1..3, y and z -1..1, a cylinder zone r 0.5 on x."""
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file(name, (M1 / name).read_bytes())
    data = configs.rotating(path)
    service.save(data)
    config = service.saved_config()
    assert config is not None
    result = service.pipeline.run_sync(service.root, config)
    assert result.succeeded, (result.message, [i.message for i in result.issues])

    imported = service.root / "cases" / IMPORTED_CASE
    areas = surface.config_areas(imported, config)
    assert areas["inlet"] == pytest.approx(4.0) and areas["outlet"] == pytest.approx(4.0)
    zone = surface.config_zone_radius(imported, config.imported_mesh.zones[0])  # type: ignore[union-attr]
    assert zone == pytest.approx(0.5, abs=1e-6)  # the cylinder's points lie on r = 0.5
    tip = service.measured_tip_radius(data, "rotor_zone")
    assert tip is not None and 0.3 < tip.radius < 0.5 and tip.source == "from rotor patch rotor"

    view = service.preview(PreviewView.MESH)
    assert view.scene is not None and not view.issues
    assert {s.name.split(" ")[0] for s in view.scene.surfaces} >= {
        "inlet", "outlet", "rotor", "ami_stat", "ami_rot"}
    assert len(view.scene.lines) == 2 and all(line.total > 0 for line in view.scene.lines)


# --- opt-in: large real meshes (MACHINES_E0_DIR) ---------------------------------------------

# file: (cells declared, patch -> radius about z in m, from E0's ASCII reads)
LARGE = {
    "GV-03-91-AG5-HighRe.msh": (281_600, {"FaceGroup13": 0.3200, "FaceGroup27": 0.3822}),
    "RU-AG5-HighRe.msh": (718_720, {"r1_inflow_1": 0.3200, "r1_outflow_1": 0.1752}),
    "DT-HighRe-uns.msh": (890_880, {"DT_INLET": 0.1752}),
    "SC-SV-HH-HighRe.msh": (3_427_708, {"SV-Outlet": 0.3822}),
}


@pytest.mark.skipif(not E0_DIR, reason="MACHINES_E0_DIR is not set (large real meshes)")
@pytest.mark.parametrize("name", sorted(LARGE))
def test_a_large_real_mesh_is_converted_binary_and_measured_from_its_surface(
        tmp_path: Path, name: str) -> None:
    path = Path(str(E0_DIR)) / name
    if not path.is_file():
        pytest.skip(f"{name} is not in MACHINES_E0_DIR")
    cells, radii = LARGE[name]
    service = MachineService(tmp_path / "project")
    start = time.perf_counter()
    inspection = service.inspect_mesh(path)
    inspected = time.perf_counter() - start
    assert inspection.info.declared_cells == cells
    start = time.perf_counter()
    result = service.convert_for_box(path)
    converted = time.perf_counter() - start
    assert result.issues == () and result.box is not None

    scratch = service.root / "cases" / SCRATCH_CASE
    head = (scratch / "constant/polyMesh/points").read_bytes()[:1000]
    assert b"binary" in head  # never written as text
    found = surface.load(scratch)
    assert found is not None
    for patch, expected in radii.items():
        assert radius(scratch, patch) == pytest.approx(expected, abs=5e-4), patch
    # Every name the detector gives is the converter's, unnamed zones as FaceGroup<id>.
    names = {p.name for p in read_boundary(scratch / "constant/polyMesh/boundary")}
    assert set(inspection.info.patches) == names
    unnamed = [i for i in inspection.issues if i.code == "MSH_UNNAMED_ZONES"]
    assert len(unnamed) == (1 if any(n.startswith("FaceGroup") for n in names) else 0)
    print(f"\n{name}: inspect {inspected:.1f} s, convert + surface {converted:.1f} s, "
          f"{len(found.offsets) - 1} surface faces, zones {list(found.zones)}")


# file: the classes checkMesh's failures fall in (E0 evidence 1; all WARNING)
CLASSES = {
    "GV-03-91-AG5-HighRe.msh": {"skewness", "non-orthogonality", "tet quality"},
    "RU-AG5-HighRe.msh": set(),
    "DT-HighRe-uns.msh": set(),
    "SC-SV-HH-HighRe.msh": {"skewness", "non-orthogonality", "tet quality",
                            "interpolation weight", "cell determinant"},
}


@pytest.mark.skipif(not E0_DIR, reason="MACHINES_E0_DIR is not set (large real meshes)")
@pytest.mark.parametrize("name", sorted(CLASSES))
def test_a_large_real_mesh_has_only_quality_warnings_and_their_cells_per_zone(
        tmp_path: Path, name: str) -> None:
    """The import's own checkMesh command on the converted file, classified in
    two levels; the per-zone cells from the fields it writes."""
    from machines.msh_import import quality
    from machines.msh_import.cases import MERGED_CASE

    path = Path(str(E0_DIR)) / name
    if not path.is_file():
        pytest.skip(f"{name} is not in MACHINES_E0_DIR")
    service = MachineService(tmp_path / "project")
    assert service.convert_for_box(path).issues == ()
    scratch = service.root / "cases" / SCRATCH_CASE
    shutil.copy(Path(__file__).parents[1] / "fixtures/msh/e0/common/system/meshQualityDict",
                scratch / "system")
    data = configs.rotating(path)
    service.save(data)
    config = service.saved_config()
    assert config is not None
    from machines.msh_import.cases import import_cases

    argv = import_cases(config).check_steps(False)[0].argv
    assert MERGED_CASE not in argv
    start = time.perf_counter()
    run = subprocess.run(list(argv), cwd=scratch, capture_output=True, text=True, check=False)
    spent = time.perf_counter() - start
    findings = quality.classify(run.stdout)

    assert {f.level for f in findings} <= {quality.WARNING}
    assert {f.kind for f in findings} == CLASSES[name]
    values = dict(re.findall(r"^\s*(\w+)\s+([^;\s]+);", (scratch / "system/meshQualityDict")
                             .read_text("utf-8"), re.MULTILINE))
    zones = quality.zone_quality(scratch, set(quality.FIELDS), quality.limits_of(values))
    print(f"\n{name}: checkMesh {spent:.1f} s; " + "; ".join(
        f"{kind}: " + ", ".join(f"{r.zone} {r.bad}/{r.cells} worst {r.worst:.3g}"
                                for r in rows if r.bad) for kind, rows in zones.items()))


def test_the_revolution_check_runs_in_the_background_on_real_openfoam(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M2a: 8 x 45 deg in the background under the heavy-step lock; the report
    is labelled with its steps."""
    from machines.heavy_lock import HEAVY_LOCK_ENV
    from machines.msh_import.revolution import read_report

    monkeypatch.setenv(HEAVY_LOCK_ENV, str(tmp_path / "heavy.lock"))
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file("rotating_41.msh", (M1 / "rotating_41.msh").read_bytes())
    service.save(configs.rotating(path))
    config = service.saved_config()
    assert config is not None and service.pipeline.run_sync(service.root, config).succeeded
    started = service.start_revolution(8)
    assert started.started, started.issues
    active = service.revolutions.latest(service.root)
    assert active is not None and active.finished.wait(120)
    report = read_report(service.root)
    assert report is not None and report["when"] == "over one revolution (8 x 45 deg)"
    assert report["steps"] == 8 and not (tmp_path / "heavy.lock").exists()


# file: faces at or below 0 in (tet quality, determinant); None: no second pass
# needed (the table has no face in error). Owner, after M2a: a determinant at or
# below 0 is BLOCKING; tet quality at or below 0 a WARNING (the solver test below).
AT_OR_BELOW_ZERO = {
    "GV-03-91-AG5-HighRe.msh": (122, 0),
    "RU-AG5-HighRe.msh": None,
    "DT-HighRe-uns.msh": None,
    "SC-SV-HH-HighRe.msh": (575, 0),
}


@pytest.mark.skipif(not E0_DIR, reason="MACHINES_E0_DIR is not set (large real meshes)")
@pytest.mark.parametrize("name", sorted(AT_OR_BELOW_ZERO))
def test_tet_quality_and_determinant_at_or_below_zero_on_a_large_real_mesh(
        tmp_path: Path, name: str) -> None:
    """The import's checkMesh, then its second pass (quality.ZERO_QUALITY_DICT)
    in a case linking the mesh, as the pipeline runs them."""
    from machines.msh_import import quality
    from machines.msh_import.cases import import_cases
    from machines.msh_import.pipeline import _converter_files

    path = Path(str(E0_DIR)) / name
    if not path.is_file():
        pytest.skip(f"{name} is not in MACHINES_E0_DIR")
    service = MachineService(tmp_path / "project")
    assert service.convert_for_box(path).issues == ()
    scratch = service.root / "cases" / SCRATCH_CASE
    shutil.copy(Path(__file__).parents[1] / "fixtures/msh/e0/common/system/meshQualityDict",
                scratch / "system")
    service.save(configs.rotating(path))
    config = service.saved_config()
    assert config is not None
    argv = import_cases(config).check_steps(False)[0].argv
    log = subprocess.run(list(argv), cwd=scratch, capture_output=True, text=True,
                         check=False).stdout
    expected = AT_OR_BELOW_ZERO[name]
    assert quality.needs_zero_pass(log) is (expected is not None)
    if expected is None:
        return
    zero = quality.prepare_zero_case(scratch, _converter_files())
    start = time.perf_counter()
    zero_log = subprocess.run(["checkMesh", "-case", ".", "-meshQuality"], cwd=zero,
                              capture_output=True, text=True, check=False).stdout
    spent = time.perf_counter() - start
    counts = quality.zero_counts(zero_log)
    assert counts == {"tet quality": expected[0], "cell determinant": expected[1]}
    findings = quality.classify(log, zero_log)
    assert [(f.level, f.count) for f in findings if f.kind == "tet quality at or below 0"] \
        == [(quality.WARNING, expected[0])]
    assert {f.level for f in findings} == {quality.WARNING}  # GV and SC import
    print(f"\n{name}: second pass {spent:.1f} s, {counts}")

