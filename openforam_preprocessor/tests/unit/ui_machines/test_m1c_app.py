"""M1c: the imported-mesh screens of the machines UI (headless, fake OpenFOAM).

Mesh source per machine (HAWT, Francis, Custom; not VAWT), the Imported mesh
section (file by path, what was detected, the box and units, the cell zone,
the AMI pair, names used), the patch table from the mesh, a run with mesh and
case results apart, the check over one revolution, the bad files' rules, and
the measured tip radius offered by a button.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh
from streamlit.testing.v1 import AppTest

from case_setup.setup import _zone_origin
from machines.config import MachineProjectConfig
from machines.msh_import.detect import inspect_mesh_file
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT_EXECUTABLES,
)
from machines.operations import MACHINE_EXECUTABLES
from machines.pipeline import MachinePipeline
from machines.service import MachineService
from tests.fakes import openfoam_env
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from tests.fixtures.msh.m1.make_bad_files import BAD, BAD_DIR, OTHER
from tests.unit.ui_machines.conftest import MachinesUi, create, go, nav, ok, open_app, texts
from vawt.service import RunView
from vawt.workspace import read_mounts

MSH_TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES, "moveDynamicMesh")
STL_ONLY = ("Rotor", "Rotating zone", "Domain", "Refinement", "Boundary layers")


class MshUi(MachinesUi):
    """MachinesUi with the .msh tools and FakeMshRunner (options: `runner`)."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(tmp_path, monkeypatch)
        self.env = {**openfoam_env(tmp_path, "v2512", (*MACHINE_EXECUTABLES, *MSH_TOOLS)),
                    "MACHINES_PROJECTS_DIR": str(self.projects)}
        for key, value in self.env.items():
            monkeypatch.setenv(key, value)
        self.runner: dict[str, Any] = {}

    def make_service(self, root: Path) -> MachineService:
        pipeline = MachinePipeline(FakeMshRunner(**self.runner), environment=self.env,
                                   system_probe=lambda _: self.resources)
        return MachineService(root, pipeline=pipeline, registry=self.registry, env=self.env,
                              mounts=read_mounts("/dev/sdd / ext4 rw 0 0\n"),
                              system_probe=lambda _: self.resources, check_cache=self.checks,
                              profiles=self.profiles)


@pytest.fixture
def msh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.machines_ui import preview, state
    project = MshUi(tmp_path, monkeypatch)
    yield project
    project.registry.shutdown(timeout=10)
    state._service.clear()
    preview._figure.clear()


def to_import(app: AppTest) -> AppTest:
    go(app, "project")
    return ok(app.button(key="machines_mesh_source").click().run())


def use_path(app: AppTest, path: Path) -> AppTest:
    go(app, "imported_mesh")
    app.text_input(key="machines_mesh_path").set_value(str(path)).run()
    return ok(app.button(key="machines_mesh_use_path").click().run())


def copy_mesh(msh: MshUi, name: str, folder: Path = BAD_DIR.parent) -> Path:
    target = msh.tmp / "inputs" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((folder / name).read_bytes())
    return target


def draft(app: AppTest) -> dict[str, Any]:
    value: dict[str, Any] = app.session_state["machines_draft"]
    return value


def set_draft(app: AppTest, data: dict[str, Any]) -> AppTest:
    """Replace draft values, as state.replace_draft (the widgets are recreated)."""
    app.session_state["machines_draft"] = {**draft(app), **copy.deepcopy(data)}
    app.session_state["draft_generation"] = app.session_state["draft_generation"] + 1
    return ok(app.run())


def run_to_end(app: AppTest, msh: MshUi) -> None:
    ok(app.button(key="machines_save").click().run())
    go(app, "run")
    ok(app.button(key="machines_start").click().run())
    service = msh.service("demo")
    deadline = time.monotonic() + 60
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert service.run_status().state is RunView.SUCCEEDED, service.run_status()


