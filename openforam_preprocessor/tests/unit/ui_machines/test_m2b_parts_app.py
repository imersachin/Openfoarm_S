"""M2b-1: the Imported mesh section for a machine of several .msh files
(headless, fake OpenFOAM).

The mesh source offered beside the single file (Francis and Custom), the
parts added from a folder at once, a part's role and prefix, its units, "Read
the parts" and the passage count confirmed by a button (the fake's meshes
are boxes: a whole ring), the rotating part, and the patch table listing the
parts' patches under their prefixes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from streamlit.testing.v1 import AppTest

from tests.unit.mesh_import.test_m2b_parts import M2B
from tests.unit.ui_machines.conftest import create, go, nav, ok, open_app, texts
from tests.unit.ui_machines.test_m1c_app import MshUi, draft, msh, set_draft

__all__ = ["msh"]  # the fixture
NAMES = ("casing.msh", "vanes.msh", "runner.msh")


def folder_with_parts(msh: MshUi) -> Path:
    folder = msh.tmp / "meshes" / "Machine"
    folder.mkdir(parents=True)
    for name in NAMES:
        (folder / name).write_bytes((M2B / name).read_bytes())
    return folder


def to_parts(app: AppTest) -> AppTest:
    go(app, "project")
    return ok(app.button(key="machines_mesh_source_PARTS").click().run())


def with_parts(msh: MshUi, profile: str = "francis") -> tuple[AppTest, Path]:
    folder = folder_with_parts(msh)
    app = to_parts(create(open_app(msh), profile))
    go(app, "imported_mesh")
    app.text_input(key="machines_disk_typed").set_value(str(folder)).run()
    ok(app.button(key="machines_disk_go").click().run())
    ok(app.button(key="machines_disk_add_all").click().run())
    return app, folder


def mesh(app: AppTest) -> dict[str, Any]:
    found: dict[str, Any] = draft(app)["imported_mesh"]
    return found


def test_several_files_are_a_mesh_source_of_francis_and_custom(msh: MshUi) -> None:
    for profile in ("francis", "custom"):
        app = to_parts(create(open_app(msh), profile, name=f"p{profile}"))
        assert mesh(app) == {"parts": [], "zones": [], "ami_pairs": []}
        assert nav(app)[:2] == ["Project", "Imported mesh"]
        go(app, "imported_mesh")
        assert "Each file is one part" in texts(app)


def test_a_folder_of_parts_is_added_at_once(msh: MshUi) -> None:
    app, folder = with_parts(msh)
    assert [p["source_path"] for p in mesh(app)["parts"]] == [str(folder / n)
                                                               for n in sorted(NAMES)]
    assert all(len(p["sha256"]) == 64 for p in mesh(app)["parts"])
    assert not any(p.get("prefix") or p.get("source_units") for p in mesh(app)["parts"])
    ok(app.button(key="machines_disk_add_all").click().run())  # each file once
    assert len(mesh(app)["parts"]) == 3


def test_a_role_sets_the_part_name_and_prefix(msh: MshUi) -> None:
    app, _ = with_parts(msh)
    ok(app.selectbox(key="machines_part_role_0").set_value("RU").run())
    assert {"name": "Runner", "prefix": "RU"}.items() <= mesh(app)["parts"][0].items()
    assert "Runner** (`RU`)" in texts(app)


def test_parts_are_read_then_their_passages_confirmed(msh: MshUi) -> None:
    app, _ = with_parts(msh)
    for index, role in enumerate(("SC", "RU", "GV")):  # casing, runner, vanes (sorted)
        ok(app.selectbox(key=f"machines_part_role_{index}").set_value(role).run())
    assert "3 part(s) to read" in texts(app)
    assert "the machine axis is not set" not in texts(app)
    ok(app.button(key="machines_parts_read").click().run())
    assert "Every part with a prefix has been read" in texts(app)
    assert "the machine axis is not set" in texts(app)
    set_draft(app, {"imported_mesh": {**mesh(app), "axis": "z",
                                      "origin": {"x": 0.0, "y": 0.0, "z": 0.0}}})
    assert texts(app).count("a whole ring: 360 deg, no periodic pair") == 3  # boxes
    assert mesh(app)["parts"][0].get("passages") is None  # never applied by itself
    ok(app.button(key="machines_part_confirm_0").click().run())
    assert {"passages": 1, "periodic": []}.items() <= mesh(app)["parts"][0].items()
    assert "Confirmed: a whole ring." in texts(app)


def test_the_rotating_part_is_asked(msh: MshUi) -> None:
    app, _ = with_parts(msh)
    ok(app.selectbox(key="machines_part_role_1").set_value("RU").run())
    assert mesh(app)["zones"] == []
    ok(app.selectbox(key="machines_parts_rotating").set_value("RU").run())
    assert mesh(app)["zones"] == [{"name": "RU", "cell_zone": "RU", "axis": None,
                                   "origin": None}]


def test_the_patch_table_lists_the_parts_patches_under_their_prefixes(msh: MshUi) -> None:
    app, _ = with_parts(msh)
    for index, role in enumerate(("SC", "RU", "GV")):
        ok(app.selectbox(key=f"machines_part_role_{index}").set_value(role).run())
    go(app, "patches")
    ok(app.button(key="machines_add_patches").click().run())
    refs = [p["source"]["ref"] for p in draft(app)["patches"]]
    assert {"SC_inlet", "RU_outlet", "GV_vane_in", "RU_bottom", "SC_bottom"} <= set(refs)
    assert "GV_bottom" in refs and len(refs) == len(set(refs)) == 4 + 6 + 6
    assert {p["source"]["kind"] for p in draft(app)["patches"]} == {"MESH_PATCH"}
