from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.services import ProjectService, new_project_template
from core.workflow.dependency_graph import PipelineOperation
from tests.fakes import FakeOpenFOAMRunner, fake_pipeline, openfoam_env
from tests.helpers import build_config


def valid_raw(source: Path) -> dict:
    return build_config(source).model_dump(mode="json")


def test_missing_project_loads_empty(tmp_path: Path) -> None:
    result = ProjectService(tmp_path).load()

    assert (result.raw, result.config, result.issues) == (None, None, ())


def test_corrupt_project_file_is_an_issue(tmp_path: Path) -> None:
    service = ProjectService(tmp_path)
    service.manager.config_path.parent.mkdir(parents=True)
    service.manager.config_path.write_text("{oops", encoding="utf-8")

    result = service.load()

    assert result.config is None
    assert [i.code for i in result.issues] == ["PROJECT_FILE_UNREADABLE"]


def test_invalid_stored_config_keeps_raw_and_reports(tmp_path: Path) -> None:
    service = ProjectService(tmp_path)
    raw = valid_raw(tmp_path / "part.stl")
    del raw["geometry"]["source_units"]  # e.g. a project saved before M2
    service.manager.config_path.parent.mkdir(parents=True)
    service.manager.config_path.write_text(
        json.dumps({"revision": 3, "created_at": "x", "sha256": "x", "config": raw}), "utf-8"
    )

    result = service.load()

    assert result.config is None
    assert result.raw == raw
    assert result.revision == 3
    assert {i.details["field"] for i in result.issues} == {"geometry.source_units"}


def test_template_never_assumes_units_or_domain() -> None:
    template = new_project_template()

    assert template["geometry"]["source_units"] is None
    assert template["mesh"]["location_in_mesh"] == {"x": None, "y": None, "z": None}
    fields = {i.details["field"] for i in ProjectService.validate(template).issues}
    assert "geometry.source_units" in fields
    assert any(f.startswith("mesh.background.domain") for f in fields)


def test_invalid_draft_is_not_saved(tmp_path: Path) -> None:
    service = ProjectService(tmp_path)

    result = service.save(new_project_template())

    assert not result.saved
    assert result.issues
    assert not service.exists()


def test_save_increments_revision(tmp_path: Path) -> None:
    service = ProjectService(tmp_path)
    raw = valid_raw(tmp_path / "part.stl")

    assert service.save(raw).revision == 1
    raw["project_name"] = "Renamed"
    assert service.save(raw).revision == 2
    assert service.load().config.project_name == "Renamed"


def test_preview_full_plan_for_new_project(tmp_path: Path) -> None:
    plan = ProjectService(tmp_path).preview_changes(valid_raw(tmp_path / "part.stl"))

    assert plan is not None
    assert PipelineOperation.GENERATE_MESH in plan.operations


def test_preview_quality_change_only_revalidates(tmp_path: Path) -> None:
    service = ProjectService(tmp_path)
    raw = valid_raw(tmp_path / "part.stl")
    service.save(raw)
    raw["mesh"]["quality"]["max_non_orthogonality"] = 50.0

    plan = service.preview_changes(raw)

    assert plan is not None
    assert plan.operations == (PipelineOperation.VALIDATE_MESH,)


def test_preview_of_invalid_draft_is_none(tmp_path: Path) -> None:
    assert ProjectService(tmp_path).preview_changes(new_project_template()) is None


def test_background_cells(tmp_path: Path) -> None:
    assert ProjectService.background_cells(valid_raw(tmp_path / "p.stl")) == (
        (8, 8, 8), (8, 8, 8),
    )
    assert ProjectService.background_cells(new_project_template()) is None


def test_store_source_file_strips_directories(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "case")

    stored = service.store_source_file("../../evil/part.STL", b"solid x\nendsolid x\n")

    assert stored == (tmp_path / "case" / "inputs" / "part.STL").resolve()
    assert stored.read_bytes().startswith(b"solid")


@pytest.mark.parametrize("name", ["part.obj", ".stl", "notes.txt"])
def test_store_source_file_rejects_non_stl(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        ProjectService(tmp_path).store_source_file(name, b"")


def test_logs_and_safe_log_reading(tmp_path: Path) -> None:
    service = ProjectService(tmp_path)
    assert service.logs() == []

    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "01_blockMesh.log").write_text("x" * 50, encoding="utf-8")
    (tmp_path / "secret.log").write_text("no", encoding="utf-8")

    assert [log.name for log in service.logs()] == ["01_blockMesh.log"]
    assert service.read_log("01_blockMesh.log", max_chars=10) == "x" * 10
    with pytest.raises(ValueError):
        service.read_log("../secret.log")


def test_reports_tolerate_missing_and_corrupt_files(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "mesh_quality_report.json").write_text("{bad", encoding="utf-8")

    assert ProjectService(tmp_path).reports() == {
        "geometry": None, "preflight": None, "mesh_quality": None,
    }


def test_end_to_end_through_service(tmp_path: Path, cube_stl: Path) -> None:
    runner = FakeOpenFOAMRunner()
    service = ProjectService(tmp_path / "case", fake_pipeline(runner, openfoam_env(tmp_path)))
    config = service.save(valid_raw(cube_stl)).config
    assert config is not None

    prepared, assessment, _ = service.preflight(config)
    assert prepared.succeeded and assessment is not None
    assert "blockMeshDict" in service.dictionaries()

    result = service.generate_mesh(config)

    assert result.succeeded
    assert runner.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]
    reports = service.reports()
    assert reports["mesh_quality"]["assessment"]["cfd_accuracy"] == "NOT_ASSESSED"
    assert {log.name for log in service.logs()} >= {"04_checkMesh.log"}
