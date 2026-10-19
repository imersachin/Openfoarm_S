"""M1-MSH E0 evidence (.msh import method proof) read back without OpenFOAM.

Logs are captured by tests/fixtures/msh/e0/run_all.sh on OpenFOAM v2512 with
gmsh 4.15.2. Expected values were read from the raw logs and boundary files,
not from the parser. These tests pin what M1b relies on
(docs/msh_import_notes.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mesh.parser import CheckMeshMetrics, CheckMeshParser
from visualization.foam_reader import read_boundary

E0 = Path(__file__).parents[2] / "fixtures" / "msh" / "e0"
EXPERIMENTS = ("E1_gmsh", "E2_fluent", "E3_baffles", "E4_rotating", "E5_failures")
BOX = {"inlet": 118, "outlet": 118, "walls": 872}  # the make_gmsh box
FLUENT_BOX = {"inlet": ("patch", 100), "outlet": ("patch", 100), "walls": ("wall", 800)}


def parse(relative: str) -> CheckMeshMetrics:
    return CheckMeshParser().parse_file(E0 / relative)


def boundary(relative: str) -> dict[str, tuple[str, int]]:
    return {p.name: (p.patch_type, p.n_faces) for p in read_boundary(E0 / relative)}


def faces(relative: str) -> dict[str, int]:
    return {name: count for name, (_, count) in boundary(relative).items()}


def zones(relative: str) -> list[str]:
    return (E0 / relative).read_text(encoding="utf-8").split("\n")[:-1]


def log(relative: str) -> str:
    return (E0 / relative).read_text(encoding="utf-8")


def output(experiment: str, name: str) -> str:
    """A command's stdout and stderr logs (stderr is kept only when not empty)."""
    error = E0 / experiment / "logs" / f"{name}.stderr.log"
    return log(f"{experiment}/logs/{name}.log") + (
        error.read_text(encoding="utf-8") if error.is_file() else "")


def exit_codes(experiment: str) -> dict[str, int]:
    text = log(f"{experiment}/logs/exit_codes.txt")
    return {m[1]: int(m[2]) for m in re.finditer(r"^(\S+)\s+exit=(\d+)", text, re.M)}


@pytest.mark.parametrize("experiment", EXPERIMENTS)
def test_every_experiment_has_logs(experiment: str) -> None:
    assert exit_codes(experiment)


# --- E1 Gmsh ------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["box_22", "box_41", "box_41_as22", "box_41_bin_as22"])
def test_ascii_msh_22_and_41_convert_directly_with_the_same_mesh(name: str) -> None:
    metrics = parse(f"E1_gmsh/logs/{name}_02_checkMesh.log")

    assert exit_codes("E1_gmsh")[f"{name}_01_gmshToFoam"] == 0
    assert metrics.mesh_ok and metrics.cells == 2842
    assert faces(f"E1_gmsh/logs/{name}_boundary") == BOX
    assert zones(f"E1_gmsh/logs/{name}_cellZones.txt") == ["fluid cellZone cellLabels 2842"]


@pytest.mark.parametrize("name", ["box_22_bin", "box_41_bin"])
def test_binary_msh_does_not_convert(name: str) -> None:
    assert exit_codes("E1_gmsh")[f"{name}_01_gmshToFoam"] == 1
    assert "Can only read ascii msh files." in output("E1_gmsh", f"{name}_01_gmshToFoam")


def test_no_physical_groups_converts_silently_to_one_patch() -> None:
    metrics = parse("E1_gmsh/logs/box_22_nogroups_02_checkMesh.log")

    assert exit_codes("E1_gmsh")["box_22_nogroups_01_gmshToFoam"] == 0
    assert metrics.mesh_ok
    assert faces("E1_gmsh/logs/box_22_nogroups_boundary") == {"patch0": 1108}
    assert zones("E1_gmsh/logs/box_22_nogroups_cellZones.txt") == [
        "cellZone_0 cellZone cellLabels 2842"]
    assert "$PhysicalNames" not in log("E1_gmsh/logs/heads/box_22_nogroups.head")