# --- mesh source -------------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", ["hawt", "francis", "custom"])
def test_each_machine_offers_an_imported_mesh(msh: MshUi, profile_id: str) -> None:
    app = create(open_app(msh), profile_id)
    stl = nav(app)
    app = to_import(app)
    labels = nav(app)

    assert labels[:2] == ["Project", "Imported mesh"]
    assert not set(labels) & {*STL_ONLY, "Water passage"}
    assert len(labels) < len(stl) and draft(app)["imported_mesh"] == {"zones": [],
                                                                     "ami_pairs": []}
    for key in ("imported_mesh", "patches", "run", "mesh", "case"):  # each section draws
        go(app, key)
    to_import(app)  # nothing filled: back to STL at once
    assert nav(app) == stl and "imported_mesh" not in draft(app)


def test_vawt_is_told_to_use_a_custom_project(msh: MshUi) -> None:
    app = create(open_app(msh), "vawt")
    assert "To use an imported VAWT mesh, create a Custom project." in texts(app)
    assert not [b for b in app.button if b.key == "machines_mesh_source"]


def test_switching_a_filled_draft_asks_first(msh: MshUi) -> None:
    app = to_import(create(open_app(msh), "custom"))
    use_path(app, copy_mesh(msh, "rotating_41.msh"))
    go(app, "project")
    ok(app.button(key="machines_confirm_mesh_source_ask").click().run())
    assert "removes the imported mesh settings" in texts(app)
    ok(app.button(key="machines_confirm_mesh_source_yes").click().run())
    assert "imported_mesh" not in draft(app) and "Rotating zones" in nav(app)


# --- the Imported mesh section ------------------------------------------------------------

def test_a_file_by_path_shows_what_was_detected(msh: MshUi) -> None:
    app = use_path(to_import(create(open_app(msh), "custom")), copy_mesh(msh, "rotating_41.msh"))
    shown = texts(app)
    mesh = draft(app)["imported_mesh"]

    assert mesh["source_path"].endswith("rotating_41.msh") and len(mesh["sha256"]) == 64
    assert "Bounding box** (read from the file)" in shown
    detected = str(app.json[0].value)
    for expected in ("GMSH 4.1 (ASCII)", "gmshToFoam", "v2512", "ami_stat", "rotating"):
        assert expected in detected
    sizes = {r["units"]: r["size in m"] for r in app.dataframe[0].value.to_dict("records")}
    assert sizes["millimetres (mm)"] == "0.004 x 0.002 x 0.002"
    assert "Zone radius: computed from the zone once the mesh is imported." in shown


def test_the_suggested_ami_pair_is_used_only_by_its_button(msh: MshUi) -> None:
    app = use_path(to_import(create(open_app(msh), "custom")), copy_mesh(msh, "rotating_41.msh"))
    assert draft(app)["imported_mesh"]["ami_pairs"] == []
    assert "Suggested: `ami_stat` / `ami_rot`" in texts(app)
    ok(app.button(key="machines_ami_use_ami_stat_ami_rot").click().run())
    assert draft(app)["imported_mesh"]["ami_pairs"] == [{"first": "ami_stat",
                                                         "second": "ami_rot"}]
    names = {r["original name"]: r for r in app.dataframe[1].value.to_dict("records")}
    assert names["ami_stat"]["name used"] == "ami_stat" and names["ami_stat"]["as"].startswith(
        "AMI")


def test_the_patch_table_offers_the_mesh_patches(msh: MshUi) -> None:
    app = use_path(to_import(create(open_app(msh), "custom")), copy_mesh(msh, "rotating_41.msh"))
    ok(app.button(key="machines_ami_use_ami_stat_ami_rot").click().run())
    go(app, "patches")
    ok(app.button(key="machines_add_patches").click().run())
    patches = draft(app)["patches"]

    assert {p["source"]["ref"] for p in patches} == {"inlet", "outlet", "rotor", *(
        configs.ROTATING_SIDES)}  # not the AMI pair
    assert all(p["source"]["kind"] == "MESH_PATCH" and "type" not in p for p in patches)
    ok(app.button(key="machines_use_suggested").click().run())
    typed = {p["name"]: p.get("type") for p in draft(app)["patches"]}
    assert typed["inlet"] == "INLET" and typed["outlet"] == "OUTLET"


