"""G2 domain meshes against real OpenFOAM v2512, on the G0 R1 and R2 geometry.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_machine_domains_real.py -v

The command sequence is G0's (blockMesh, surfaceFeatureExtract, snappyHexMesh
-overwrite, checkMesh); the pipeline that will run it comes later. Both of
G0 R2's silent failures are meshed here on purpose, to show that the result
checks catch them (section 18, decision 2).
"""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from machines.case_generator import DomainCase, DomainCaseGenerator
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.mesh_checks import check_domain_case
from machines.validation import validate_machine
from mesh.parser import CheckMeshMetrics, CheckMeshParser
from tests.fixtures.machines import configs
from visualization.foam_reader import read_boundary

TOOLS = ("blockMesh", "snappyHexMesh", "surfaceFeatureExtract", "checkMesh")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
GEN = DomainCaseGenerator()


def run(case: Path, name: str, *argv: str) -> subprocess.CompletedProcess[str]:
    done = subprocess.run([*argv, "-case", str(case)], capture_output=True, text=True,
                          check=False, cwd=case)
    (case / f"{name}.log").write_text(done.stdout + done.stderr, "utf-8")
    assert done.returncode == 0, f"{name} failed:\n{done.stderr[-2000:]}"
    return done


def mesh(root: Path, case: DomainCase) -> CheckMeshMetrics:
    directory = root / case.root
    run(directory, "blockMesh", "blockMesh")
    if case.cut:
        run(directory, "surfaceFeatureExtract", "surfaceFeatureExtract")
        run(directory, "snappyHexMesh", "snappyHexMesh", "-overwrite")
    done = run(directory, "checkMesh", "checkMesh", "-allTopology", "-meshQuality")
    return CheckMeshParser().parse_text(done.stdout)


def faces(root: Path, case: DomainCase) -> dict[str, int]:
    return {p.name: p.n_faces
            for p in read_boundary(root / case.root / "constant/polyMesh/boundary")}


def generate(root: Path, data: dict[str, Any]) -> tuple[DomainCase, ...]:
    config = MachineProjectConfig.model_validate(data)
    cases = GEN.render(config, read_imported(config))
    GEN.write(root, cases)
    return cases


def codes(data: dict[str, Any]) -> dict[str, str]:
    return {i.code: i.severity.value for i in validate_machine(data).issues}


def duct(directory: Path, fmt: str, files: list[str]) -> dict[str, Any]:
    data = configs.duct(directory)
    part = data["domain"]["parts"][0]
    part["format"] = fmt
    part["files"] = [configs.stl(directory / f) for f in files]
    part["cell_size"] = 0.0625  # G0 R2's background cell size
    return data


# --- generated domains ---------------------------------------------------------------------

def test_box_domain(tmp_path: Path) -> None:
    data = configs.pole(tmp_path / "geometry", motion="STATIONARY", hole_diameter=0.16)
    (case,) = generate(tmp_path, data)

    metrics = mesh(tmp_path, case)

    assert metrics.mesh_ok and metrics.cells == 90 * 27 * 38  # the SIMPLE preset box
    assert faces(tmp_path, case) == {
        "inlet": 27 * 38, "outlet": 27 * 38, "side_y_min": 90 * 38, "side_y_max": 90 * 38,
        "side_z_min": 90 * 27, "side_z_max": 90 * 27}
    assert check_domain_case(tmp_path / case.root, case) == ()


def test_cylinder_domain(tmp_path: Path) -> None:
    # G0 R1: cylinder r 2, x -2..5; 0.125 m cells as R1b.
    data = configs.hawt(tmp_path / "geometry")
    data["domain"]["cell_size"] = 0.125
    assert codes(data).get("MESH_POINT_ON_CELL_FACE") is None
    (case,) = generate(tmp_path, data)

    metrics = mesh(tmp_path, case)
    found = faces(tmp_path, case)

    assert metrics.mesh_ok and metrics.failed_checks == 0
    # snappyHexMesh drops the emptied background patch.
    assert set(found) == {"inlet", "outlet", "side"}
    assert all(found[p] > 0 for p in ("inlet", "outlet", "side"))
    assert check_domain_case(tmp_path / case.root, case) == ()
    types = {p.name: p.patch_type
             for p in read_boundary(tmp_path / case.root / "constant/polyMesh/boundary")}
    assert types["side"] == "patch"  # SLIP meshes as patch


