"""The notes under the uploaders (owner): which file goes where, as captions.

Machines app: the Imported mesh uploader has the .msh note; each STL uploader
has the STL note (one note per uploader). VAWT app: the rotor uploader points
to the Machines app.
"""

from __future__ import annotations

from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from app.components.upload_notes import MSH_UPLOAD_NOTE, STL_UPLOAD_NOTE, VAWT_UPLOAD_NOTE
from tests.unit.ui_machines.conftest import MachinesUi, create, go, ok, open_app
from tests.unit.vawt import ui_helpers as vawt_ui
from tests.unit.vawt.ui_helpers import UiProject


def captions(app: AppTest) -> list[str]:
    return [str(c.value) for c in app.caption]


def uploaders(app: AppTest) -> int:
    return len(list(app.get("file_uploader")))


def test_the_imported_mesh_uploader_has_the_msh_note(ui: MachinesUi) -> None:
    app = create(open_app(ui), "francis")
    go(app, "project")
    ok(app.button(key="machines_mesh_source").click().run())
    go(app, "imported_mesh")

    assert uploaders(app) == 1
    assert captions(app).count(MSH_UPLOAD_NOTE) == 1
    assert STL_UPLOAD_NOTE not in captions(app)


def imported_domain(app: AppTest) -> None:
    app.selectbox(key="machines_domain_kind").set_value("IMPORTED").run()
    ok(app.button(key="machines_set_domain").click().run())


@pytest.mark.parametrize(("profile_id", "section", "setup"), [
    ("hawt", "bodies", None),  # the rotor
    ("francis", "parts", None),  # the water passage slots
    ("custom", "bodies", None),  # add a body
    ("custom", "zones", None),  # an imported zone surface
    ("custom", "domain", imported_domain),  # imported parts
])
def test_each_stl_uploader_has_the_stl_note(ui: MachinesUi, profile_id: str, section: str,
                                            setup: Any) -> None:
    app = create(open_app(ui), profile_id)
    go(app, section)
    if setup is not None:
        setup(app)

    assert uploaders(app) >= 1
    assert captions(app).count(STL_UPLOAD_NOTE) == uploaders(app)
    assert MSH_UPLOAD_NOTE not in captions(app)


@pytest.fixture
def vawt(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.vawt_ui import state
    project = UiProject(tmp_path, monkeypatch)
    yield project
    project.registry.shutdown(timeout=10)
    state._service.clear()


def test_the_vawt_uploader_points_to_the_machines_app(vawt: UiProject) -> None:
    vawt.save()
    app = vawt_ui.go(vawt_ui.open_app(vawt), "geometry")

    assert uploaders(app) == 1
    assert captions(app).count(VAWT_UPLOAD_NOTE) == 1
    assert "http://localhost:8502" in VAWT_UPLOAD_NOTE
