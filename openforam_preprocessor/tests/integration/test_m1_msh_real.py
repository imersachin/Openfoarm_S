"""M1-MSH on real OpenFOAM v2512: the .msh import through the application.

Repeats E0.1 (Gmsh 2.2, 4.1 and binary), E0.3 (Fluent) and E0.5 (a rotating
case through AMI, checks, case setup and a few pimpleFoam steps) with the
application's code instead of the evidence scripts, plus units in mm and a
mesh whose checkMesh fails with exit code 0. **Synthetic meshes only**
(tests/fixtures/msh/m1); a run that completes is not evidence that its
results are correct (invariant 15).

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_m1_msh_real.py -v -s
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from case_setup.case import CASE_DIR
from machines.config import MachineProjectConfig
from machines.msh_import.cases import IMPORTED_CASE, SCRATCH_CASE
from machines.msh_import.checks import check_converted
from machines.msh_import.detect import inspect_mesh_file
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT,
    IMPORT_REPORT,
)
from machines.pipeline import MachinePipeline, MachineRunResult
from machines.service import MachineService
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1, mesh_file
from tests.foam_binary import read_points
from visualization.foam_reader import read_boundary

E0 = Path(__file__).parents[1] / "fixtures" / "msh" / "e0" / "common"
TOOLS = ("gmshToFoam", "fluent3DMeshToFoam", "transformPoints", "foamMeshToFluent",
         "topoSet", "surfaceMeshExtract", *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES,
         "pimpleFoam")
HAS_GMSH = importlib.util.find_spec("gmsh") is not None
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


def run(command: list[str], case: Path) -> subprocess.CompletedProcess[str]:
    # From the case directory (G0 G11: the repository's name ends in "processor").
    return subprocess.run(command, cwd=case, capture_output=True, text=True, timeout=900,
                          check=False)


def mesh_digest(mesh: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(mesh.iterdir()) if p.is_file()}


def import_run(root: Path, data: dict[str, Any]) -> MachineRunResult:
    config = MachineProjectConfig.model_validate(data)
    return MachinePipeline().run_sync(root, config)


def solve(case: Path) -> str:
    result = run(["pimpleFoam"], case)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


# --- E0.1 and E0.3: conversion and the box ----------------------------------------------

@pytest.mark.parametrize("name", ["box_22.msh", "box_41.msh", "fluent_box.msh",
                                  *(["box_22_bin.msh"] if HAS_GMSH else [])])
def test_each_format_converts_with_the_box_of_the_file(tmp_path: Path, name: str) -> None:
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file(name, (M1 / name).read_bytes())
    scratch = service.convert_for_box(path)
    boundary = {p.name: (p.patch_type, p.n_faces) for p in read_boundary(
        service.root / "cases" / SCRATCH_CASE / "constant/polyMesh/boundary")}

    assert scratch.issues == (), [i.message for i in scratch.issues]
    assert np.allclose(scratch.box, ((0.0, 0.0, 0.0), (1.0, 0.5, 0.5)))
    raw = inspect_mesh_file(path).box
    if raw is not None:  # ASCII: the raw box is the converted mesh's box
        assert np.allclose(raw, scratch.box)
    assert set(boundary) == {"inlet", "outlet", "walls"}
    assert boundary["walls"][0] == ("wall" if name.startswith("fluent") else "patch")
    assert all(faces > 0 for _, faces in boundary.values())
    if name == "box_22_bin.msh":
        assert service.inspect_mesh(path).box_source == "from the converted mesh"


def test_faces_in_no_group_are_found_after_a_real_conversion(tmp_path: Path) -> None:
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file("box_22_nowalls.msh",
                                   (M1 / "box_22_nowalls.msh").read_bytes())
    scratch = service.convert_for_box(path)
    assert scratch.issues == ()
    config = MachineProjectConfig.model_validate(configs.box(path))
    found = check_converted(service.root / "cases" / SCRATCH_CASE, inspect_mesh_file(path),
                            config, "m")
    issue = next(i for i in found if i.code == "GMSH_UNGROUPED_FACES")

    assert issue.severity.value == "BLOCKING" and issue.details["faces"] > 0
    assert np.allclose(issue.details["box"], [[0, 0, 0], [1, 0.5, 0.5]])


# --- E0.5: a rotating case -----------------------------------------------------------------

@pytest.mark.parametrize("name", ["rotating_41.msh", "fluent_rotating.msh"])
def test_a_rotating_mesh_is_imported_set_up_and_runs(tmp_path: Path, name: str) -> None:
    root = tmp_path / "project"
    data = configs.rotating(mesh_file(name, tmp_path / "inputs"))
    without = {k: v for k, v in data.items() if k != "physics"}
    first = import_run(root, without)
    assert first.succeeded, (first.message, [i.message for i in first.issues])
    imported = mesh_digest(root / "cases" / IMPORTED_CASE / "constant/polyMesh")
    merged = mesh_digest(root / "cases/merged/constant/polyMesh")

    result = import_run(root, data)  # physics added: case setup only (invariant 14)

    assert result.succeeded, (result.message, [i.message for i in result.issues])
    assert IMPORT in result.reused and "machine_case_setup" in result.executed
    assert mesh_digest(root / "cases" / IMPORTED_CASE / "constant/polyMesh") == imported
    assert mesh_digest(root / "cases/merged/constant/polyMesh") == merged
    case = root / CASE_DIR
    boundary = case / "constant/polyMesh/boundary"
    patches = {p.name: p.patch_type for p in read_boundary(boundary)}
    # M1c: the pair keeps its own names (foamDictionary), and pimpleFoam runs on it.
    assert patches["ami_stat"] == patches["ami_rot"] == "cyclicAMI"
    assert patches["rotor"] == "wall" and not any(n.endswith("_ami") for n in patches)
    assert re.search(r"ami_stat\s*\{[^}]*neighbourPatch\s+ami_rot;", boundary.read_text())
    dynamic = (case / "constant/dynamicMeshDict").read_text("utf-8")
    assert "cellZone        rotating;" in dynamic

    log = solve(case)
    assert len(re.findall(r"^Time = ", log, re.M)) == 10 and log.rstrip().endswith("End")
    sums = [float(v) for v in re.findall(r"sum\(weights\) min:(\S+)", log)]
    assert sums and min(sums) > 0.85
    report = (root / IMPORT_REPORT).read_text("utf-8")
    assert '"version": "v2512"' in report


def test_units_in_mm_give_the_same_mesh_as_metres(tmp_path: Path) -> None:
    """E0.6: the same rotating mesh written in mm, imported with units mm."""
    work = tmp_path / "mm"
    (work / "system").mkdir(parents=True)
    (work / "constant").mkdir()
    for dictionary in (E0 / "system").iterdir():
        shutil.copy(dictionary, work / "system")
    assert run(["gmshToFoam", str(M1 / "rotating_41.msh"), "-case", "."], work).returncode == 0
    assert run(["transformPoints", "-case", ".", "-scale", "(1000 1000 1000)"],
               work).returncode == 0
    assert run(["foamMeshToFluent", "-case", "."], work).returncode == 0
    stationary = 3190  # the rotating_41.msh zones: stationary cells first (E6 split)
    mm_file = tmp_path / "rotating_mm.msh"
    split = subprocess.run([sys.executable, str(E0 / "split_fluent_cells.py"),
                            str(work / "fluentInterface/mm.msh"), str(mm_file),
                            str(stationary), "stationary", "rotating"],
                           capture_output=True, text=True, check=False)
    assert split.returncode == 0, split.stderr

    in_mm = import_run(tmp_path / "in_mm", configs.rotating(mm_file, units="mm"))
    in_m = import_run(tmp_path / "in_m", configs.rotating(mesh_file(
        "fluent_rotating.msh", tmp_path / "inputs")))

    assert in_mm.succeeded and in_m.succeeded, (in_mm.message, in_m.message)
    points = [read_points(r / "cases/merged/constant/polyMesh/points")
              for r in (tmp_path / "in_mm", tmp_path / "in_m")]
    assert np.allclose(points[0], points[1], atol=1e-9)


@pytest.mark.skipif(not HAS_GMSH, reason="the gmsh package makes the coarse mesh")
def test_a_mesh_failing_checkmesh_with_exit_0_fails(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner review: "Failed N mesh checks" fails the mesh whatever the exit code.
    The rotating geometry meshed coarser (0.5, 0.05 instead of the fixture's 0.3,
    0.04) has a non-orthogonality checkMesh counts as a failed check."""
    from tests.fixtures.msh.m1.make_gmsh_small import coarse, make_gmsh

    monkeypatch.setattr(make_gmsh, "_mesh", lambda _a, _b: coarse(0.5, 0.05))
    path = tmp_path / "coarse.msh"
    make_gmsh.rotating(path, "4.1", False)
    result = import_run(tmp_path / "project", configs.rotating(path))

    log = (tmp_path / "project/logs/merged/checkMesh.log").read_text("utf-8")
    assert re.search(r"Failed \d+ mesh checks", log)
    assert not result.succeeded and result.mesh is not None
    assert result.mesh.status.value == "FAILED"
    assert "CHECKMESH_FAILURE" in {i.code for i in result.issues}
    assert result.case_setup is not None and result.case_setup.reason == "MESH_NOT_READY"


