"""Headless UI tests (streamlit.testing). The UI only presents service state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.services import ProjectService
from tests.helpers import build_config

DASHBOARD = Path(__file__).parents[2] / "app" / "dashboard.py"
TABS = ["Project", "Geometry", "Transform", "Domain", "Mesh", "Refinement",
        "Boundary Layers", "Validate", "Generate", "Results", "Logs"]


def open_app(case: Path) -> AppTest:
    app = AppTest.from_file(str(DASHBOARD), default_timeout=60)
    app.run()
    app.sidebar.text_input[0].set_value(str(case)).run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def button(app: AppTest, label: str):
    return next(b for b in app.button if b.label == label)


def texts(elements) -> str:
    return "\n".join(str(e.value) for e in elements)


@pytest.fixture
def saved_case(tmp_path: Path, cube_stl: Path) -> Path:
    case = tmp_path / "case"
    ProjectService(case).save(build_config(cube_stl).model_dump(mode="json"))
    return case


def test_new_project_renders_all_tabs_and_cannot_be_saved(tmp_path: Path) -> None:
    app = open_app(tmp_path / "new")

    assert [tab.label for tab in app.tabs] == TABS
    assert button(app, "Save configuration").disabled
    assert "geometry.source_units" in texts(app.error)
    units = next(s for s in app.selectbox if s.label.startswith("Units"))
    assert units.value is None  # never defaulted


def test_saved_project_loads_without_errors(saved_case: Path) -> None:
    app = open_app(saved_case)

    assert "Saved revision 1" in texts(app.success)
    assert not app.error
    assert not button(app, "Save configuration").disabled
    assert "Generate mesh" in [b.label for b in app.button]


def test_editing_and_saving_creates_new_revision(saved_case: Path) -> None:
    app = open_app(saved_case)

    next(t for t in app.text_input if t.label == "Project name").set_value("Wing").run()
    assert "Unsaved changes." in texts(app.warning)
    button(app, "Save configuration").click().run()

    assert not app.exception
    assert ProjectService(saved_case).load().config.project_name == "Wing"
    assert ProjectService(saved_case).load().revision == 2


def test_quality_limit_change_previews_validation_only(saved_case: Path) -> None:
    app = open_app(saved_case)

    field = next(n for n in app.number_input if n.label == "Max non-orthogonality (°)")
    field.set_value(50.0).run()

    preview = texts(app.markdown)
    assert "Mesh validation" in preview
    assert "Mesh (snappyHexMesh)" not in preview


def test_invalid_stored_project_shows_structured_issue(saved_case: Path) -> None:
    path = saved_case / ".preprocessor" / "project.json"
    payload = json.loads(path.read_text("utf-8"))
    del payload["config"]["geometry"]["source_units"]
    path.write_text(json.dumps(payload), "utf-8")

    app = open_app(saved_case)

    assert "geometry.source_units" in texts(app.error)
    assert "Save a valid configuration" in texts(app.info)


def test_logs_tab_handles_empty_and_existing_logs(saved_case: Path) -> None:
    (saved_case / "logs").mkdir()
    app = open_app(saved_case)
    assert "No logs yet." in texts(app.info)

    (saved_case / "logs" / "01_blockMesh.log").write_text("blockMesh End\n", "utf-8")
    app = open_app(saved_case)

    assert "blockMesh End" in texts(app.code)


def test_prepare_case_button_runs_backend(saved_case: Path) -> None:
    app = open_app(saved_case)

    button(app, "Prepare case (geometry + dictionaries)").click().run()

    assert not app.exception
    assert "Case files are current" in texts(app.success)
    assert (saved_case / "system" / "blockMeshDict").is_file()


def test_geometry_view_renders_on_request(saved_case: Path) -> None:
    app = open_app(saved_case)
    button(app, "Prepare case (geometry + dictionaries)").click().run()

    button(app, "Show geometry and domain").click().run()

    assert not app.exception
    charts = app.get("plotly_chart")
    assert len(charts) == 1


def test_mesh_view_without_mesh_explains_instead_of_failing(saved_case: Path) -> None:
    app = open_app(saved_case)

    button(app, "Show mesh and problem locations").click().run()

    assert not app.exception
    assert "The mesh cannot be displayed" in texts(app.info)


def test_mesh_view_renders_existing_mesh(saved_case: Path) -> None:
    from tests.foam_fixtures import write_poly_mesh, write_set

    write_set(write_poly_mesh(saved_case), "skewFaces", "faceSet", [0])
    app = open_app(saved_case)

    button(app, "Show mesh and problem locations").click().run()

    assert not app.exception
    assert len(app.get("plotly_chart")) == 1
