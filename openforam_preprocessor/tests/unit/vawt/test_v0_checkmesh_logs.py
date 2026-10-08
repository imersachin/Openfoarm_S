"""The existing checkMesh parser against real OpenFOAM v2512 output (V0 evidence).

Logs are captured by tests/fixtures/vawt/v0/run_all.sh. Expected values were
read from the raw logs, not from the parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mesh.parser import CheckMeshMetrics, CheckMeshParser

V0 = Path(__file__).parents[2] / "fixtures" / "vawt" / "v0"


def parse(relative: str) -> CheckMeshMetrics:
    return CheckMeshParser().parse_file(V0 / relative)


def patch_faces(metrics: CheckMeshMetrics) -> dict[str, int]:
    return {p.name: p.faces for p in metrics.patches}


def test_two_mesh_ami_merged_mesh() -> None:
    metrics = parse("E1_ami_two_mesh/logs/merged_flags_none.log")

    assert metrics.recognized and metrics.mesh_ok
    assert (metrics.cells, metrics.faces, metrics.points) == (608926, 1875397, 657919)
    assert metrics.boundary_patches == 9
    faces = patch_faces(metrics)
    assert faces["AMI1"] == 3040 and faces["AMI2"] == 4480
    assert faces["rotor"] == 16224
    assert {"inlet", "outlet", "lateral_min", "lateral_max", "axial_min", "axial_max"} <= set(faces)
    # The interface splits an AMI mesh into two regions; checkMesh reports it as a note.
    assert "Number of regions: 2" in metrics.warning_messages


def test_single_mesh_ami_has_conformal_interface() -> None:
    metrics = parse("E2_single_mesh/logs/E2b_05_checkMesh.log")

    faces = patch_faces(metrics)
    assert faces["AMI1"] == faces["AMI2"] == 3776
    assert metrics.cells == 559710


def test_all_geometry_option_alone_reports_concave_cells() -> None:
    flagged = parse("E1_ami_two_mesh/logs/merged_flags_allGeometry.log")
    engine_flags = parse("E1_ami_two_mesh/logs/merged_03_checkMesh.log")

    for metrics in (flagged, engine_flags):
        assert metrics.failed_checks == 1
        assert not metrics.mesh_ok
        assert metrics.failed_check_messages == (
            "Concave cells (using face planes) found, number of cells: 16536",
        )
    for option in ("none", "meshQuality", "allTopology"):
        assert parse(f"E1_ami_two_mesh/logs/merged_flags_{option}.log").mesh_ok


def test_missing_mesh_output_is_not_recognized() -> None:
    metrics = parse("E4_failures/logs/checkMesh_no_mesh.log")

    assert not metrics.recognized
    assert metrics.cells is None


def test_mesh_of_the_wrong_region_still_parses_as_ok() -> None:
    # locationInMesh inside a blade: snappyHexMesh exits 0 and meshes the blade
    # interior; checkMesh calls it OK. Only the result shows the mistake.
    metrics = parse("E4_failures/logs/location_in_rotor_checkMesh.log")

    assert metrics.mesh_ok
    assert metrics.cells == 4536
    assert set(patch_faces(metrics)) - {'".*"'} == {"rotor"}


@pytest.mark.xfail(strict=True, reason="known: v2512 prints a '\".*\"' row for the group of "
                   "all patches and the parser reports it as a patch (V0 finding)")
def test_patch_group_summary_row_is_not_a_patch() -> None:
    metrics = parse("E1_ami_two_mesh/logs/merged_flags_none.log")

    assert '".*"' not in patch_faces(metrics)