# --- after the M1b testing review -----------------------------------------------------------

@pytest.mark.parametrize("name", ["box_22.msh", "box_41.msh", "box_22_nowalls.msh",
                                  "rotating_41.msh", "fluent_box.msh",
                                  "fluent_rotating.msh",
                                  *(["box_22_bin.msh"] if HAS_GMSH else [])])
def test_detected_names_are_what_the_real_converter_writes(tmp_path: Path, name: str) -> None:
    """The detector, not only the fake, is checked against v2512: every patch
    and cell zone it reads is in the converted mesh, and nothing else is."""
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file(name, (M1 / name).read_bytes())
    assert service.convert_for_box(path).issues == ()
    poly = service.root / "cases" / SCRATCH_CASE / "constant/polyMesh"
    info = inspect_mesh_file(path)
    converted = {p.name for p in read_boundary(poly / "boundary")} - {"defaultFaces"}
    zones = set(re.findall(r"^(\S+)\s*\n\{\s*type\s+cellZone;",
                           # binary since M2a: the names are text, the labels not
                           (poly / "cellZones").read_text("utf-8", errors="replace"), re.M))

    assert set(info.patches) == converted
    assert set(info.cell_zones) == zones


def test_the_scratch_conversion_is_promoted_on_real_openfoam(tmp_path: Path) -> None:
    """Owner answer 4 on v2512: box from a scale-1 conversion, units confirmed
    (cm), the run moves that conversion into place and scales it; no second
    conversion."""
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file("rotating_41.msh", (M1 / "rotating_41.msh").read_bytes())
    assert service.convert_for_box(path).issues == ()
    service.save({k: v for k, v in configs.rotating(path, units="cm").items()
                  if k != "physics"})
    config = service.saved_config()
    assert config is not None
    result = service.pipeline.run_sync(service.root, config)
    logs = service.root / "logs" / IMPORTED_CASE

    assert result.succeeded, (result.message, [i.message for i in result.issues])
    assert f"{IMPORT}:scratch" in result.reused
    assert (logs / "00_promote.log").is_file() and not list(logs.glob("*_convert.log"))
    points = read_points(service.root / "cases/merged/constant/polyMesh/points")
    assert np.allclose(points.min(axis=0), (-0.01, -0.01, -0.01))
    assert np.allclose(points.max(axis=0), (0.03, 0.01, 0.01))


