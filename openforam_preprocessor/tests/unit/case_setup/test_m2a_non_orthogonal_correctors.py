"""Correctors for a highly non-orthogonal mesh (owner, after M2a; the future-list
item brought forward; docs/msh_import_notes.md 13.9).

Above 70 deg of mesh non-orthogonality, case setup writes 2 non-orthogonal
correctors and nCorrectors 2, the reason beside them in system/fvSolution, in
reports/case_setup.json and as an INFO. On v2512, GV (81.6 deg) diverged in 10
pimpleFoam steps with the tutorial's settings and ran cleanly with these. At or
below 70 deg every case file is as before (the G6 tests are unchanged).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from case_setup.dictionaries import fv_solution
from case_setup.setup import NonOrthogonality, non_orthogonality
from machines.pipeline import CASE_SETUP_REPORT
from tests.fakes_msh import FakeMshRunner
from tests.unit.mesh_import.test_m2a_quality import Setup

LOG = "Checking geometry...\n    Mesh non-orthogonality Max: 81.6247 average: 9.2\n"
REASON = ("max non-orthogonality 81.6 deg in skin (above 70 deg): 2 non-orthogonal "
          "correctors, nCorrectors 2")


def test_the_value_and_its_zone_are_read_from_checkmesh_and_the_zone_report() -> None:
    by_zone = {"non-orthogonality": [
        {"zone": "down", "worst": 78.5}, {"zone": "skin", "worst": 81.6},
        {"zone": "up", "worst": 72.8}]}
    found = non_orthogonality(LOG, by_zone)
    assert found == NonOrthogonality(81.6247, "skin")
    assert found.corrected and found.reason == REASON
    assert non_orthogonality(LOG) == NonOrthogonality(81.6247, None)
    assert non_orthogonality("no geometry check") is None
    assert not NonOrthogonality(70.0).corrected  # above 70, not at it


def plain_and_corrected(tmp_path: Path) -> tuple[str, str]:
    setup = Setup(tmp_path, FakeMshRunner())
    assert setup.run().succeeded
    from case_setup.case import render
    from machines.domains import read_imported

    config = setup.service.saved_config()
    assert config is not None
    mesh = setup.root / "cases" / "merged" / "constant" / "polyMesh"
    case, _ = render(config, read_imported(config), mesh)
    return (fv_solution(case),
            fv_solution(replace(case, non_orthogonality=NonOrthogonality(81.6247, "skin"))))


def test_fvsolution_gets_the_correctors_and_the_reason_above_70_deg(tmp_path: Path) -> None:
    plain, corrected = plain_and_corrected(tmp_path)
    assert "    nCorrectors         1;\n    nNonOrthogonalCorrectors 0;\n" in plain
    assert (f"    // {REASON}\n    nCorrectors         2;\n"
            "    nNonOrthogonalCorrectors 2;\n") in corrected
    # nothing else changes
    assert corrected.replace(f"    // {REASON}\n", "").replace(
        "nCorrectors         2", "nCorrectors         1").replace(
        "nNonOrthogonalCorrectors 2", "nNonOrthogonalCorrectors 0") == plain


@pytest.mark.parametrize("value", [40.0, 70.0])
def test_at_or_below_70_deg_the_case_is_as_before(tmp_path: Path, value: float) -> None:
    before = Setup(tmp_path / "before", FakeMshRunner())
    at = Setup(tmp_path / "at", FakeMshRunner(non_ortho=value))
    for setup in (before, at):
        assert setup.run().succeeded
    for name in ("system/fvSolution", "system/fvSchemes", "system/controlDict"):
        assert (at.root / "case" / name).read_bytes() == (before.root / "case" / name)\
            .read_bytes()
    assert "non_orthogonality" not in at.json(CASE_SETUP_REPORT)


def test_an_imported_mesh_above_70_deg_gets_the_correctors_with_its_zone(
        tmp_path: Path) -> None:
    angles = [30.0] * 100
    angles[70] = 81.6  # a rotating cell (the fake's zones: stationary 60, rotating 40)
    runner = FakeMshRunner(cells=100, zones={"stationary": 60, "rotating": 40},
                           non_ortho=81.6, fields={"nonOrthoAngle": angles})
    setup = Setup(tmp_path, runner)
    result = setup.run()

    assert result.succeeded, result.message
    reason = ("max non-orthogonality 81.6 deg in rotating (above 70 deg): 2 "
              "non-orthogonal correctors, nCorrectors 2")
    text = (setup.root / "case" / "system" / "fvSolution").read_text("utf-8")
    assert f"// {reason}\n    nCorrectors         2;\n    nNonOrthogonalCorrectors 2;" in text
    report = json.loads((setup.root / CASE_SETUP_REPORT).read_text("utf-8"))
    assert report["non_orthogonality"] == {
        "max_deg": 81.6, "zone": "rotating", "reason": reason,
        "schemes": "laplacian and snGrad limited corrected 0.33, in every case"}
    info = [i for i in result.issues if i.code == "CASE_NON_ORTHOGONAL_CORRECTORS"]
    assert [i.severity.value for i in info] == ["INFO"]
    # On screen the INFO leaves the zone out (Case setup shows it, short, with hover).
    assert info[0].message == (
        "Case setup: max non-orthogonality 81.6 deg (above 70 deg): 2 non-orthogonal "
        "correctors, nCorrectors 2 (in system/fvSolution; the zone is in Case setup).")
    assert info[0].details["zone"] == "rotating"


def test_a_change_of_the_mesh_value_re_writes_the_case(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner())
    assert setup.run().succeeded
    setup.service.pipeline.runner = FakeMshRunner(non_ortho=81.6)
    config = setup.service.saved_config()
    assert config is not None
    result = setup.service.pipeline.run_sync(setup.root, config, force=True)
    assert result.succeeded
    assert "nNonOrthogonalCorrectors 2" in (setup.root / "case/system/fvSolution").read_text(
        "utf-8")
