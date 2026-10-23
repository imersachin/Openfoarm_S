"""U1-1 on screen: the rotor page shows the axis point (the surface centroid by
default) and the swept D, the 3D pane draws the proposed axis before the
preset, and only the preset button applies it, with an entered point if any.
The measured tip radius offered for a tip-speed ratio is labelled the outer
swept radius (blade corners)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.vawt.three_blade import SWEPT_DIAMETER, write_three_blade
from tests.unit.ui_machines.conftest import MachinesUi, charts, create, go, ok, open_app, texts
from tests.unit.ui_machines.test_m1c_app import MshUi, msh, set_draft, tsr

__all__ = ["msh"]  # the fixture
AXIS_UV = (0.3, -0.2)


def rotor_page(ui: MachinesUi) -> Any:
    path = write_three_blade(ui.tmp / "three_blade.stl", AXIS_UV)
    app = create(open_app(ui), "vawt")
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    app.session_state["machines_rotor_inputs"] = {
        "source": {"source_path": str(path), "source_units": "m", "scale": 1.0,
                   "rotation_deg": dict(zero), "translation": dict(zero)},
        "axis": "z", "flow_axis": "x"}
    return go(app, "bodies")


def field(app: Any, label: str) -> Any:
    return next(n for n in app.number_input if n.label == label)


def test_the_axis_point_is_shown_and_drawn_before_the_preset(ui: MachinesUi) -> None:
    app = rotor_page(ui)
    shown = texts(app)
    assert ("**Axis point** (the rotation axis passes through it): x = 0.3 m, y = -0.2 m, "
            "the surface centroid.") in shown
    assert "The bounding-box centre is 0.09769 m from this point" in shown
    assert "rotating_zones" not in app.session_state["machines_draft"] or not \
        app.session_state["machines_draft"]["rotating_zones"]  # nothing applied yet
    # The 3D pane: Setup cannot draw the draft yet, so it shows the proposal.
    assert "Rotor axis" in app.radio(key="machines_preview_view").options
    assert len(charts(app)) == 1
    spec = str(charts(app)[0].proto.spec)
    assert "proposed axis point (0.3, -0.2, 0) m" in spec
    assert "bounding-box centre, 0.09769 m off the axis" in spec


def test_only_the_preset_button_applies_the_entered_point(ui: MachinesUi) -> None:
    app = rotor_page(ui)
    field(app, "Axis point x (m)").set_value(0.31)
    ok(next(b for b in app.button if b.label == "Apply"
            and "axis_point" in str(b.form_id)).click().run())
    assert "x = 0.31 m, y = -0.2 m, entered." in texts(app)
    assert not app.session_state["machines_draft"].get("rotating_zones")
    ok(app.button(key="machines_apply_preset").click().run())
    shape = app.session_state["machines_draft"]["rotating_zones"][0]["shape"]
    assert (shape["centre_u"], shape["centre_v"]) == pytest.approx((0.31, -0.2), abs=1e-6)
    # Back to the centroid, and the preset again.
    ok(app.button(key="machines_axis_centroid").click().run())
    assert "x = 0.3 m, y = -0.2 m, the surface centroid." in texts(app)
    ok(app.button(key="machines_apply_preset").click().run())
    shape = app.session_state["machines_draft"]["rotating_zones"][0]["shape"]
    assert (shape["centre_u"], shape["centre_v"]) == pytest.approx(AXIS_UV, abs=1e-6)
    assert shape["diameter"] == pytest.approx(1.5 * SWEPT_DIAMETER, abs=1e-4)


def test_the_measured_tip_radius_is_labelled_the_outer_swept_radius(msh: MshUi) -> None:
    from tests.unit.ui_machines.test_g7_app import hawt_physics, hawt_project
    app = hawt_project(msh)
    physics = hawt_physics()
    physics["rotation"] = {"rotating": tsr(9.0)["rotor_zone"]}
    set_draft(app, {"physics": physics})
    go(app, "rotation")
    shown = texts(app)
    assert "m from rotor body rotor, the outer swept radius (blade corners)." in shown
    assert ("A tip-speed ratio usually uses the blade mounting radius, which may be "
            "slightly smaller than the outer swept radius.") in shown
    button = app.button(key="machines_use_measured_rotating")
    assert "outer swept radius (blade corners)" in str(button.label)