# --- imported domains in both formats -------------------------------------------------------

def test_both_formats_give_the_same_mesh(tmp_path: Path) -> None:
    named = duct(tmp_path / "geometry", "NAMED_REGIONS", ["duct_named.stl"])
    per_file = duct(tmp_path / "geometry", "ONE_FILE_PER_PATCH",
                    ["inlet.stl", "outlet.stl", "wall.stl"])
    for data in (named, per_file):
        assert not [c for c, s in codes(data).items() if s in ("ERROR", "BLOCKING")]

    (a,) = generate(tmp_path / "named", named)
    (b,) = generate(tmp_path / "per_file", per_file)
    meshes = [mesh(tmp_path / "named", a), mesh(tmp_path / "per_file", b)]
    found = [faces(tmp_path / "named", a), faces(tmp_path / "per_file", b)]

    assert all(m.mesh_ok for m in meshes) and meshes[0].cells == meshes[1].cells
    assert found[0] == found[1]
    assert set(found[0]) == {"inlet", "outlet", "wall"}  # no background left
    assert all(found[0][p] > 0 for p in ("inlet", "outlet", "wall"))
    assert check_domain_case(tmp_path / "named" / a.root, a) == ()
    types = {p.name: p.patch_type for p in read_boundary(
        tmp_path / "named" / a.root / "constant/polyMesh/boundary")}
    assert types == {"inlet": "patch", "outlet": "patch", "wall": "wall"}


# --- G0 R2's silent failures: caught before meshing, and after ------------------------------

def test_binary_stl_as_named_regions(tmp_path: Path) -> None:
    data = duct(tmp_path / "geometry", "NAMED_REGIONS", ["duct_binary.stl"])

    found = codes(data)
    assert found["BINARY_STL_AS_NAMED_REGIONS"] == "ERROR"
    assert found["PATCH_SOURCE_UNKNOWN"] == "BLOCKING"  # inlet, outlet, wall do not exist

    (case,) = generate(tmp_path, data)  # meshed anyway, as G0 did
    metrics = mesh(tmp_path, case)
    after = {i.code: i for i in check_domain_case(tmp_path / case.root, case)}

    assert metrics.mesh_ok  # checkMesh alone does not see it (G0 R2)
    assert faces(tmp_path, case).keys() == {"duct_binary"}
    missing = [i for i in check_domain_case(tmp_path / case.root, case)
               if i.code == "DOMAIN_PATCH_MISSING"]
    assert sorted(i.details["patch"] for i in missing) == ["inlet", "outlet", "wall"]
    assert after["DOMAIN_PATCH_MISSING"].severity.value == "ERROR"


def test_open_union_of_domain_surfaces(tmp_path: Path) -> None:
    data = duct(tmp_path / "geometry", "ONE_FILE_PER_PATCH", ["inlet.stl", "wall.stl"])
    data["patches"] = [p for p in data["patches"] if p["name"] != "outlet"]

    found = codes(data)
    assert found["IMPORTED_SURFACE_OPEN"] == "BLOCKING"
    assert found["NO_OUTLET"] == "BLOCKING"

    (case,) = generate(tmp_path, data)  # meshed anyway, as G0 did
    mesh(tmp_path, case)  # every command exits 0 (G0 R2)
    after = {i.code: i for i in check_domain_case(tmp_path / case.root, case)}

    # G0's grid also passed checkMesh; whether it does depends on the grid, so
    # only the result check is relied on.
    assert faces(tmp_path, case)["background"] > 0
    assert after["WRONG_REGION_KEPT"].severity.value == "ERROR"


def test_generation_is_deterministic(tmp_path: Path) -> None:
    data = duct(tmp_path / "geometry", "ONE_FILE_PER_PATCH",
                ["inlet.stl", "outlet.stl", "wall.stl"])
    first = generate(tmp_path / "a", copy.deepcopy(data))
    second = generate(tmp_path / "b", copy.deepcopy(data))

    assert first == second
    assert GEN.write(tmp_path / "a", first) == ()  # nothing changed on disk
