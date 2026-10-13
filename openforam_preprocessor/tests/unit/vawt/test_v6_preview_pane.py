"""V6: the 3D pane of the VAWT app and the service calls behind it.

The pane shows the draft (Setup) and the last mesh (Mesh). A rerun with the
same preview key reads no STL or mesh file and calls no preview build; a new
session loads the stored display file only (spec 14.2, last row)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.vawt_ui import preview
from machines import previews
from tests.foam_block import write_block_mesh, write_set
from tests.unit.vawt.ui_helpers import UiProject, go, open_app, texts
from vawt.case_generator import case_layout
from vawt.project_store import write_last_meshed
from vawt.service import PreviewView, VawtService


@pytest.fixture(autouse=True)
def _fresh_figure_cache() -> None:
    preview._figure.clear()


def charts(app: Any) -> list[Any]:
    return list(app.get("plotly_chart"))


def mesh_project(ui: UiProject, **block: Any) -> Path:
    """A saved project with a meshed final case (a block mesh standing in)."""
    ui.save()
    config = ui.service().saved_config()
    assert config is not None
    case = ui.root / "cases" / case_layout(config).final
    poly = write_block_mesh(case, **block)
    write_last_meshed(ui.root, "run-1", config, "passed")
    return poly


class Calls:
    """Counts preview builds, stored-scene loads and mesh or STL reads."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        for module, name in ((VawtService, "preview"), (previews, "build_setup_scene"),
                             (previews, "build_mesh_scene"), (previews, "read_points"),
                             (previews, "load_bodies"), (previews.PreviewStore, "_load")):
            self._spy(monkeypatch, module, name)

    def _spy(self, monkeypatch: pytest.MonkeyPatch, owner: Any, name: str) -> None:
        original = getattr(owner, name)

        def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, spy)


# --- the pane -----------------------------------------------------------------------------

def test_setup_view_draws_the_draft(ui: UiProject) -> None:
    ui.save()
    app = open_app(ui)
    (chart,) = charts(app)
    shown = texts(app)
    assert "3D preview" in shown and "right-hand rule" in shown
    assert app.radio(key=preview.VIEW_KEY).value is PreviewView.SETUP
    assert '"rotor' in chart.proto.spec  # the rotor's patch trace is in the figure
    assert "flow along +x (flow axis)" in chart.proto.spec


def test_rerun_reads_no_geometry_and_builds_nothing(ui: UiProject,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    ui.save()
    app = open_app(ui)
    calls = Calls(monkeypatch)
    go(app, "domain")
    go(app, "rotating_zone")
    assert calls.calls == []
    assert len(charts(app)) == 1


def test_mesh_view_without_a_mesh_says_so(ui: UiProject) -> None:
    ui.save()
    app = open_app(ui)
    app.radio(key=preview.VIEW_KEY).set_value(PreviewView.MESH).run()
    assert not app.exception
    assert charts(app) == []
    assert "No mesh has been built for this project yet." in texts(app)


def test_mesh_view_draws_the_last_mesh_and_rerun_reads_nothing(
        ui: UiProject, monkeypatch: pytest.MonkeyPatch) -> None:
    poly = mesh_project(ui, n=(6, 4, 4), origin=(-3.0, -2.0, -2.0))
    write_set(poly, "region0", "cellSet", list(range(96)))
    write_set(poly, "skewFaces", "faceSet", [0, 1])
    app = open_app(ui)
    app.radio(key=preview.VIEW_KEY).set_value(PreviewView.MESH).run()
    assert not app.exception, [e.value for e in app.exception]
    (chart,) = charts(app)
    assert "checkMesh: skewFaces" in chart.proto.spec
    assert "cut along the flow" in chart.proto.spec
    assert "not problems" in texts(app)  # the region set is explained, not drawn

    calls = Calls(monkeypatch)
    go(app, "domain")
    go(app, "layers")
    assert calls.calls == []
    assert len(charts(app)) == 1


def test_a_new_session_loads_the_stored_file_only(ui: UiProject,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    mesh_project(ui, n=(6, 4, 4), origin=(-3.0, -2.0, -2.0))
    first = open_app(ui)
    first.radio(key=preview.VIEW_KEY).set_value(PreviewView.MESH).run()
    preview._figure.clear()  # as in a new server process
    calls = Calls(monkeypatch)
    second = open_app(ui)
    second.radio(key=preview.VIEW_KEY).set_value(PreviewView.MESH).run()
    assert len(charts(second)) == 1
    assert "build_mesh_scene" not in calls.calls and "read_points" not in calls.calls
    assert "build_setup_scene" not in calls.calls and "load_bodies" not in calls.calls


def test_incomplete_draft_shows_why(ui: UiProject) -> None:
    app = open_app(ui)  # nothing saved: a new, incomplete draft
    assert charts(app) == []
    assert "The setup is shown once the configuration can be read." in texts(app)


# --- the service --------------------------------------------------------------------------

def test_preview_key_follows_the_draft_and_the_files(ui: UiProject) -> None:
    ui.save()
    service = ui.service()
    key = service.preview_key(PreviewView.SETUP, ui.draft)
    assert key.signature is not None
    assert service.preview_key(PreviewView.SETUP, ui.draft) == key

    geometry = dict(ui.draft["geometry"], translation={"x": 0.1, "y": 0.0, "z": 0.0})
    shifted = service.preview_key(PreviewView.SETUP, {**ui.draft, "geometry": geometry})
    assert shifted.signature != key.signature


def test_preview_built_once_then_loaded(ui: UiProject) -> None:
    mesh_project(ui, n=(6, 4, 4), origin=(-3.0, -2.0, -2.0))
    first = ui.service().preview(PreviewView.MESH)
    second = ui.service().preview(PreviewView.MESH)
    assert (first.built, second.built) == (True, False)
    assert first.scene is not None and second.scene is not None
    assert [s.name for s in second.scene.surfaces] == [s.name for s in first.scene.surfaces]
    assert {a.kind for a in first.arrows} == {"axis", "rotation", "flow"}


def test_mesh_from_an_earlier_configuration_is_noted(ui: UiProject) -> None:
    mesh_project(ui)
    ui.save({**ui.draft, "max_global_cells": 123_456})
    data = ui.service().preview(PreviewView.MESH)
    assert data.notes[0].startswith("This mesh was built from an earlier configuration")


def test_preview_limits_are_configurable(ui: UiProject) -> None:
    mesh_project(ui, n=(6, 4, 4), origin=(-3.0, -2.0, -2.0))
    service = ui.service()
    service.preview_limits = previews.PreviewLimits(max_faces=20, min_faces_per_patch=4)
    data = service.preview(PreviewView.MESH)
    assert data.scene is not None
    assert any(n.startswith("Showing ") and "boundary faces" in n for n in data.notes)
