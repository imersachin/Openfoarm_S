"""M1c on real OpenFOAM v2512 through the machines UI (in-process AppTest, the
real MachineService): an imported rotating mesh chosen by path, run, its
mesh and case results, and the check over one revolution, whose WARNING the
browser checklist also asks for (the fixture reaches 1.104).

Skipped unless the OpenFOAM tools are on PATH.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.machines_ui import preview, state
from machines.msh_import.pipeline import ASSEMBLE_EXECUTABLES, CHECK_EXECUTABLES
from machines.service import MachineService
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from tests.unit.ui_machines.conftest import create, go, ok, open_app, texts
from vawt.service import RunView

TOOLS = ("gmshToFoam", "topoSet", "surfaceMeshExtract", *ASSEMBLE_EXECUTABLES,
         *CHECK_EXECUTABLES, "moveDynamicMesh")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


@pytest.fixture
def projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "projects"
    monkeypatch.setenv("MACHINES_PROJECTS_DIR", str(root))
    monkeypatch.setattr(state, "SERVICE_FACTORY", MachineService)
    monkeypatch.setenv("HOME", str(tmp_path))  # the picker's WSL root, the app settings
    state._service.clear()
    preview._figure.clear()
    yield root
    state._service.clear()
    preview._figure.clear()


def test_an_imported_mesh_through_the_ui(projects: Path, tmp_path: Path) -> None:
    path = tmp_path / "inputs" / "rotating_41.msh"
    path.parent.mkdir()
    path.write_bytes((M1 / "rotating_41.msh").read_bytes())
    app = create(open_app(None), "custom")  # type: ignore[arg-type]
    go(app, "project")
    ok(app.button(key="machines_mesh_source").click().run())
    go(app, "imported_mesh")
    app.text_input(key="machines_disk_typed").set_value(str(path)).run()  # the picker (M2a)
    ok(app.button(key="machines_disk_go").click().run())
    data = configs.rotating(path)
    data.pop("machine")
    app.session_state["machines_draft"] = {**app.session_state["machines_draft"], **data}
    app.session_state["draft_generation"] = app.session_state["draft_generation"] + 1
    ok(app.run())
    ok(app.button(key="machines_save").click().run())
    go(app, "run")
    ok(app.button(key="machines_start").click().run())
    service = MachineService(projects / "demo")
    deadline = time.monotonic() + 600
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert service.run_status().state is RunView.SUCCEEDED

    go(app, "mesh")
    shown = texts(app)
    assert "Mesh: SUCCESS" in shown and "with gmshToFoam on OpenFOAM v2512" in shown
    assert "at rest (0 deg): sum(weights)" in shown
    ok(app.button(key="machines_ami_revolution").click().run())
    shown = texts(app)
    assert "over one revolution (36 x 10 deg): sum(weights) 0.9946–1.104" in shown
    warnings = "\n".join(str(w.value) for w in app.warning)
    assert "over one revolution (36 x 10 deg) spans" in warnings and "0.95-1.05" in warnings
    go(app, "case")
    assert "Case setup: SUCCESS" in texts(app)
    assert (projects / "demo" / "case" / "constant" / "dynamicMeshDict").is_file()
    # The rotor block (x, z +-0.05, y +-0.4) about the x axis: the checklist's A13.
    measured = service.measured_tip_radius(app.session_state["machines_draft"], "rotor_zone")
    assert measured is not None and measured.source == "from rotor patch rotor"
    assert measured.radius == pytest.approx((0.4**2 + 0.05**2) ** 0.5, rel=1e-6)

