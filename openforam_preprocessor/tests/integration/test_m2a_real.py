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


# file: (inlet, outlet, flow rate m^3/s or None: 2 m/s through the inlet, the zone
# of the worst non-orthogonality). GV: the
# outer ring face in (r 0.3822 m), the inner one out (r 0.3200 m, notes 13.2); SC:
# its named inlet and outlet. Every other patch is a wall (GV's periodic sides too:
# a test of the solver on these cells, not of the flow).
SOLVER_CASES = {
    "GV-03-91-AG5-HighRe.msh": ("FaceGroup27", "FaceGroup13", None,
                                "fluid_GV_flux_1_GV1_skin"),
    "SC-SV-HH-HighRe.msh": ("SC-Inlet", "SV-Outlet", 0.3, None),
}
SOLVER_STEPS = 10


@pytest.mark.skipif(not E0_DIR, reason="MACHINES_E0_DIR is not set (large real meshes)")
@pytest.mark.parametrize("name", sorted(SOLVER_CASES))
def test_ten_solver_steps_run_on_a_real_mesh_with_tet_faces_below_zero(
        tmp_path: Path, name: str) -> None:
    """Owner, after M2a: GV (122 tet faces at or below 0) and SC (575) run 10
    pimpleFoam steps with the usual case setup (case_setup's fields and
    dictionaries: k-omega SST, water) on the static mesh (no dynamicMeshDict),
    with a fixed step: so tet quality at or below 0 is a WARNING. Both are above
    70 deg of non-orthogonality, read from the import's own checkMesh as the
    pipeline reads it, so the case has the correctors (without them GV
    diverges: notes 13.8). The zone of the worst value is checked for GV."""
    from case_setup.dictionaries import dictionaries
    from case_setup.fields import fields
    from case_setup.physics import (
        WATER,
        PhysicsConfig,
        TurbulenceConfig,
        turbulence_k,
        turbulence_omega,
    )
    from case_setup.setup import (
        CaseSetup,
        Inlet,
        PatchKind,
        Rotation,
        TimeControls,
        non_orthogonality,
    )
    from machines.msh_import import quality
    from machines.msh_import.cases import import_cases

    path = Path(str(E0_DIR)) / name
    if not path.is_file():
        pytest.skip(f"{name} is not in MACHINES_E0_DIR")
    service = MachineService(tmp_path / "project")
    assert service.convert_for_box(path).issues == ()
    case = service.root / "cases" / SCRATCH_CASE
    inlet, outlet, flow, worst_zone = SOLVER_CASES[name]
    names = [p.name for p in read_boundary(case / "constant/polyMesh/boundary")]
    assert {inlet, outlet} <= set(names)
    area = surface.patch_areas(case, [inlet])[inlet]
    flow = flow if flow is not None else 2.0 * area
    speed, length, step = flow / area, 0.01, 1e-5
    k = turbulence_k(speed, 5.0)
    omega = turbulence_omega(k, length)
    kinds = {inlet: PatchKind.INLET, outlet: PatchKind.OUTLET}
    # The import's checkMesh and per-zone fields, as the pipeline reads them.
    shutil.copy(Path(__file__).parents[1] / "fixtures/msh/e0/common/system/meshQualityDict",
                case / "system")
    service.save(configs.rotating(path))
    config = service.saved_config()
    assert config is not None
    check = subprocess.run(list(import_cases(config).check_steps(False)[0].argv), cwd=case,
                           capture_output=True, text=True, check=False).stdout
    values = dict(re.findall(r"^\s*(\w+)\s+([^;\s]+);", (case / "system/meshQualityDict")
                             .read_text("utf-8"), re.MULTILINE))
    rows = quality.zone_quality(case, set(quality.FIELDS), quality.limits_of(values))
    quality.remove_outputs(case)
    measured = non_orthogonality(check, {k: [r.as_dict() for r in v] for k, v in rows.items()})
    assert measured is not None and measured.corrected
    assert worst_zone is None or measured.zone == worst_zone
    for patch in names:  # the final types, as the import's assembly sets them
        subprocess.run(["foamDictionary", "constant/polyMesh/boundary", "-entry",
                        f"entry0/{patch}/type", "-set", "patch" if patch in kinds else "wall"],
                       cwd=case, capture_output=True, check=True)
    setup = CaseSetup(
        patches=tuple((n, kinds.get(n, PatchKind.WALL)) for n in names),
        rotation=Rotation("none", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0, None),
        inlets={inlet: Inlet(None, flow, speed, k, omega)}, outlets={outlet: 0.0},
        physics=PhysicsConfig(fluid=WATER, turbulence=TurbulenceConfig(length_scale=length)),
        time=TimeControls(step, step, SOLVER_STEPS * step, SOLVER_STEPS * step, 5.0, None,
                          None, None),
        internal_k=k, internal_omega=omega, non_orthogonality=measured)
    files = {**fields(setup), **dictionaries(setup)}
    files.pop("constant/dynamicMeshDict")  # a static mesh
    assert "nNonOrthogonalCorrectors 2;" in files["system/fvSolution"]
    files["system/controlDict"] = files["system/controlDict"].replace(
        "adjustTimeStep  yes;", "adjustTimeStep  no;")  # exactly SOLVER_STEPS steps
    for relative, body in files.items():
        (case / relative).parent.mkdir(parents=True, exist_ok=True)
        (case / relative).write_text(body, encoding="utf-8")
    start = time.perf_counter()
    run = subprocess.run(["pimpleFoam", "-case", "."], cwd=case, capture_output=True,
                         text=True, check=False)
    spent = time.perf_counter() - start
    log = run.stdout + run.stderr
    (tmp_path / "pimpleFoam.log").write_text(log, encoding="utf-8")
    steps = len(re.findall(r"^Time = ", log, re.MULTILINE))
    courant = [float(c) for c in re.findall(r"Courant Number mean: \S+ max: (\S+)", log)]
    continuity = re.findall(r"continuity errors : sum local = (\S+)", log)
    print(f"\n{name}: {measured.reason}; {steps} steps in {spent:.1f} s; Q {flow:.4g} "
          f"m3/s, inlet "
          f"{speed:.3g} m/s; max Courant {max(courant or [0.0]):.3g}; last continuity "
          f"(local) {continuity[-1] if continuity else None}; exit {run.returncode}")
    assert run.returncode == 0, log[-3000:]
    assert "FOAM FATAL" not in log and not re.search(r"\bnan\b", log, re.IGNORECASE)
    assert steps == SOLVER_STEPS and re.search(r"^End\s*$", log, re.MULTILINE)
    # Bounded, not only finished (GV without the correctors also exits 0: notes 13.8).
    assert max(courant) < 50 and float(continuity[-1].rstrip(",")) < 1e-6
