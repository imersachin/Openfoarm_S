"""G0 evidence (rotating-machinery method proof) read back without OpenFOAM.

Logs are captured by tests/fixtures/machines/g0/run_all.sh on OpenFOAM v2512.
Expected values were read from the raw logs and boundary files, not from the
parser. These tests pin what later milestones rely on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mesh.parser import CheckMeshMetrics, CheckMeshParser

G0 = Path(__file__).parents[2] / "fixtures" / "machines" / "g0"
EXPERIMENTS = ("R1_cylinder_domain", "R2_imported_domain", "R3_hawt", "R4_francis", "R5_pole")


def parse(relative: str) -> CheckMeshMetrics:
    return CheckMeshParser().parse_file(G0 / relative)


def patch_faces(metrics: CheckMeshMetrics) -> dict[str, int]:
    return {p.name: p.faces for p in metrics.patches if p.name != '".*"'}


def exit_codes(experiment: str) -> dict[str, int]:
    text = (G0 / experiment / "logs" / "exit_codes.txt").read_text(encoding="utf-8")
    return {m[1]: int(m[2]) for m in re.finditer(r"^(\S+)\s+exit=(\d+)", text, re.M)}


@pytest.mark.parametrize("log, cells, faces", [
    ("R1_cylinder_domain/logs/ogrid_02_checkMesh.log", 50176,
     {"inlet": 896, "outlet": 896, "side": 3584}),
    ("R1_cylinder_domain/logs/snappy_04_checkMesh.log", 45472,
     {"inlet": 812, "outlet": 812, "side": 4928}),
])
def test_both_cylinder_domain_methods_give_valid_meshes(log: str, cells: int,
                                                        faces: dict[str, int]) -> None:
    metrics = parse(log)

    assert metrics.mesh_ok and metrics.cells == cells
    assert patch_faces(metrics) == faces


def test_named_regions_and_one_file_per_patch_give_the_same_mesh() -> None:
    named = parse("R2_imported_domain/logs/named_04_checkMesh.log")
    per_file = parse("R2_imported_domain/logs/per_file_04_checkMesh.log")

    for metrics in (named, per_file):
        assert metrics.mesh_ok and metrics.cells == 2048
        assert patch_faces(metrics) == {"inlet": 56, "outlet": 56, "wall": 986}


def test_binary_stl_given_as_named_regions_becomes_one_patch() -> None:
    metrics = parse("R2_imported_domain/logs/binary_04_checkMesh.log")
    checks = json.loads((G0 / "R2_imported_domain/logs/python_checks.json").read_text())

    assert patch_faces(metrics) == {"duct": 1098}  # inlet and outlet are lost, exit 0
    assert checks["binary"]["files"]["duct_binary.stl"]["format"] == "binary"
    assert checks["binary"]["files"]["duct_binary.stl"]["regions"] is None
    assert checks["named"]["files"]["duct_named.stl"]["regions"] == {
        "inlet": 2, "outlet": 2, "wall": 8}


def test_open_imported_domain_meshes_the_outside_and_passes_checkmesh() -> None:
    # The fluid region leaks into the background box: snappyHexMesh exits 0,
    # checkMesh says OK. Only a closedness check before meshing, or the
    # background patch after it, shows the mistake.
    metrics = parse("R2_imported_domain/logs/open_04_checkMesh.log")
    checks = json.loads((G0 / "R2_imported_domain/logs/python_checks.json").read_text())

    assert exit_codes("R2_imported_domain")["open_03_snappyHexMesh"] == 0
    assert metrics.mesh_ok and metrics.cells == 14552
    assert patch_faces(metrics)["background"] == 3928
    assert checks["open"]["union_closed"] is False and checks["open"]["open_edges"] == 4
    assert checks["per_file"]["union_closed"] is True


@pytest.mark.parametrize("log, regions, interfaces", [
    ("R3_hawt/logs/merged_ogrid_03_checkMesh.log", 2, {"AMI1": 5968, "AMI2": 2712}),
    ("R3_hawt/logs/merged_snappy_03_checkMesh.log", 2, {"AMI1": 3232, "AMI2": 2712}),
    ("R4_francis/logs/merged_ami_03_checkMesh.log", 4,
     {"S1": 1804, "S2": 1804, "AMI1": 1804, "AMI2": 1804, "AMI3": 1804, "AMI4": 1828}),
    ("R4_francis/logs/merged_ami_nc_03_checkMesh.log", 4,
     {"S1": 1804, "S2": 2244, "AMI1": 2244, "AMI2": 1804, "AMI3": 1804, "AMI4": 1828}),
    ("R4_francis/logs/merged_stitch_06_checkMesh.log", 3,
     {"AMI1": 1804, "AMI2": 1804, "AMI3": 1804, "AMI4": 1828}),
    ("R5_pole/logs/rotating_merged_03_checkMesh.log", 2, {"AMI1": 3352, "AMI2": 4712}),
    ("R5_pole/logs/hole_merged_03_checkMesh.log", 2,
     {"AMI1": 3504, "AMI2": 4784, "AMI3": 2592, "AMI4": 4480}),
])
def test_rotating_meshes_have_one_region_per_disconnected_part(
        log: str, regions: int, interfaces: dict[str, int]) -> None:
    metrics = parse(log)

    assert metrics.mesh_ok
    assert f"Number of regions: {regions}" in metrics.warning_messages
    faces = patch_faces(metrics)
    assert {name: faces[name] for name in interfaces} == interfaces


def test_stitching_a_non_conformal_stationary_joint_fails() -> None:
    # stitchMesh exits 0, leaves part of the joint unstitched and creates
    # highly skewed faces; the mesh fails a check and the solver stops on the
    # leftover patch.
    metrics = parse("R4_francis/logs/merged_stitch_nc_06_checkMesh.log")
    codes = exit_codes("R4_francis")

    assert codes["merged_stitch_nc_02_stitchMesh"] == 0
    assert not metrics.mesh_ok and metrics.failed_checks == 1
    assert metrics.max_skewness == pytest.approx(5.6285564)
    assert patch_faces(metrics)["casing_out"] == 144
    assert codes["solve_stitch_nc"] == 1


def test_only_the_recorded_failures_exit_non_zero() -> None:
    failures = {(e, step) for e in EXPERIMENTS for step, rc in exit_codes(e).items() if rc}

    assert failures == {
        ("R4_francis", "merged_stitch_05_checkMesh_with_meshPhi"),
        ("R4_francis", "merged_stitch_nc_05_checkMesh_with_meshPhi"),
        ("R4_francis", "solve_stitch_nc"),
    }


SOLVES = [(e, s) for e in EXPERIMENTS for s in exit_codes(e)
          if s.startswith("solve_") and (e, s) != ("R4_francis", "solve_stitch_nc")]


@pytest.mark.parametrize("experiment, solve", SOLVES)
def test_every_solver_smoke_run_completes_ten_steps(experiment: str, solve: str) -> None:
    text = (G0 / experiment / "logs" / f"{solve}.log").read_text(encoding="utf-8")

    assert len(re.findall(r"^Time = ", text, re.M)) == 10
    assert re.search(r"^End\s*$", text, re.M)
    assert "AMI: Patch source sum(weights)" in text


def test_solver_smoke_runs_cover_each_machine() -> None:
    assert {e for e, _ in SOLVES} == {"R3_hawt", "R4_francis", "R5_pole"}
    assert len(SOLVES) == 9