def test_msh_41_without_groups_fails_until_resaved_as_22() -> None:
    assert exit_codes("E1_gmsh")["box_41_nogroups_01_gmshToFoam"] == 1
    assert "Unhandled element 15" in output("E1_gmsh", "box_41_nogroups_01_gmshToFoam")
    assert faces("E1_gmsh/logs/box_41_nogroups_as22_boundary") == {"patch0": 1108}


def test_faces_without_a_group_go_to_default_faces() -> None:
    assert exit_codes("E1_gmsh")["box_22_nowalls_01_gmshToFoam"] == 0
    assert faces("E1_gmsh/logs/box_22_nowalls_boundary") == {
        "inlet": 118, "outlet": 118, "defaultFaces": 872}


def test_msh_1_loses_the_names() -> None:
    assert faces("E1_gmsh/logs/box_v1_boundary") == {"patch0": 118, "patch1": 118, "patch2": 872}


def test_a_missing_gmsh_file_aborts() -> None:
    assert exit_codes("E1_gmsh")["box_missing_01_gmshToFoam"] == 134


# --- E2 Fluent round trip -----------------------------------------------------------------

@pytest.mark.parametrize("case", ["src", "f3d", "f2d", "f2d_zones", "mm_f3d_scale",
                                  "mm_transform"])
def test_fluent_round_trip_keeps_patches_types_and_cells(case: str) -> None:
    assert boundary(f"E2_fluent/logs/{case}_boundary") == FLUENT_BOX
    if case != "src":
        check = "04" if case == "mm_transform" else "02"
        metrics = parse(f"E2_fluent/logs/{case}_{check}_checkMesh.log")
        assert metrics.mesh_ok and metrics.cells == 2000


def test_fluent_round_trip_loses_the_cell_zone() -> None:
    assert zones("E2_fluent/logs/src_cellZones.txt") == ["half cellZone cellLabels 1000"]
    assert zones("E2_fluent/logs/f3d_cellZones.txt") == ["fluid-1 cellZone cellLabels 2000"]
    assert zones("E2_fluent/logs/f2d_cellZones.txt") == ["(no zones)"]
    assert zones("E2_fluent/logs/f2d_zones_cellZones.txt") == ["(no zones)"]
    sections = log("E2_fluent/logs/src_msh_sections.txt")
    assert "(39 (10 pressure-outlet inlet)())" in sections


def test_both_scalings_give_the_same_points() -> None:
    text = log("E2_fluent/logs/mm_compare_points.log")
    assert "mm_transform vs mm_f3d_scale: max |difference| 0.000e+00 m" in text
    assert "box [0.0, 0.0, 0.0] [1.0, 0.5, 0.5]" in text
    unscaled = log("E2_fluent/logs/mm_transform_02_checkMesh_scale1.log")
    assert "Overall domain bounding box (0 0 0) (1000 500 500)" in unscaled


# --- E3 baffles ---------------------------------------------------------------------------

def test_fluent3d_keeps_a_wall_baffle_pair_and_checkmesh_reports_it() -> None:
    patches = boundary("E3_baffles/logs/f3d_boundary")
    metrics = log("E3_baffles/logs/f3d_02_checkMesh.log")

    assert patches["plate"] == ("wall", 50) and patches["plate-shadow"] == ("wall", 50)
    assert "Number of identical duplicate faces (baffle faces): 50" in metrics
    assert "Writing 100 duplicate faces" in log("E3_baffles/logs/f3d_03_detectBaffles.log")


def test_fluent_mesh_to_foam_drops_a_baffle_silently() -> None:
    assert exit_codes("E3_baffles")["f2d_01_fluentMeshToFoam"] == 0
    assert boundary("E3_baffles/logs/f2d_boundary") == FLUENT_BOX  # no plate patches
    assert parse("E3_baffles/logs/f2d_02_checkMesh.log").mesh_ok
    assert "Writing 0 duplicate faces" in log("E3_baffles/logs/f2d_03_detectBaffles.log")


# --- E4 rotating --------------------------------------------------------------------------

