"""Case files and checkMesh options that OpenFOAM v2512 needs.

Real v2512 runs showed: snappyHexMesh stops without system/fvSchemes;
checkMesh -meshQuality stops when meshQualityDict has no FoamFile header;
checkMesh -allGeometry reports concave cells on ordinary snappyHexMesh meshes,
which made every mesh INVALID. The two real-OpenFOAM integration tests cover
the end-to-end behaviour; these tests pin the causes without OpenFOAM.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.artifacts import MANIFEST_PATH
from core.config.models import ProjectConfig
from core.workflow.dependency_graph import PipelineOperation
from core.workflow.pipeline import PipelineResult
from mesh.generator import OpenFOAMMeshCaseGenerator
from openfoam.commands import check_mesh_step
from tests.fakes import FakeOpenFOAMRunner, fake_pipeline, openfoam_env
from tests.helpers import build_config

FOAM_HEADER = "FoamFile\n{"


def system_text(case: Path, name: str) -> str:
    return (case / "system" / name).read_text(encoding="utf-8")


def run(tmp_path: Path, config: ProjectConfig) -> tuple[PipelineResult, FakeOpenFOAMRunner]:
    runner = FakeOpenFOAMRunner()
    pipeline = fake_pipeline(runner, openfoam_env(tmp_path, "v2512"))
    result = asyncio.run(pipeline.generate_mesh(tmp_path / "case", config))
    assert result.succeeded, result.issues
    return result, runner


# --- case files -----------------------------------------------------------------

def test_fv_schemes_and_fv_solution_are_written_under_system(tmp_path: Path) -> None:
    files = OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    schemes, solution = system_text(tmp_path, "fvSchemes"), system_text(tmp_path, "fvSolution")
    assert "object      fvSchemes;" in schemes and FOAM_HEADER in schemes
    for section in ("ddtSchemes", "gradSchemes", "divSchemes", "laplacianSchemes",
                    "interpolationSchemes", "snGradSchemes"):
        assert f"{section} {{}}" in schemes
    assert "object      fvSolution;" in solution and FOAM_HEADER in solution
    assert files.fv_schemes_changed and files.fv_solution_changed


def test_fv_files_are_deterministic_and_written_only_once(tmp_path: Path) -> None:
    generator = OpenFOAMMeshCaseGenerator()
    config = build_config(Path("part.stl"))
    generator.generate(tmp_path / "a", config)
    generator.generate(tmp_path / "b", config)

    again = generator.generate(tmp_path / "a", config)

    for name in ("fvSchemes", "fvSolution"):
        assert system_text(tmp_path / "a", name) == system_text(tmp_path / "b", name)
    assert not again.fv_schemes_changed and not again.fv_solution_changed


def test_mesh_quality_dict_has_a_header_and_keeps_its_entries(tmp_path: Path) -> None:
    OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    text = system_text(tmp_path, "meshQualityDict")
    assert FOAM_HEADER in text and "object      meshQualityDict;" in text
    assert text.index("FoamFile") < text.index("maxNonOrtho")
    assert "maxNonOrtho             65.0;" in text
    assert '#include "meshQualityDict"' in system_text(tmp_path, "snappyHexMeshDict")


# --- checkMesh options ------------------------------------------------------------

def test_check_mesh_runs_topology_and_quality_checks_without_all_geometry() -> None:
    argv = check_mesh_step(Path("/case")).argv

    assert argv[:3] == ("checkMesh", "-case", "/case")
    assert "-allTopology" in argv and "-meshQuality" in argv
    assert "-allGeometry" not in argv


# --- cache keys -----------------------------------------------------------------

def test_mesh_inputs_include_fv_files_and_a_change_remeshes(
    tmp_path: Path, cube_stl: Path
) -> None:
    config = build_config(cube_stl)
    run(tmp_path, config)
    manifest = json.loads((tmp_path / "case" / MANIFEST_PATH).read_text("utf-8"))
    inputs = manifest["records"][PipelineOperation.GENERATE_MESH.value]["inputs"]
    assert {"system/fvSchemes", "system/fvSolution"} <= set(inputs)

    # fvSchemes has fixed content, so simulate a mesh that was produced from a
    # different fvSchemes: its recorded input hash no longer matches.
    record = manifest["records"][PipelineOperation.GENERATE_MESH.value]
    record["inputs"]["system/fvSchemes"] = "0" * 64
    (tmp_path / "case" / MANIFEST_PATH).write_text(json.dumps(manifest), "utf-8")

    _, runner = run(tmp_path, config)

    # Re-meshed; the fake produces byte-identical mesh files, so the verified
    # checkMesh result is reused (same rule as test_artifact_cache).
    assert runner.calls == ["blockMesh", "snappyHexMesh"]


def test_check_result_from_other_options_is_not_reused(
    tmp_path: Path, cube_stl: Path
) -> None:
    config = build_config(cube_stl)
    run(tmp_path, config)
    path = tmp_path / "case" / MANIFEST_PATH
    manifest = json.loads(path.read_text("utf-8"))
    record = manifest["records"][PipelineOperation.CHECK_MESH.value]
    assert record["inputs"]["checkMesh_options"] == "-allTopology -meshQuality"

    # A record written before the options were part of the key (an existing
    # project whose cached checkMesh result came from -allGeometry).
    del record["inputs"]["checkMesh_options"]
    path.write_text(json.dumps(manifest), "utf-8")

    _, runner = run(tmp_path, config)

    assert runner.calls == ["checkMesh"]
