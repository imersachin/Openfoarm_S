from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.issues import IssueCategory
from core.workflow.pipeline import MeshPipeline
from openfoam.runner import OpenFOAMRunner
from tests.helpers import build_config


class RecordingRunner(OpenFOAMRunner):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[object] = []

    async def run_meshing_pipeline(self, **kwargs):  # type: ignore[override]
        self.calls.append(kwargs)
        return []


def test_missing_source_is_reported_not_raised(tmp_path: Path) -> None:
    config = build_config(tmp_path / "missing.stl")

    result = MeshPipeline().prepare_case(tmp_path / "case", config)

    assert not result.succeeded
    assert [i.code for i in result.issues] == ["GEOMETRY_SOURCE_MISSING"]
    report = json.loads(result.geometry_report_path.read_text(encoding="utf-8"))
    assert report["source"]["issues"][0]["severity"] == "ERROR"
    assert not (tmp_path / "case" / "system").exists()


def test_valid_geometry_generates_case_under_system(tmp_path: Path, cube_stl: Path) -> None:
    case = tmp_path / "case"

    result = MeshPipeline().prepare_case(case, build_config(cube_stl))

    assert result.succeeded
    assert (case / "constant" / "triSurface" / "part.stl").is_file()
    assert (case / "system" / "blockMeshDict").is_file()
    assert (case / "system" / "controlDict").is_file()


def test_required_feature_extraction_without_command_blocks_meshing(
    tmp_path: Path, cube_stl: Path
) -> None:
    runner = RecordingRunner()
    config = build_config(cube_stl, extract_features=True)

    result = asyncio.run(MeshPipeline(runner).generate_mesh(tmp_path / "case", config, None))

    assert not result.succeeded
    assert runner.calls == []
    issue = result.issues[-1]
    assert issue.code == "FEATURE_EXTRACTION_UNAVAILABLE"
    assert issue.category is IssueCategory.DEPENDENCY


def test_feature_command_is_not_run_when_extraction_disabled(
    tmp_path: Path, cube_stl: Path
) -> None:
    runner = RecordingRunner()

    asyncio.run(MeshPipeline(runner).generate_mesh(
        tmp_path / "case", build_config(cube_stl), ("surfaceFeatureExtract",)
    ))

    assert runner.calls[0]["extract_features_argv"] is None  # type: ignore[index]


def test_feature_command_is_run_when_extraction_enabled(tmp_path: Path, cube_stl: Path) -> None:
    runner = RecordingRunner()
    config = build_config(cube_stl, extract_features=True)

    asyncio.run(MeshPipeline(runner).generate_mesh(
        tmp_path / "case", config, ("surfaceFeatureExtract",)
    ))

    assert runner.calls[0]["extract_features_argv"] == ("surfaceFeatureExtract",)  # type: ignore[index]