def test_a_binary_file_gets_its_box_by_the_convert_button(
        msh: MshUi, monkeypatch: pytest.MonkeyPatch) -> None:
    from machines.msh_import import detect
    monkeypatch.setattr(detect, "gmsh_available", lambda: True)
    app = use_path(to_import(create(open_app(msh), "custom")), copy_mesh(msh, "box_22_bin.msh"))
    assert "cannot be read from this file directly" in texts(app)
    ok(app.button(key="machines_mesh_box").click().run())
    assert "Bounding box** (from the converted mesh)" in texts(app)


def test_a_changed_file_is_found_and_used_again_by_a_button(msh: MshUi) -> None:
    path = copy_mesh(msh, "rotating_41.msh")
    app = use_path(to_import(create(open_app(msh), "custom")), path)
    before = draft(app)["imported_mesh"]["sha256"]
    path.write_bytes(path.read_bytes() + b"\n")
    ok(app.run())
    assert "The file has changed since it was chosen." in texts(app)
    ok(app.button(key="machines_mesh_rehash").click().run())
    assert draft(app)["imported_mesh"]["sha256"] != before


def shows_rule(app: AppTest, path: Path, code: str) -> None:
    """The rule's BLOCKING message is shown, while the draft is still incomplete."""
    (issue,) = [i for i in inspect_mesh_file(path).issues if i.code == code]
    errors = "\n".join(str(e.value) for e in app.error)
    assert f"**BLOCKING** · MESH_IMPORT · {issue.message}" in errors, errors


@pytest.mark.parametrize("name", sorted(BAD))
def test_each_bad_file_shows_its_rule(msh: MshUi, name: str) -> None:
    path = copy_mesh(msh, name, BAD_DIR)
    shows_rule(use_path(to_import(create(open_app(msh), "custom")), path), path, BAD[name][0])


@pytest.mark.parametrize("name", ["box_v1.msh", "box_22_nogroups.msh"])
def test_the_committed_bad_meshes_show_their_rule(msh: MshUi, name: str) -> None:
    path = copy_mesh(msh, name)
    shows_rule(use_path(to_import(create(open_app(msh), "custom")), path), path, OTHER[name])


# --- a run, its results and the check over one revolution ---------------------------------

def rotating_project(msh: MshUi, profile_id: str = "custom", **physics: Any) -> AppTest:
    path = copy_mesh(msh, "rotating_41.msh")
    app = to_import(create(open_app(msh), profile_id))
    data = configs.rotating(path)
    data["physics"].update(physics)
    data.pop("machine")
    return set_draft(app, data)


def test_a_run_shows_the_import_mesh_and_case_apart(msh: MshUi) -> None:
    msh.runner = {"weights": (0.999, 1.0004), "turning": (0.9946, 1.104)}
    app = rotating_project(msh)
    run_to_end(app, msh)
    go(app, "mesh")
    shown = texts(app)

    assert "Mesh: SUCCESS" in shown and "with gmshToFoam on OpenFOAM v2512" in shown
    assert "at rest (0 deg): sum(weights) 0.999–1" in shown
    assert "(WARNING outside 0.95–1.05)" in shown
    assert "Not checked yet." in shown
    ok(app.button(key="machines_ami_revolution").click().run())
    shown = texts(app)
    assert "over one revolution (36 x 10 deg): sum(weights) 0.9946–1.104" in shown
    warnings = "\n".join(str(w.value) for w in app.warning)
    assert "over one revolution (36 x 10 deg) spans 0.995-1.1, outside 0.95-1.05" in warnings
    go(app, "case")
    assert "Case setup: SUCCESS" in texts(app)


