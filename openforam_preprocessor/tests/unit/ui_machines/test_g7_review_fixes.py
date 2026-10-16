"""G7 testing-review findings, each as a test (docs/rotating_machinery.md section 27)."""

from __future__ import annotations

import copy
import time
from typing import Any

from app.machines_ui.sections.review import ACCEPT_BOX
from machines.project_store import WallReference
from tests.fixtures.machines import configs
from tests.unit.ui_machines.conftest import (
    MachinesUi,
    go,
    ok,
    open_app,
    resources_at,
    texts,
)
from tests.unit.ui_machines.test_g7_app import apply_button, hawt_project
from vawt.service import RunView


def wait(ui: MachinesUi) -> RunView:
    service = ui.service("demo")
    deadline = time.monotonic() + 60
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return service.run_status().state


def test_high_resource_risk_accepted_in_review_lets_the_run_start(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    ok(app.button(key="machines_save").click().run())
    resources_at(ui, "HIGH RESOURCE RISK")
    service = ui.service("demo")
    assessment = service.preflight().assessment
    assert assessment is not None and assessment.status.value == "HIGH RESOURCE RISK"
    go(app, "run")
    assert app.button(key="machines_start").disabled  # not accepted yet
    go(app, "review")
    ok(app.checkbox(key=ACCEPT_BOX).check().run())
    go(app, "run")  # the checkbox is not drawn here; the acceptance is kept
    assert not app.button(key="machines_start").disabled
    ok(app.button(key="machines_start").click().run())
    assert wait(ui) is RunView.SUCCEEDED
    assert len(service.runs()) == 1


def test_the_acceptance_is_for_one_saved_revision(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    ok(app.button(key="machines_save").click().run())
    resources_at(ui, "HIGH RESOURCE RISK")
    go(app, "review")
    ok(app.checkbox(key=ACCEPT_BOX).check().run())
    draft = copy.deepcopy(app.session_state["machines_draft"])
    draft["max_global_cells"] = 1_999_999
    app.session_state["machines_draft"] = draft
    go(app, "refinement")  # Save is in a setup section's action bar
    ok(app.button(key="machines_save").click().run())  # a new revision
    go(app, "run")
    assert app.button(key="machines_start").disabled
    assert "accept it in Review first" in texts(app)


def test_blocked_cannot_be_overridden(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    ok(app.button(key="machines_save").click().run())
    resources_at(ui, "BLOCKED")
    go(app, "review")
    assert "cannot be overridden" in texts(app)
    assert not [c for c in app.checkbox if c.key == ACCEPT_BOX]
    go(app, "run")
    assert app.button(key="machines_start").disabled


def hawt_physics_without_outlets() -> dict[str, Any]:
    return {"fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
            "turbulence": {"length_scale": 0.1}, "inlets": {}, "outlets": {},
            "rotation": {}}


def test_showing_boundary_values_keeps_a_saved_project_saved(ui: MachinesUi) -> None:
    app = hawt_project(ui, physics=hawt_physics_without_outlets())
    ok(app.button(key="machines_save").click().run())
    before = copy.deepcopy(app.session_state["machines_draft"])
    go(app, "boundary")
    assert app.session_state["machines_draft"] == before
    assert "Unsaved changes" not in texts(app)
    ok(apply_button(app).click().run())  # Apply: the outlet takes the default 0 Pa
    assert app.session_state["machines_draft"]["physics"]["outlets"] == {"outlet": {}}


def test_a_fluid_preset_gives_typed_outlets_the_default(ui: MachinesUi) -> None:
    app = hawt_project(ui)
    go(app, "fluid")
    ok(app.button(key="machines_fluid_Air").click().run())
    assert app.session_state["machines_draft"]["physics"]["outlets"] == {"outlet": {}}


def test_use_this_thickness_needs_the_minimum_thickness(ui: MachinesUi) -> None:
    physics = {"fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
               "turbulence": {"length_scale": 0.1}}
    app = hawt_project(ui, physics=physics)  # the preset's layers: relative sizing
    service = ui.service("demo")
    service.save_ui_settings(service.ui_settings().model_copy(update={
        "wall": WallReference(length_m=0.1, speed_m_s=40.0, target_y_plus=1.0)}))
    go(app, "layers")
    use = next(b for b in app.button if str(b.key) == "machines_use_layer_0")
    assert use.disabled
    assert "Give the minimum layer thickness" in texts(app)
    next(n for n in app.number_input if n.key == "machines_use_min_thickness").set_value(1e-6)
    ok(app.run())
    ok(next(b for b in app.button if str(b.key) == "machines_use_layer_0").click().run())
    layers = app.session_state["machines_draft"]["bodies"][0]["layers"]
    assert layers["sizing"] == "ABSOLUTE" and layers["min_thickness_m"] == 1e-6
    assert service.validate(app.session_state["machines_draft"]).config is not None


def test_a_new_custom_project_shows_every_acceptance_limit(ui: MachinesUi) -> None:
    from tests.unit.ui_machines.conftest import create
    from vawt.config import StrictMeshQualityLimits

    app = create(open_app(ui), "custom")
    go(app, "mesh")
    shown = {str(n.key).split(":", 1)[-1] for n in app.number_input}
    assert {f"quality.{k}" for k in StrictMeshQualityLimits().model_dump()} <= shown
    assert [b for b in app.button if b.key == "machines_save"]  # the limits can be saved


def test_a_saved_project_without_a_profile_is_not_retyped(ui: MachinesUi) -> None:
    service = ui.service("pole")
    assert service.save(configs.pole(ui.tmp / "g")).saved  # VAWT_POLE: G8, no profile
    app = open_app(ui)
    app.text_input(key="machines_folder").set_value(str(service.root)).run()
    ok(app.button(key="machines_open_folder").click().run())
    assert "saved as a VAWT_POLE machine" in texts(app)
    assert not [b for b in app.button if b.key == "machines_use_machine"]
    stored = service.saved_config()
    assert stored is not None and stored.machine.value == "VAWT_POLE"
