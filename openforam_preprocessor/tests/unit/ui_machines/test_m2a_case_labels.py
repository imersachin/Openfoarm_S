"""On screen (owner, after M2a): a long cell-zone name is shown short with its
full name on hover; the solution settings say why there are correctors; every
Courant number shown has the note that below 50 is stable, not accurate."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from app.machines_ui.sections.common import COURANT_NOTE, short_name, zone_html

GV_SKIN = "fluid_GV_flux_1_GV1_skin"


def test_a_long_zone_name_is_short_with_the_full_name_on_hover() -> None:
    assert short_name(GV_SKIN) == "skin"
    assert zone_html(GV_SKIN) == f'<abbr title="{GV_SKIN}">skin</abbr>'
    assert short_name("rotating") == "rotating" and zone_html("rotating") == "rotating"
    assert short_name("a" * 30) == "a" * 15 + "…"  # no '_' to cut at
    assert zone_html("<x>_" + "y" * 20) == '<abbr title="&lt;x&gt;_' + "y" * 20 + '">' \
        + "y" * 20 + "</abbr>"


def test_the_courant_note() -> None:
    assert COURANT_NOTE == ("A max Courant number below 50 keeps a run stable, not accurate: "
                            "rotor-stator studies usually need a max Courant number around "
                            "1-5.")


def _case_page() -> None:
    from app.machines_ui.sections.case import values_view
    from app.machines_ui.sections.mesh import zone_quality_view

    values_view({
        "fluid": {}, "turbulence": "K_OMEGA_SST", "solver": "pimpleFoam",
        "time": {"courant_estimate": 3.2, "estimates": "heuristic"},
        "non_orthogonality": {"max_deg": 81.643, "zone": "fluid_GV_flux_1_GV1_skin"}})
    zone_quality_view({"quality_by_zone": {"non-orthogonality": [
        {"zone": "fluid_GV_flux_1_GV1_skin", "cells": 71680, "cells_past_limit": 5724,
         "percent": 7.99, "worst": 81.64}]}})


def test_case_setup_shows_the_reason_short_and_the_courant_note() -> None:
    app = AppTest.from_function(_case_page).run()
    assert not app.exception
    texts = [m.value for m in app.markdown]
    solution = next(t for t in texts if t.startswith("**Solution settings:**"))
    assert ("max non-orthogonality 81.6 deg in <abbr title=\"fluid_GV_flux_1_GV1_skin\">"
            "skin</abbr> (above 70 deg): 2 non-orthogonal correctors, nCorrectors 2") \
        in solution
    assert COURANT_NOTE in [c.value for c in app.caption]
    table = next(t for t in texts if t.startswith("| check | cell zone"))
    assert '<abbr title="fluid_GV_flux_1_GV1_skin">skin</abbr> | 71,680 | 5,724' in table


def _plain_case() -> None:
    from app.machines_ui.sections.case import solution_view

    solution_view({})


def test_without_correctors_the_settings_are_named_too() -> None:
    app = AppTest.from_function(_plain_case).run()
    assert [m.value for m in app.markdown] == [
        "**Solution settings:** the tutorial's (nCorrectors 1, no non-orthogonal "
        "correctors); schemes limited (limited corrected 0.33)."]