# --- M1c: the inlet area measured from the mesh, the weights over one revolution -------------

def test_a_flow_rate_inlet_gets_its_area_from_the_mesh_and_runs(tmp_path: Path) -> None:
    """The rotating box's inlet is its x = -1 face, 2 m x 2 m."""
    root = tmp_path / "project"
    data = configs.rotating(mesh_file("fluent_rotating.msh", tmp_path / "inputs"))
    data["physics"]["inlets"] = {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": 20.0}}
    result = import_run(root, data)

    assert result.succeeded, (result.message, [i.message for i in result.issues])
    report = json.loads((root / "reports/case_setup.json").read_text("utf-8"))
    assert report["inlet_area_m2"]["inlet"] == pytest.approx(4.0)
    assert report["inlets"]["inlet"]["speed"] == pytest.approx(5.0)
    assert "flowRateInletVelocity" in (root / CASE_DIR / "0/U").read_text("utf-8")
    imported = json.loads((root / IMPORT_REPORT).read_text("utf-8"))
    assert imported["patch_area_m2"]["inlet"] == pytest.approx(4.0)
    log = solve(root / CASE_DIR)
    assert len(re.findall(r"^Time = ", log, re.M)) == 10 and log.rstrip().endswith("End")


@pytest.mark.skipif(shutil.which("moveDynamicMesh") is None, reason="moveDynamicMesh")
@pytest.mark.parametrize("name", ["rotating_41.msh", "fluent_rotating.msh"])
def test_the_weights_over_one_revolution(tmp_path: Path, name: str) -> None:
    service = MachineService(tmp_path / "project")
    path = service.store_mesh_file(name, (M1 / name).read_bytes())
    service.save({k: v for k, v in configs.rotating(path).items() if k != "physics"})
    config = service.saved_config()
    assert config is not None
    assert service.pipeline.run_sync(service.root, config).succeeded
    at_rest = json.loads((service.root / "reports/mesh_quality_report.json").read_text("utf-8"))
    started = time.monotonic()
    result = service.check_ami_revolution()
    took = time.monotonic() - started

    assert result.done and result.steps == 36 and result.failed_steps == 0, result.issues
    (pair,) = result.weights
    print(f"\n{name}: at rest {at_rest['ami_weights']}, over one revolution "
          f"{pair.minimum:.6g}-{pair.maximum:.6g}, {took:.1f} s")
    out = pair.minimum < 0.95 or pair.maximum > 1.05
    assert ("AMI_WEIGHTS_OUT_OF_RANGE" in {i.code for i in result.issues}) == out
    assert not (service.root / "cases/ami_revolution").exists()  # its VTK files too
    assert took < 60


def test_a_rotating_mesh_with_an_ungrouped_face_stops_with_its_box(tmp_path: Path) -> None:
    """The M1c browser checklist's file: y_min in no physical group."""
    data = configs.rotating(mesh_file("rotating_41_noymin.msh", tmp_path / "inputs"))
    data["patches"] = [p for p in data["patches"] if p["name"] != "y_min"]
    result = import_run(tmp_path / "project", data)
    issue = next(i for i in result.issues if i.code == "GMSH_UNGROUPED_FACES")

    assert not result.succeeded and issue.severity.value == "BLOCKING"
    assert issue.details["faces"] > 0
    low, high = issue.details["box"]
    assert low[1] == pytest.approx(-1) and high[1] == pytest.approx(-1)  # the y = -1 face


def test_fluent_wall_zones_paired_as_ami_lose_their_wall_group(tmp_path: Path) -> None:
    """M1c review: fluent3DMeshToFoam writes wall zones with inGroups 1(wall)."""
    text = (M1 / "fluent_rotating.msh").read_text("latin-1")
    for name in ("ami_stat", "ami_rot"):
        text = re.sub(rf"\(39 \((\d+) pressure-outlet {name}\)", rf"(39 (\1 wall {name})", text)
    path = tmp_path / "inputs" / "fluent_walls.msh"
    path.parent.mkdir()
    path.write_text(text, encoding="latin-1")
    result = import_run(tmp_path / "project", {
        k: v for k, v in configs.rotating(path).items() if k != "physics"})

    assert result.succeeded, (result.message, [i.message for i in result.issues])
    imported = (tmp_path / "project/cases/imported/constant/polyMesh/boundary").read_text()
    assert re.search(r"ami_stat\s*\{[^}]*inGroups", imported)  # as the converter wrote it
    merged = (tmp_path / "project/cases/merged/constant/polyMesh/boundary").read_text()
    side = re.search(r"ami_stat\s*\{([^}]*)\}", merged)
    assert side is not None and "cyclicAMI" in side[1] and "inGroups" not in side[1]