def test_gmsh_volumes_become_cell_zones_and_the_zone_surfaces_an_ami_pair() -> None:
    assert zones("E4_rotating/logs/gmsh_cellZones.txt") == [
        "stationary cellZone cellLabels 23408", "rotating cellZone cellLabels 789"]
    patches = boundary("E4_rotating/logs/ami_boundary")
    assert patches["AMI1"] == ("cyclicAMI", 328) and patches["AMI2"] == ("cyclicAMI", 328)
    assert patches["rotor"] == ("wall", 104)
    assert "ami_rot" not in patches and "ami_stat" not in patches
    folder = log("E4_rotating/logs/ami_04_folderChecks.log")
    assert "AMI pairs: (('AMI1', 'AMI2'),)" in folder and "regions: 2" in folder
    assert "validity: VALID quality: WITHIN_LIMITS" in folder and "issues: none" in folder


def test_case_setup_leaves_the_imported_mesh_unchanged_and_the_case_runs() -> None:
    setup = log("E4_rotating/logs/project_01_caseSetup.log")
    solver = log("E4_rotating/logs/project_02_pimpleFoam.log")

    assert "imported mesh files unchanged by case setup: True" in setup
    assert "case mesh is a byte-identical copy: True" in setup
    assert exit_codes("E4_rotating")["project_02_pimpleFoam"] == 0
    assert len(re.findall(r"^Time = ", solver, re.M)) == 10 and solver.rstrip().endswith("End")


def test_the_fluent_route_loses_the_rotating_zone() -> None:
    assert zones("E4_rotating/logs/fluent_cellZones.txt") == ["fluid-1 cellZone cellLabels 24197"]
    patches = boundary("E4_rotating/logs/fluent_boundary")
    assert patches["AMI1"] == ("patch", 328) and patches["AMI2"] == ("patch", 328)
    assert patches["rotor"] == ("wall", 104)
    assert "(39 (18 pressure-outlet AMI2)())" in log("E4_rotating/logs/fluent_msh_zones.txt")
    assert "*Number of regions: 2" in log("E4_rotating/logs/fluent_03_checkMesh.log")


# --- E5 failures --------------------------------------------------------------------------

def test_a_truncated_fluent_file_converts_with_exit_0_to_a_broken_mesh() -> None:
    metrics = parse("E5_failures/logs/truncated_fluent3DMeshToFoam_checkMesh.log")

    assert exit_codes("E5_failures")["truncated_fluent3DMeshToFoam"] == 0
    assert exit_codes("E5_failures")["truncated_fluent3DMeshToFoam_checkMesh"] == 0
    assert metrics.cells == 538 and not metrics.mesh_ok and metrics.failed_checks == 9


@pytest.mark.parametrize("name, code, message", [
    ("truncated_fluentMeshToFoam", 134, "bad size -1"),
    ("h5_fluent3DMeshToFoam", 1, "Do not understand characters: H"),
    ("empty_fluent3DMeshToFoam", 1, "Mesh is not 3D, dimension of grid: 0"),
    ("empty_gmshToFoam", 134, "Cannot dereference nullptr"),
    ("wrong_tool_gmsh_to_fluent3D", 1, "Do not understand characters: $"),
    ("wrong_tool_fluent_to_gmsh", 1, "expected word"),
    ("missing_fluent3DMeshToFoam", 1, "not found"),
])
def test_bad_inputs_fail(name: str, code: int, message: str) -> None:
    assert exit_codes("E5_failures")[name] == code
    assert message in output("E5_failures", name)


def test_reading_the_box_of_an_ascii_file_is_cheap() -> None:
    text = log("E5_failures/logs/size_03_raw_bbox.log")
    match = re.search(r"1030301 nodes, box \[0.0, 0.0, 0.0\] \[1.0, 0.5, 0.5\], "
                      r"184.8 MB in ([\d.]+) s", text)
    assert match is not None
    codes = log("E5_failures/logs/exit_codes.txt")
    assert re.search(r"^size_04_fluent3DMeshToFoam\s+exit=0", codes, re.M)
    small = log("E5_failures/logs/size_06_raw_bbox_small.log")
    assert "box_22_bin.msh: not read: binary MSH" in small
