"""G7, owner addition B: mesh checks that need only a finished polyMesh take a
mesh folder, not the machine configuration (machines.mesh_folder)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.config.models import MeshQualityLimits
from machines import mesh_folder
from machines.mesh_folder import (
    MeshExpectation,
    ami_pairs,
    check_ami_pairing,
    check_ami_weights,
    check_cell_zones,
    check_mesh_folder,
    check_patches,
    check_regions,
    poly_mesh_dir,
    read_patches,
)
from tests.foam_block import write_block_mesh

CHECKMESH = Path(__file__).resolve().parents[2] / "fixtures" / "checkmesh"
AMI = {"x_min": ("inlet", "patch"), "x_max": ("outlet", "patch"),
       "y_min": ("zone_stat", "cyclicAMI"), "y_max": ("zone_rot", "cyclicAMI"),
       "z_min": ("walls", "wall"), "z_max": ("walls", "wall")}


def with_neighbours(poly: Path, neighbours: dict[str, str | None]) -> None:
    """Add neighbourPatch entries to the cyclicAMI patches of a block mesh."""
    text = (poly / "boundary").read_text()
    for patch, neighbour in neighbours.items():
        marker = f"    {patch}\n    {{\n        type cyclicAMI;\n"
        assert marker in text, patch
        if neighbour is not None:
            text = text.replace(marker, marker + f"        neighbourPatch {neighbour};\n")
    (poly / "boundary").write_text(text)


def test_the_module_never_takes_the_machine_configuration() -> None:
    tree = ast.parse(Path(mesh_folder.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "machines.config" not in imported and "machines.case_generator" not in imported


def test_patches_against_an_expectation(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (3, 2, 2))
    patches, problem = read_patches(tmp_path, "duct")
    assert problem is None and [p.name for p in patches] == ["inlet", "outlet", "walls"]
    expected = MeshExpectation("duct", {"inlet": "patch", "outlet": "patch", "walls": "patch",
                                        "missing": "patch"})
    found = {i.code: i.details for i in check_patches(patches, expected)}
    assert found["DOMAIN_PATCH_MISSING"]["patch"] == "missing"
    assert found["DOMAIN_PATCH_TYPE_WRONG"]["found_type"] == "wall"
    assert poly_mesh_dir(tmp_path) == poly and poly_mesh_dir(poly) == poly


def test_an_unreadable_folder_is_one_finding(tmp_path: Path) -> None:
    report = check_mesh_folder(tmp_path / "nothing")
    assert [i.code for i in report.issues] == ["DOMAIN_MESH_UNREADABLE"]


def test_cell_zones_and_regions(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (2, 1, 1))
    (poly / "cellZones").write_text(
        "FoamFile\n{\n    class regIOobject;\n}\n1\n(\nrotating\n"
        "{\n    type cellZone;\n    cellLabels List<label> 0();\n}\n)\n")
    assert check_cell_zones(tmp_path, ["rotating"], "merged") == ()
    (missing,) = check_cell_zones(tmp_path, ["runner"], "merged")
    assert missing.code == "CELL_ZONE_MISSING" and "merged" in missing.message
    assert check_regions("Number of regions: 2 (OK).", 2, "merged") == ()
    assert [i.code for i in check_regions("Number of regions: 1", 2, "merged")] == [
        "REGION_COUNT_UNEXPECTED"]
    assert [i.code for i in check_regions("", 2, "merged")] == ["REGION_COUNT_UNKNOWN"]


def test_ami_weights_from_a_log() -> None:
    log = ("AMI: Creating AMI for source:a and target:b\n"
           "AMI: Patch source sum(weights) min:0.95 max:1.02\n"
           "AMI: Patch target sum(weights) min:0.9 max:1.6\n")
    (high,) = check_ami_weights(log, [("a", "b")])
    assert high.code == "AMI_WEIGHTS_OUT_OF_RANGE" and high.details["maximum"] == 1.6
    assert [i.code for i in check_ami_weights(log, [("c", "d")])] == ["AMI_WEIGHTS_UNKNOWN"]


def test_ami_pairing_needs_no_expectation(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (2, 2, 2), patches=AMI)
    with_neighbours(poly, {"zone_stat": "zone_rot", "zone_rot": "zone_stat"})
    assert check_ami_pairing(tmp_path) == ()
    assert ami_pairs(tmp_path) == (("zone_rot", "zone_stat"),)


@pytest.mark.parametrize("neighbours, code", [
    ({"zone_stat": None, "zone_rot": "zone_stat"}, "AMI_NEIGHBOUR_MISSING"),
    ({"zone_stat": "inlet", "zone_rot": "zone_stat"}, "AMI_NEIGHBOUR_MISSING"),
    ({"zone_stat": "zone_rot", "zone_rot": "zone_rot"}, "AMI_NOT_RECIPROCAL"),
])
def test_broken_ami_pairs(tmp_path: Path, neighbours: dict[str, str | None],
                          code: str) -> None:
    poly = write_block_mesh(tmp_path, (2, 2, 2), patches=AMI)
    with_neighbours(poly, neighbours)
    assert code in [i.code for i in check_ami_pairing(tmp_path)]


def test_a_foreign_folder_without_checkmesh_output(tmp_path: Path) -> None:
    write_block_mesh(tmp_path, (3, 2, 2))
    report = check_mesh_folder(tmp_path, limits=MeshQualityLimits())
    assert [p.name for p in report.patches] == ["inlet", "outlet", "walls"]
    assert report.quality is None and report.regions is None
    assert [i.code for i in report.issues] == ["CHECK_MESH_OUTPUT_MISSING"]


def test_a_foreign_folder_with_checkmesh_output(tmp_path: Path) -> None:
    write_block_mesh(tmp_path, (3, 2, 2))
    (tmp_path / "log.checkMesh").write_text((CHECKMESH / "ok.log").read_text())
    report = check_mesh_folder(tmp_path, limits=MeshQualityLimits())
    assert report.quality is not None and report.quality.validity.value == "VALID"
    assert not any(i.code == "CHECK_MESH_OUTPUT_MISSING" for i in report.issues)
    failed = check_mesh_folder(tmp_path, limits=MeshQualityLimits(),
                               check_mesh_log=(CHECKMESH / "negative_volume.log").read_text())
    assert failed.quality is not None and failed.quality.validity.value == "INVALID"


def test_an_expectation_adds_patches_zones_and_regions(tmp_path: Path) -> None:
    write_block_mesh(tmp_path, (3, 2, 2))
    expected = MeshExpectation("merged", {"inlet": "patch"}, other=("outlet", "walls"),
                               cell_zones=("rotating",), regions=2)
    report = check_mesh_folder(tmp_path, expected=expected,
                               check_mesh_log="Number of regions: 1")
    assert {i.code for i in report.issues} == {"CELL_ZONE_MISSING", "REGION_COUNT_UNEXPECTED"}