def test_a_flow_rate_inlet_is_measured_from_the_mesh(msh: MshUi) -> None:
    app = rotating_project(msh, "francis", inlets={
        "inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": 40.0}})
    go(app, "operating_point")
    shown = "\n".join(str(e.value) for e in app.info)
    assert "measured from the mesh when it is imported" in shown  # INFO, not blocking
    assert "INLET_AREA_UNKNOWN" not in texts(app)
    run_to_end(app, msh)
    go(app, "case")
    assert "Inlet inlet: area 20 m² (measured from the mesh" in texts(app)


# --- the tip radius ------------------------------------------------------------------------

def tsr(radius: float) -> dict[str, Any]:
    return {"rotor_zone": {"speed": {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 5.0,
                                     "radius": radius, "free_stream_speed": 5.0},
                           "direction": "POSITIVE"}}


def test_the_measured_tip_radius_is_offered_and_set_only_by_its_button(msh: MshUi) -> None:
    app = rotating_project(msh, rotation=tsr(2.0))
    go(app, "rotation")
    assert "Measured: not available yet" in texts(app)  # nothing converted yet
    run_to_end(app, msh)
    go(app, "rotation")
    shown = texts(app)
    # The fake's points are the box corners: y, z = +-1 about the x axis.
    assert "Measured: 1.414 m from rotor patch rotor" in shown
    assert "with tip radius R = 2 m" in shown
    assert "TIP_RADIUS_DIFFERS" in "\n".join(str(e.label) for e in app.expander)
    assert draft(app)["physics"]["rotation"]["rotor_zone"]["speed"]["radius"] == 2.0
    ok(app.button(key="machines_use_measured_rotor_zone").click().run())
    assert draft(app)["physics"]["rotation"]["rotor_zone"]["speed"]["radius"] == \
        pytest.approx(2**0.5)
    assert "TIP_RADIUS_DIFFERS" not in "\n".join(str(e.label) for e in app.expander)


def test_an_stl_rotor_s_radius_is_measured_from_its_body(msh: MshUi) -> None:
    from tests.unit.ui_machines.test_g7_app import hawt_physics, hawt_project
    app = hawt_project(msh)
    physics = hawt_physics()
    physics["rotation"] = {"rotating": tsr(9.0)["rotor_zone"]}
    set_draft(app, {"physics": physics})
    go(app, "rotation")
    # Independently: the rotor STL's points about the zone's axis (x) through
    # the zone's centre, as drafted by the HAWT preset.
    config = MachineProjectConfig.model_validate(draft(app))
    zone = config.rotating_zones[0]
    source = config.bodies[0].source
    vertices = np.asarray(trimesh.load(str(source.source_path)).vertices)
    vertices = vertices * source.source_units.to_metres
    radial = (vertices - np.asarray(_zone_origin(zone)))[:, 1:]
    expected = float(np.linalg.norm(radial, axis=1).max())
    assert f"Measured: {expected:.4g} m from rotor body rotor" in texts(app)
    ok(app.button(key="machines_use_measured_rotating").click().run())
    assert draft(app)["physics"]["rotation"]["rotating"]["speed"]["radius"] == \
        pytest.approx(expected)


def test_a_hawt_project_runs_on_an_imported_mesh(msh: MshUi) -> None:
    app = rotating_project(msh, "hawt")
    assert draft(app)["machine"] == "HAWT" and "Rotor" not in nav(app)
    run_to_end(app, msh)
    go(app, "case")
    assert "Case setup: SUCCESS" in texts(app)
    assert (M1 / "rotating_41.msh").is_file()


def test_showing_the_screens_never_edits_the_draft(msh: MshUi) -> None:
    app = use_path(to_import(create(open_app(msh), "custom")), copy_mesh(msh, "rotating_41.msh"))
    before = copy.deepcopy(draft(app))
    for key in ("imported_mesh", "patches", "fluid", "rotation", "review", "run", "mesh",
                "case", "imported_mesh"):
        go(app, key)
    assert draft(app) == before
