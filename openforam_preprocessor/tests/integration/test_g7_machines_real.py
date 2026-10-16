"""G7 on real OpenFOAM v2512: the machines UI's service and app.

- HAWT through MachineService with physics: a background run meshes and sets
  up the case; the two results are reported apart; the 3D pane draws the real
  mesh with the rotation arrow from the physics; the folder-only mesh checks
  pass on the merged mesh (AMI pairs from the boundary file alone).
- Francis from the five named slots (stay vanes optional), typed, with an
  operating point (Q, N, outlet pressure): mesh and case setup.
- The machines app under a real `streamlit run`: open the project, every
  section renders without an exception.

    pytest -m openfoam tests/integration/test_g7_machines_real.py -v -s
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from machines.operations import MACHINE_EXECUTABLES
from machines.profiles import default_registry
from machines.service import MachineService
from tests.fixtures.machines import configs
from tests.fixtures.machines.g0.common import make_geometry
from tests.integration.test_case_setup_real import SMOKE_TIME
from tests.integration.test_francis_pipeline_real import TYPES
from tests.integration.test_streamlit_run_real import Browser, Server
from vawt.runtime import RunRegistry
from vawt.service import PreviewView, RunView

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in MACHINE_EXECUTABLES),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


def finish(service: MachineService, timeout: float = 900.0) -> tuple[RunView, float]:
    start = time.monotonic()
    started = service.start_run()
    assert started.started, [(i.code, i.message) for i in started.issues]
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() - start < timeout
        time.sleep(0.5)
    return service.run_status().state, time.monotonic() - start


def hawt_physics() -> dict[str, Any]:
    return {"fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
            "turbulence": {"length_scale": 0.1},
            "rotation": {"rotating": {"speed": {"kind": "TIP_SPEED_RATIO",
                                                "tip_speed_ratio": 6.0, "radius": 0.5},
                                      "direction": "POSITIVE"}},
            "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0.0, 0.0)}},
            "outlets": {"outlet": {}}, "time": SMOKE_TIME}


def test_hawt_mesh_and_case_setup_through_the_service(tmp_path: Path) -> None:
    root = tmp_path / "projects" / "hawt"
    service = MachineService(root, registry=RunRegistry())
    service.choose_profile("hawt")
    draft = {**configs.hawt(tmp_path / "geometry"), "physics": hawt_physics()}
    assert service.save(draft).saved

    state, seconds = finish(service)
    assert state is RunView.SUCCEEDED, service.run_status().issues
    stages = service.last_stages()
    assert stages["mesh"]["status"] == "SUCCESS"
    assert stages["case_setup"]["status"] == "SUCCESS"
    reports = service.reports()
    assert reports["mesh"]["assessment"]["mesh_validity"] == "VALID"
    assert reports["case_setup"]["rotation"]["omega_rad_s"] == pytest.approx(60.0)
    assert {"case/0/U", "case/constant/dynamicMeshDict", "case/system/controlDict"} <= set(
        service.case_files())

    mesh = service.preview(PreviewView.MESH)
    assert mesh.scene is not None and mesh.scene.surfaces and len(mesh.scene.lines) == 2
    rotation = next(a for a in mesh.arrows if a.kind == "rotation")
    assert rotation.defined and rotation.label.startswith("rotating: positive about +x")

    # The folder-only checks on the merged mesh. Its checkMesh output is in the
    # project's logs/, not in the folder: without it quality is not assessed.
    merged = root / "cases" / "merged"
    bare = service.check_mesh_folder(merged)
    assert [i.code for i in bare.issues] == ["CHECK_MESH_OUTPUT_MISSING"]
    from machines.mesh_folder import check_mesh_folder

    saved = service.saved_config()
    assert saved is not None
    log = (root / "logs" / "merged" / "checkMesh.log").read_text(encoding="utf-8")
    report = check_mesh_folder(merged, limits=saved.quality, check_mesh_log=log)
    assert report.ami_pairs == (("rotating_outer_rot", "rotating_outer_stat"),)
    assert report.regions == 2 and report.cell_zones == ("rotating",)
    assert report.quality is not None and report.quality.validity.value == "VALID"
    assert not [i for i in report.issues if i.code.startswith("AMI_")]
    assert report.patches == bare.patches
    print(f"\nG7 HAWT through MachineService: mesh and case setup {seconds:.1f} s; "
          f"{sum(s.total for s in mesh.scene.surfaces):,} boundary faces drawn")


def test_francis_from_named_slots_with_an_operating_point(tmp_path: Path) -> None:
    geometry = tmp_path / "geometry"
    geometry.mkdir()
    make_geometry.francis(geometry)
    root = tmp_path / "projects" / "francis"
    service = MachineService(root, registry=RunRegistry())
    profile = service.choose_profile("francis")
    files = {"spiral_casing": "casing", "guide_vanes": "guide", "runner": "runner",
             "draft_tube": "draft"}
    result = service.draft_from_preset(profile, {
        "project_name": "Francis", "axis": "z", "origin": configs.vec(0, 0, 0),
        "diameter": 0.6, "slots": {slot: {"source_path": str(geometry / f"{name}.stl"),
                                          "source_units": "m"}
                                   for slot, name in files.items()}})
    assert result.draft is not None, result.issues
    draft = result.draft
    for patch in draft["patches"]:
        patch["type"] = TYPES[patch["name"]]
    discharge = 0.28  # about 1 m/s through the G0 R4 inlet (G6, P4)
    draft["physics"] = {
        "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
        "turbulence": {"length_scale": 0.03},
        "rotation": {"runner": {"speed": {"kind": "RPM", "rpm": 20 * 60 / (2 * math.pi)},
                                "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": discharge}},
        "outlets": {"outlet": {}}, "time": SMOKE_TIME}
    analysis = service.analyse(draft, profile)
    assert analysis.outcome.can_run, [(i.code, i.message) for i in analysis.outcome.issues
                                      if i.is_stopping]
    assert service.save(draft).saved

    state, seconds = finish(service)
    assert state is RunView.SUCCEEDED, service.run_status().issues
    stages = service.last_stages()
    assert (stages["mesh"]["status"], stages["case_setup"]["status"]) == ("SUCCESS", "SUCCESS")
    report = service.reports()["case_setup"]
    assert report["rotation"]["omega_rad_s"] == pytest.approx(20.0)
    assert report["inlets"]["inlet"]["flow_rate"] == pytest.approx(discharge)
    from machines.mesh_folder import ami_pairs

    assert len(ami_pairs(root / "cases" / "merged")) == 3  # the three joints
    print(f"\nG7 Francis from slots: mesh and case setup {seconds:.1f} s")


def test_the_machines_app_under_streamlit_run(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    server = Server(tmp_path)
    monkeypatch.setenv("MACHINES_PROJECTS_DIR", str(server.projects))
    root = server.projects / "hawt"
    service = MachineService(root)
    service.choose_profile("hawt")
    assert service.save({**configs.hawt(tmp_path / "geometry"), "physics": hawt_physics()}).saved
    try:
        server.start("app/machines_app.py")
        with server.connect() as ws:
            browser = Browser(ws)
            start = browser.run()
            assert start.exceptions == []
            assert "Rotating machines: mesh and case setup" in start.text()
            folder = start.widget_id("text_input", "Project folder (inside WSL)")
            browser.run(text={folder: str(root)})
            page = browser.run(click=start.widget_id("button", "Open folder"))
            assert page.exceptions == []
            profile = default_registry().get("hawt")
            assert profile is not None
            for spec in profile.sections:
                nav = next(i for t, i, _ in page.elements if t == "button"
                           and i.endswith(f"-nav-{spec.key}"))
                page = browser.run(click=nav)
                assert page.exceptions == [], spec.key
                assert nav in page.primary, spec.key
    finally:
        server.stop()
    assert "Traceback" not in server.output(), server.output()
