"""G7: the machines UI (docs/rotating_machinery.md sections 13 and 27): a machine
choice first, then each machine's own sections and terms; physics; mesh and
case-setup results apart; the 3D pane with the rotation from the physics."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from app.machines_ui import sections as section_modules
from app.machines_ui import state
from machines import previews
from machines.profiles import ProfileRegistry
from tests.fixtures.machines import configs
from tests.fixtures.machines.g0.common import make_geometry
from tests.unit.ui_machines.conftest import (
    MachinesUi,
    charts,
    create,
    go,
    nav,
    ok,
    open_app,
    texts,
    write_profile,
)
from vawt.service import PreviewView, RunView

REPO = Path(__file__).resolve().parents[3]


def hawt_physics() -> dict[str, Any]:
    return {"fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
            "turbulence": {"length_scale": 0.1},
            "rotation": {"rotating": {"speed": {"kind": "RPM", "rpm": 300.0},
                                      "direction": "POSITIVE"}},
            "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5, 0, 0)}},
            "outlets": {"outlet": {}}}


def stl(path: Path) -> dict[str, Any]:
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    return {"source_path": str(path), "source_units": "m", "scale": 1.0,
            "rotation_deg": dict(zero), "translation": dict(zero)}


def hawt_project(ui: MachinesUi, **extra: Any) -> Any:
    """A HAWT project made through the UI: the preset from the rotor inputs."""
    make_geometry.hawt(ui.tmp)
    app = create(open_app(ui), "hawt")
    app.session_state["machines_rotor_inputs"] = {"source": stl(ui.tmp / "hawt_rotor.stl"),
                                                  "axis": "x"}
    go(app, "bodies")
    ok(app.button(key="machines_apply_preset").click().run())
    if extra:
        app.session_state["machines_draft"] = {**app.session_state["machines_draft"], **extra}
        ok(app.run())
    return app


def apply_button(app: Any) -> Any:
    return next(b for b in app.button if b.label == "Apply")


# --- the machine choice and each machine's sections ---------------------------------------

def test_the_app_opens_with_the_machine_choice(ui: MachinesUi) -> None:
    app = open_app(ui)
    assert "Rotating machines: mesh and case setup" in texts(app)
    options = app.radio(key="machines_landing_machine").options
    assert [o.split(" — ")[0] for o in options] == ["VAWT", "HAWT", "Francis turbine", "Custom"]


@pytest.mark.parametrize("profile_id", ["vawt", "hawt", "francis", "custom"])
def test_each_machine_has_its_own_sections(ui: MachinesUi, profile_id: str) -> None:
    app = create(open_app(ui), profile_id)
    profile = ui.profiles.get(profile_id)
    assert profile is not None
    assert nav(app) == [s.title for s in profile.sections]
    assert f"{profile.label} · " in texts(app)
    for spec in profile.sections:  # a fresh project: every section draws
        go(app, spec.key)
        assert state.SECTION in app.session_state and \
            app.session_state[state.SECTION] == spec.key


def test_machine_terms(ui: MachinesUi) -> None:
    assert nav(create(open_app(ui), "hawt"))[1:3] == ["Rotor", "Rotating zone"]
    app = create(open_app(ui), "francis", "turbine")
    labels = nav(app)
    assert labels[1:3] == ["Water passage", "Patch types"] and "Operating point" in labels
    go(app, "parts")
    shown = texts(app)
    for slot in ("Spiral casing", "Stay vanes (optional)", "Guide vanes", "Runner",
                 "Draft tube"):
        assert slot in shown


def test_only_the_active_section_renders(ui: MachinesUi,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    app = create(open_app(ui), "custom")
    calls: list[str] = []
    for key, module in section_modules.RENDERERS.items():
        original = module.render

        def spy(ctx: Any, _key: str = key, _render: Any = original) -> None:
            calls.append(_key)
            _render(ctx)

        monkeypatch.setattr(module, "render", spy)
    go(app, "zones")
    assert calls == ["zones"]


def test_a_profile_loaded_from_data_renders_its_sections(ui: MachinesUi) -> None:
    data = {"id": "kaplan", "label": "Kaplan turbine", "description": "Adjustable blades.",
            "machine": "CUSTOM", "preset": "NONE", "order": 5,
            "terms": {"body": "runner blade"},
            "sections": [{"key": "project"}, {"key": "bodies", "label": "Runner blades"},
                         {"key": "domain", "label": "Water passage"}, {"key": "zones"},
                         {"key": "patches"}, {"key": "fluid"},
                         {"key": "rotation", "label": "Runner speed"}, {"key": "review"},
                         {"key": "run"}, {"key": "mesh"}, {"key": "case"}]}
    write_profile(ui.tmp / "profiles", data)
    ui.profiles = ProfileRegistry.from_directory(ui.tmp / "profiles")
    app = open_app(ui)
    assert app.radio(key="machines_landing_machine").options == [
        "Kaplan turbine — Adjustable blades."]
    app = create(app, "kaplan")
    assert nav(app) == ["Project", "Runner blades", "Water passage", "Rotating zones",
                        "Patches", "Fluid and turbulence", "Runner speed", "Review", "Run",
                        "Mesh", "Case setup"]
    for key in ("bodies", "domain", "rotation", "case"):
        go(app, key)
    service = ui.service("demo")
    assert service.profile() is not None and service.profile().id == "kaplan"


# --- presets, physics, the pane ------------------------------------------------------------

def test_hawt_preset_drafts_the_machine(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    draft = app.session_state["machines_draft"]
    assert (draft["machine"], len(draft["rotating_zones"]), draft["domain"]["kind"]) == (
        "HAWT", 1, "CYLINDER")
    assert "Preset values are starting points" in texts(app)
    assert nav(app)[:5] == ["Project", "Rotor", "Rotating zone", "Domain", "Patches"]
    assert len(charts(app)) == 1


def test_showing_a_section_never_changes_the_draft(ui: MachinesUi) -> None:
    app = hawt_project(ui, physics=hawt_physics())
    before = copy.deepcopy(app.session_state["machines_draft"])
    for key in ("project", "bodies", "zones", "domain", "patches", "refinement", "layers",
                "fluid", "boundary", "rotation", "time", "review", "mesh", "case"):
        go(app, key)
        assert app.session_state["machines_draft"] == before, key


def test_physics_from_a_preset_then_the_rotation_arrow(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    go(app, "fluid")
    ok(app.button(key="machines_fluid_Air").click().run())
    physics = app.session_state["machines_draft"]["physics"]
    assert physics["fluid"] == {"density": 1.2, "kinematic_viscosity": 1.5e-5}
    (chart,) = charts(app)
    assert "rotating: rotation not set" in chart.proto.spec

    go(app, "rotation")
    ok(app.radio(key=next(r.key for r in app.radio if str(r.key).startswith(
        "machines_speed_kind_rotating"))).set_value("RPM").run())
    rpm = next(n for n in app.number_input if str(n.key).endswith("rotating.speed.rpm"))
    rpm.set_value(120.0)
    direction = next(s for s in app.selectbox
                     if str(s.key).endswith("rotating.direction"))
    direction.set_value("NEGATIVE")
    ok(apply_button(app).click().run())
    rotation = app.session_state["machines_draft"]["physics"]["rotation"]["rotating"]
    assert rotation == {"speed": {"kind": "RPM", "rpm": 120.0}, "direction": "NEGATIVE"}
    assert "ω = 12.5664 rad/s = 120 rpm" in texts(app)
    (chart,) = charts(app)
    assert "negative about +x (clockwise seen from +x), 120 rpm" in chart.proto.spec


def test_francis_water_passage_from_named_slots(ui: MachinesUi) -> None:
    geometry = ui.tmp / "francis"
    geometry.mkdir()
    make_geometry.francis(geometry)
    app = create(open_app(ui), "francis")
    app.session_state["machines_slot_inputs"] = {
        "slots": {"spiral_casing": stl(geometry / "casing.stl"),
                  "guide_vanes": stl(geometry / "guide.stl"),
                  "runner": stl(geometry / "runner.stl"),
                  "draft_tube": stl(geometry / "draft.stl")},
        "axis": "z", "origin": configs.vec(0, 0, 0), "diameter": 0.6}
    go(app, "parts")
    ok(app.button(key="machines_francis_draft").click().run())
    draft = app.session_state["machines_draft"]
    assert [p["name"] for p in draft["domain"]["parts"]] == [
        "spiral_casing", "guide_vanes", "draft_tube"]
    assert len(draft["joints"]) == 3
    assert "Proposed joints, with their measurements" in texts(app)
    assert len(charts(app)) == 1  # untyped patches drawn as regions without a patch
    assert "Patches without a type are drawn as regions" in texts(app)

    go(app, "patches")
    ok(app.button(key="machines_use_suggested").click().run())
    types = {p["name"]: p.get("type") for p in app.session_state["machines_draft"]["patches"]}
    assert (types["inlet"], types["outlet"]) == ("INLET", "OUTLET")
    assert types["vanes"] is None  # never typed without a suggestion


def test_francis_operating_point(ui: MachinesUi) -> None:
    service = ui.service("op")
    drafted = configs.francis(ui.tmp / "g")  # typed patches, G0 R4 names
    drafted["physics"] = {"fluid": {"density": 998.0, "kinematic_viscosity": 1e-6}}
    assert service.save(drafted).saved
    service.choose_profile("francis")
    app = open_app(ui)
    app.text_input(key="machines_folder").set_value(str(service.root)).run()
    ok(app.button(key="machines_open_folder").click().run())
    go(app, "operating_point")
    q = next(n for n in app.number_input if str(n.key).endswith("inlet.volumetric_flow_rate"))
    q.set_value(0.25)
    rpm = next(n for n in app.number_input if str(n.key).endswith("runner.speed.rpm"))
    rpm.set_value(500.0)
    next(s for s in app.selectbox if str(s.key).endswith("runner.direction")).set_value(
        "POSITIVE")
    ok(apply_button(app).click().run())
    physics = app.session_state["machines_draft"]["physics"]
    assert physics["inlets"]["inlet"] == {"kind": "FLOW_RATE", "volumetric_flow_rate": 0.25}
    assert physics["rotation"]["runner"] == {"speed": {"rpm": 500.0, "kind": "RPM"},
                                             "direction": "POSITIVE"}
    assert physics["outlets"] == {"outlet": {}}  # the default, 0 Pa
    assert "rated net head" in texts(app).lower()
    head = next(n for n in app.number_input if "Rated net head" in str(n.label))
    head.set_value(100.0)
    ok(next(b for b in app.button if b.label == "Keep as reference").click().run())
    assert service.ui_settings().rated_net_head_m == 100.0
    assert "Specific speed n_q ≈ 7.906 (reference)" in texts(app)  # 500 x 0.25^0.5 / 100^0.75
    assert "never a case input" in str(head.label)


def test_y_plus_estimate_in_the_layers_section(ui: MachinesUi) -> None:
    draft_extra = {"physics": hawt_physics()}
    app = hawt_project(ui, **draft_extra)
    draft = app.session_state["machines_draft"]
    draft["bodies"][0]["layers"] = {"enabled": True, "sizing": "ABSOLUTE",
                                    "first_layer_thickness": 2e-5, "min_thickness_m": 1e-5}
    app.session_state["machines_draft"] = draft
    service = ui.service("demo")
    from machines.project_store import WallReference

    service.save_ui_settings(service.ui_settings().model_copy(update={
        "wall": WallReference(length_m=0.1, speed_m_s=40.0, target_y_plus=1.0)}))
    go(app, "layers")
    shown = texts(app)
    assert "estimated **y+ ≈" in shown and "For a target **y+ = 1**" in shown
    assert "F. M. White" in shown and "Not a solver result" in shown
    use = next(b for b in app.button if str(b.key) == "machines_use_layer_0")
    ok(use.click().run())
    layers = app.session_state["machines_draft"]["bodies"][0]["layers"]
    assert layers["first_layer_thickness"] < 2e-5 and layers["sizing"] == "ABSOLUTE"


# --- runs and results ---------------------------------------------------------------------

def test_a_run_from_the_ui_shows_mesh_and_case_setup_apart(ui: MachinesUi) -> None:
    app = hawt_project(ui, physics=hawt_physics())
    ok(app.button(key="machines_save").click().run())
    go(app, "review")
    assert "Case setup (fields and dictionaries; not solved)" in texts(app)
    go(app, "run")
    ok(app.button(key="machines_start").click().run())
    service = ui.service("demo")
    deadline = time.monotonic() + 60
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert service.run_status().state is RunView.SUCCEEDED
    go(app, "mesh")
    assert "Mesh: SUCCESS" in texts(app)
    go(app, "case")
    shown = texts(app)
    assert "Case setup: SUCCESS" in shown and "Prepared, not solved" in shown
    assert "ω = 31.4159" in shown
    labels = nav(app)
    assert labels.index("Mesh") < labels.index("Case setup")


def test_mesh_only_run_reports_case_setup_skipped(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    ok(app.button(key="machines_save").click().run())
    go(app, "run")
    ok(app.button(key="machines_start").click().run())
    service = ui.service("demo")
    deadline = time.monotonic() + 60
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    go(app, "case")
    shown = texts(app)
    assert "Case setup: SKIPPED" in shown and "meshed only" in shown


# --- performance and boundaries -------------------------------------------------------------

def test_switching_section_reads_no_geometry_and_builds_nothing(
        ui: MachinesUi, monkeypatch: pytest.MonkeyPatch) -> None:
    app = hawt_project(ui, physics=hawt_physics())
    checks = ui.checks.runs
    built: list[str] = []
    for name in ("build_setup_scene", "build_mesh_scene", "load_bodies", "read_points"):
        original = getattr(previews, name)
        monkeypatch.setattr(previews, name,
                            lambda *a, _n=name, _o=original, **k: built.append(_n) or _o(*a, **k))
    for key in ("zones", "domain", "fluid", "rotation", "review"):
        go(app, key)
    assert ui.checks.runs == checks  # Review's plan and preflight reuse the checks
    assert built == []
    assert len(charts(app)) == 1


def test_preset_inputs_never_carry_to_another_project(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    assert app.session_state["machines_rotor_inputs"]
    other = ui.service("other")
    other.root.mkdir(parents=True)
    other.choose_profile("hawt")
    app.text_input(key="machines_folder").set_value(str(other.root)).run()
    ok(app.button(key="machines_open_folder").click().run())
    assert "machines_rotor_inputs" not in app.session_state


def test_ui_adds_no_plotly_modules_at_start() -> None:
    probe = ("import sys, streamlit; before = {m for m in sys.modules if 'plotly' in m}; "
             "import app.machines_ui.shell, app.machines_ui.state; "
             "print(sorted({m for m in sys.modules if 'plotly' in m} - before))")
    result = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True,
                            text=True, env={**os.environ, "PYTHONPATH": str(REPO)},
                            check=True)
    assert result.stdout.strip() == "[]"


def test_preview_view_enum_is_shared() -> None:
    assert [v.value for v in PreviewView] == ["setup", "mesh"]
