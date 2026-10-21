"""M2a commit 3: the file picker of the Imported mesh section (headless).

A folder under the WSL home lists its .msh files with the header summary; a
file inside WSL is used where it is; a file on a Windows drive is copied into
the project first and used when the copy is checked; the remembered mesh
folder is listed first.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from machines import disk
from tests.fixtures.msh.configs import M1
from tests.unit.ui_machines.conftest import create, go, ok, open_app, texts
from tests.unit.ui_machines.test_m1c_app import MshUi, draft, msh, to_import

__all__ = ["msh"]  # the fixture


def folder_with_meshes(msh: MshUi) -> Path:
    folder = msh.tmp / "meshes" / "Francis"
    folder.mkdir(parents=True)
    for name in ("rotating_41.msh", "fluent_box.msh"):
        (folder / name).write_bytes((M1 / name).read_bytes())
    return folder


def open_folder(app: AppTest, folder: Path) -> AppTest:
    go(app, "imported_mesh")
    app.text_input(key="machines_disk_typed").set_value(str(folder)).run()
    return ok(app.button(key="machines_disk_go").click().run())


def test_a_folder_lists_its_meshes_with_their_summary(msh: MshUi) -> None:
    folder = folder_with_meshes(msh)
    app = open_folder(to_import(create(open_app(msh), "custom")), folder)
    shown = texts(app)
    assert "2 .msh file(s)." in shown
    assert "`fluent_box.msh`" in shown and "Fluent (ASCII)" in shown
    assert "Gmsh (ASCII), 2 cell zone(s)" in shown


def test_a_file_in_wsl_is_used_where_it_is(msh: MshUi) -> None:
    folder = folder_with_meshes(msh)
    app = open_folder(to_import(create(open_app(msh), "custom")), folder)
    ok(app.button(key=f"machines_disk_use_{folder / 'rotating_41.msh'}").click().run())
    assert draft(app)["imported_mesh"]["source_path"] == str(folder / "rotating_41.msh")


def test_a_folder_outside_the_roots_is_refused(msh: MshUi, tmp_path: Path) -> None:
    app = open_folder(to_import(create(open_app(msh), "custom")), Path("/proc"))
    assert any("is not a folder inside" in w.value for w in app.warning)


def test_the_remembered_folder_is_listed_first(msh: MshUi) -> None:
    folder = folder_with_meshes(msh)
    app = open_folder(to_import(create(open_app(msh), "custom")), folder)
    ok(app.button(key="machines_disk_remember").click().run())
    assert disk.read_settings()["mesh_folder"] == str(folder)
    del app.session_state["machines_disk_folder"]  # as a new session
    ok(app.run())
    assert any(f"`{folder}`" in m.value for m in app.markdown)


def test_a_file_on_a_windows_drive_is_copied_then_used(
        msh: MshUi, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = folder_with_meshes(msh)
    monkeypatch.setattr(disk, "on_windows_drive", lambda path: "Francis" in str(path))
    app = open_folder(to_import(create(open_app(msh), "custom")), folder)
    button = app.button(key=f"machines_disk_use_{folder / 'rotating_41.msh'}")
    assert button.label == "Copy and use"
    ok(button.click().run())
    service = msh.service("demo")
    target = service.root / "inputs" / "mesh" / "rotating_41.msh"
    disk.JOBS.wait(target, 10)
    deadline = time.monotonic() + 10
    mesh: dict[str, Any] = {}
    while time.monotonic() < deadline:
        ok(app.run())
        mesh = draft(app)["imported_mesh"]
        if mesh.get("source_path") == str(target):
            break
    assert mesh.get("source_path") == str(target) and target.is_file()
    assert mesh.get("sha256") == service.inspect_mesh(target).sha256
