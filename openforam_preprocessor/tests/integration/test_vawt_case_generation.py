"""V2 dictionaries against real OpenFOAM v2512 and the V0 fixtures.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_vawt_case_generation.py -v

The command sequence here is the V0 one (docs/vawt_method_notes.md); the
pipeline that will run it is V3.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mesh.parser import CheckMeshMetrics, CheckMeshParser
from tests.fixtures.vawt.drafts import preset_draft
from tests.fixtures.vawt.rotor import write_rotor
from vawt.case_generator import AMI_PATCHES, VawtCaseGenerator
from vawt.config import VawtProjectConfig

TOOLS = ("blockMesh", "snappyHexMesh", "surfaceFeatureExtract", "checkMesh", "mergeMeshes",
         "createPatch", "topoSet", "foamDictionary")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]

V0 = Path(__file__).parents[1] / "fixtures" / "vawt" / "v0"
E1 = V0 / "E1_ami_two_mesh"
GEN = VawtCaseGenerator()


def e1_config(tmp_path: Path) -> VawtProjectConfig:
    """The E1 settings: SIMPLE preset for the fixture rotor, features, relative layers."""
    draft = preset_draft(tmp_path)
    draft["refinement"]["extract_features"] = True
    draft["layers"]["enabled"] = True
    return VawtProjectConfig.model_validate(draft)


def run(log_dir: Path, name: str, *argv: str) -> None:
    # Like openfoam/runner.py, run from the case directory (the -case argument, or
    # the master case for mergeMeshes). OpenFOAM v2512 mergeMeshes appends
    # "/processor" to the master path when the working directory's name ends in
    # "processor" (as this repository's does), and then fails.
    case = Path(argv[argv.index("-case") + 1]) if "-case" in argv else Path(argv[-2])
    log_dir.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=case)
    (log_dir / f"{name}.log").write_text(done.stdout + done.stderr, "utf-8")
    assert done.returncode == 0, f"{name} failed:\n{done.stderr[-2000:]}"


def check(case: Path, log_dir: Path) -> CheckMeshMetrics:
    # The engine's checkMesh options (no -allGeometry; see docs/architecture.md 16).
    run(log_dir, f"checkMesh_{case.name}", "checkMesh", "-case", str(case),
        "-allTopology", "-meshQuality")
    return CheckMeshParser().parse_file(log_dir / f"checkMesh_{case.name}.log")


def expanded(path: Path) -> list[str]:
    path = path.resolve()
    text = subprocess.run(["foamDictionary", path.name, "-expand"], capture_output=True,
                          text=True, check=True, cwd=path.parent).stdout
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith(("//", "/*", "\\", "|"))]


# --- comparison with the V0 dictionaries that ran ------------------------------------------

# Differences that are intended, each with its reason.
INTENDED = {
    "rotor/system/blockMeshDict": "V0 rounded the rotor block by hand (+-0.875, 38 cells); "
    "the generator uses exactly 2 zone cells of margin (+-0.8745, 37 cells)",
    "rotor/system/topoSetDict": "box is the rotor block itself instead of +-1000 m",
}
FIXTURE_OF = {
    "outer/system/blockMeshDict": E1 / "outer/system/blockMeshDict",
    "outer/system/snappyHexMeshDict": E1 / "outer/system/snappyHexMeshDict",
    "rotor/system/snappyHexMeshDict": E1 / "rotor/system/snappyHexMeshDict",
    "rotor/system/surfaceFeatureExtractDict": E1 / "rotor/system/surfaceFeatureExtractDict",
    "merged/system/createPatchDict": E1 / "merged/system/createPatchDict",
    **{f"{case}/system/{name}": V0 / "common/system" / name
       for case in ("outer", "rotor", "merged")
       for name in ("controlDict", "fvSchemes", "fvSolution", "meshQualityDict")},
}


def test_generated_ami_dictionaries_match_v0(tmp_path: Path) -> None:
    project = tmp_path / "project"
    GEN.generate(project, e1_config(tmp_path))
    generated = {p.relative_to(project / "cases").as_posix()
                 for p in (project / "cases").rglob("*") if p.is_file()}
    assert generated == set(FIXTURE_OF) | set(INTENDED)

    mismatches = {}
    for relative, fixture in FIXTURE_OF.items():
        # Stage the fixture as run.sh does: common files next to the case's own.
        staged = tmp_path / "fixture" / relative
        staged.parent.mkdir(parents=True, exist_ok=True)
        for common in (V0 / "common/system").iterdir():
            shutil.copy(common, staged.parent / common.name)
        shutil.copy(fixture, staged)
        # The only intended difference in these files: mesh points are now moved
        # off cell faces (R1); V0 placed them by hand.
        ours = [x for x in expanded(project / "cases" / relative)
                if not x.startswith("locationInMesh")]
        theirs = [x for x in expanded(staged) if not x.startswith("locationInMesh")]
        if ours != theirs:
            mismatches[relative] = [(a, b) for a, b in zip(ours, theirs, strict=False)
                                    if a != b][:3]
    assert mismatches == {}


# --- the generated cases mesh on real OpenFOAM ---------------------------------------------

def mesh_rotor(case: Path, logs: Path, stl: Path, features: bool) -> None:
    shutil.copy(stl, case / "constant" / "triSurface" / "rotor.stl")
    run(logs, "rotor_blockMesh", "blockMesh", "-case", str(case))
    if features:
        run(logs, "rotor_surfaceFeatureExtract", "surfaceFeatureExtract", "-case", str(case))
    run(logs, "rotor_snappyHexMesh", "snappyHexMesh", "-case", str(case), "-overwrite")
    run(logs, "rotor_topoSet", "topoSet", "-case", str(case))


def prepare(project: Path, config: VawtProjectConfig, tmp_path: Path) -> Path:
    result = GEN.generate(project, config)
    assert result.layout is not None
    for sub_case in result.layout.sub_cases:
        (project / "cases" / sub_case / "constant" / "triSurface").mkdir(parents=True)
    return write_rotor(tmp_path / "rotor.stl")


def zone_cells(case: Path) -> str:
    return (case / "constant" / "polyMesh" / "cellZones").read_text("utf-8", errors="replace")


def test_generated_ami_case_meshes(tmp_path: Path) -> None:
    project, logs = tmp_path / "project", tmp_path / "logs"
    stl = prepare(project, e1_config(tmp_path), tmp_path)
    outer, rotor, merged = (project / "cases" / n for n in ("outer", "rotor", "merged"))

    run(logs, "outer_blockMesh", "blockMesh", "-case", str(outer))
    run(logs, "outer_snappyHexMesh", "snappyHexMesh", "-case", str(outer), "-overwrite")
    mesh_rotor(rotor, logs, stl, features=True)
    shutil.copytree(outer / "constant" / "polyMesh", merged / "constant" / "polyMesh")
    run(logs, "mergeMeshes", "mergeMeshes", "-overwrite", str(merged), str(rotor))
    run(logs, "createPatch", "createPatch", "-case", str(merged), "-overwrite")
    metrics = check(merged, logs)

    assert metrics.mesh_ok, metrics.failed_check_messages
    faces = {p.name: p.faces for p in metrics.patches}
    assert faces[AMI_PATCHES[0]] > 0 and faces[AMI_PATCHES[1]] > 0
    assert {"inlet", "outlet", "rotor"} <= set(faces)
    assert "rotating" in zone_cells(merged)


def test_generated_rotor_only_cell_zone_case_meshes(tmp_path: Path) -> None:
    draft: dict[str, Any] = preset_draft(tmp_path, include_domain=False)  # CELL_ZONE
    draft["refinement"].update(blade_min_level=0, blade_max_level=1)
    project, logs = tmp_path / "project", tmp_path / "logs"
    stl = prepare(project, VawtProjectConfig.model_validate(draft), tmp_path)
    rotor = project / "cases" / "rotor"

    mesh_rotor(rotor, logs, stl, features=False)
    metrics = check(rotor, logs)

    assert metrics.mesh_ok, metrics.failed_check_messages
    faces = {p.name: p.faces for p in metrics.patches}
    assert faces["rotatingZone"] > 0 and faces["rotor"] > 0
    assert "rotating" in zone_cells(rotor)


def test_generated_single_mesh_cell_zone_case_meshes(tmp_path: Path) -> None:
    draft: dict[str, Any] = preset_draft(tmp_path)
    draft["rotating_zone"]["interface"] = "CELL_ZONE"
    # Coarser blades keep this run short; it checks that the dictionaries run.
    draft["refinement"].update(blade_min_level=0, blade_max_level=1)
    project, logs = tmp_path / "project", tmp_path / "logs"
    stl = prepare(project, VawtProjectConfig.model_validate(draft), tmp_path)
    merged = project / "cases" / "merged"
    shutil.copy(stl, merged / "constant" / "triSurface" / "rotor.stl")

    run(logs, "blockMesh", "blockMesh", "-case", str(merged))
    run(logs, "snappyHexMesh", "snappyHexMesh", "-case", str(merged), "-overwrite")
    metrics = check(merged, logs)

    assert metrics.mesh_ok, metrics.failed_check_messages
    assert "rotating" in zone_cells(merged)
    faces = (merged / "constant" / "polyMesh" / "faceZones").read_text("utf-8", "replace")
    assert "rotatingZone" in faces
    assert "AMI1" not in {p.name for p in metrics.patches}
