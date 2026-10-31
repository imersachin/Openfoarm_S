"""C1 (owner, after review): an imported machine's case runs the robust PIMPLE
settings at a fixed step, starts as chosen (Allrun), may carry the guide
vanes' swirl, and is checked against the runner's turning and the guide vane
opening. An STL-built case is as before (its G6 tests are unchanged).

Evidence: docs/c1_startup_notes.md (v2512, the owner's Francis runner + draft
tube): the earlier settings diverged from every start; these ran.
"""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from case_setup import dictionaries as dicts
from case_setup.case import generated_before, write_case
from case_setup.dictionaries import ALLRUN, dictionaries, imported_files
from case_setup.fields import velocity
from case_setup.setup import CaseSetupError, resolve
from case_setup.validation import check_physics
from machines.config import MachineProjectConfig
from machines.msh_import import host_memory, solve_estimate, surface
from machines.msh_import.surface import BoundarySurface, PatchRadius
from machines.pipeline import CASE_SETUP_REPORT
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.msh import configs
from tests.unit.mesh_import.test_m2a_quality import Setup


def imported(**physics: Any) -> MachineProjectConfig:
    data = configs.rotating(configs.mesh_file("rotating_41.msh"))
    data["physics"].update(copy.deepcopy(physics))
    return MachineProjectConfig.model_validate(data)


def boundary(config: MachineProjectConfig) -> list[tuple[str, str]]:
    return [(p.name, "patch") for p in config.patches] + [
        ("ami_stat", "cyclicAMI"), ("ami_rot", "cyclicAMI")]


SWIRL = {"kind": "SWIRL", "volumetric_flow_rate": 0.071, "flow_angle_deg": 7.8,
         "direction": "NEGATIVE"}
RING = 0.11828  # m2, the Francis runner's inflow ring (C1 evidence)


def swirl_setup(**changes: Any) -> Any:
    inlet = {**SWIRL, **changes}
    config = imported(inlets={"inlet": inlet},
                      rotation={"rotor_zone": {"speed": {"kind": "RPM", "rpm": 406.2},
                                               "direction": "NEGATIVE"}})
    return resolve(config, [], boundary(config), mesh_areas={"inlet": RING},
                   mesh_radii={"inlet": PatchRadius(0.32, 1.0)})


# --- the robust settings, written ---------------------------------------------------------------

def test_an_imported_case_gets_the_robust_settings_and_a_fixed_step(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner())
    assert setup.run().succeeded
    case = setup.root / "case"
    control = (case / "system/controlDict").read_text("utf-8")
    assert "adjustTimeStep  no;" in control and "maxCo" not in control
    solution = (case / "system/fvSolution").read_text("utf-8")
    for line in ("nOuterCorrectors    5;", "nCorrectors         2;",
                 "nNonOrthogonalCorrectors 1;", "turbOnFinalIterOnly false;",
                 "U { tolerance 0.0001; relTol 0; }", "p { tolerance 0.001; relTol 0; }",
                 "UFinal          1;", "\"(k|omega)Final\" 1;", "pFinal          1;",
                 "U               0.7;", "p               0.3;"):
        assert line in solution, line
    assert "residualControl" in solution and "outerCorrectorResidualControl" not in solution
    assert "numberOfSubdomains 16;" in (case / "system/decomposeParDict").read_text("utf-8")
    run = (case / ALLRUN).read_text("utf-8")
    assert run.startswith("#!/bin/sh\n")  # a script: no generated-file header above it
    assert run.index("potentialFoam -writephi") < run.index("runParallel pimpleFoam")
    assert "runParallel pimpleFoam || exit 1" in run  # stops at the first failed step
    if os.name == "posix":
        assert os.access(case / ALLRUN, os.X_OK)
    report = setup.json(CASE_SETUP_REPORT)
    assert report["start_up"] == "POTENTIAL_FLOW" and report["ranks"] == 16
    assert report["run_order"] == "case/Allrun"
    assert all(row["reason"] for row in report["solution_settings"])
    assert report["solution_settings"][0]["setting"].startswith("Time step: ")


def test_a_users_own_allrun_is_kept(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner())
    mine = "#!/bin/sh\necho my own run\n"
    (setup.root / "case").mkdir(parents=True)
    (setup.root / "case" / ALLRUN).write_text(mine, "utf-8")
    result = setup.run()
    assert result.succeeded
    assert (setup.root / "case" / ALLRUN).read_text("utf-8") == mine
    info = [i for i in result.issues if i.code == "CASE_RUN_ORDER_USERS"]
    assert [i.severity.value for i in info] == ["INFO"]
    assert setup.json(CASE_SETUP_REPORT)["run_order"] == "case/Allrun (yours, kept)"
    assert (setup.root / "case/system/controlDict").is_file()  # the rest is written


def test_more_ranks_than_processors_is_a_warning(tmp_path: Path) -> None:
    from tests.fakes import PLENTY

    setup = Setup(tmp_path, FakeMshRunner())
    data = configs.rotating(setup.service.store_mesh_file(
        "rotating_41.msh", configs.mesh_file("rotating_41.msh").read_bytes()))
    data["physics"]["ranks"] = (PLENTY.cpu_count or 1) + 1
    result = setup.run(data)
    found = [i for i in result.issues if i.code == "RANKS_ABOVE_CORES"]
    assert result.succeeded and [i.severity.value for i in found] == ["WARNING"]
    data["physics"]["ranks"] = 1
    assert "RANKS_ABOVE_CORES" not in {i.code for i in Setup(
        tmp_path / "b", FakeMshRunner()).run(data).issues}


@pytest.mark.parametrize(("field", "section"), [
    ("physics.guide_vanes", "operating_point"), ("physics.start_up", "time"),
    ("physics.ranks", "time"), ("physics.inlets.inlet.direction", "operating_point")])
def test_the_new_findings_are_shown_where_their_fields_are(field: str, section: str) -> None:
    from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
    from machines.profiles import default_registry
    from machines.service import MachineService

    profile = default_registry().get("francis")
    assert profile is not None
    issue = Issue(category=IssueCategory.CONFIGURATION, severity=IssueSeverity.WARNING,
                  stage=IssueStage.CASE_SETUP, code="X", message="x", details={"field": field})
    assert MachineService.section_of_issue(issue, profile) == section


def test_every_setting_says_why() -> None:
    """Each PIMPLE and relaxation entry of an imported case has a comment above it."""
    config = imported()
    text = dicts.imported_fv_solution(resolve(config, [], boundary(config)))
    pimple = text.split("PIMPLE\n{", 1)[1].split("cache", 1)[0]
    for key in ("nOuterCorrectors", "nCorrectors", "turbOnFinalIterOnly", "residualControl",
                "relaxationFactors"):
        before = pimple.split(key, 1)[0].rstrip().splitlines()
        assert any(line.lstrip().startswith("//") for line in before[-4:]), key


def stl(tmp_path: Path, **physics: Any) -> MachineProjectConfig:
    from tests.fixtures.machines import configs as machines
    from tests.unit.case_setup.test_g6_case_setup import hawt_physics

    return MachineProjectConfig.model_validate(
        {**machines.hawt(tmp_path / "geometry"), "physics": hawt_physics(**physics)})


def test_an_stl_case_is_as_before(tmp_path: Path) -> None:
    from tests.unit.case_setup.test_g6_case_setup import HAWT_MESH

    found = resolve(stl(tmp_path), [], HAWT_MESH)
    assert not found.imported
    files = dictionaries(found)
    assert ALLRUN not in files and "system/decomposeParDict" not in files
    assert "maxCo" in files["system/controlDict"]
    assert "nOuterCorrectors    2;" in files["system/fvSolution"]
    assert imported_files(found, files) == {}


def test_above_70_deg_the_imported_case_keeps_its_two_correctors() -> None:
    from case_setup.setup import NonOrthogonality

    config = imported()
    found = resolve(config, [], boundary(config))
    from dataclasses import replace
    text = dicts.imported_fv_solution(replace(found, non_orthogonality=NonOrthogonality(81.6)))
    assert ("    // max non-orthogonality 81.6 deg (above 70 deg): 2 non-orthogonal correctors, "
            "nCorrectors 2\n    nCorrectors         2;\n    nNonOrthogonalCorrectors 2;\n") in text


# --- the start-ups --------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["FROM_REST", "STEADY_MRF"])
def test_the_start_up_is_written_into_the_run_order(method: str) -> None:
    config = imported(start_up={"method": method, "mrf_iterations": 40})
    found = resolve(config, [], boundary(config))
    files = dictionaries(found)
    run = files[ALLRUN]
    solution = files["system/fvSolution"]
    assert "runParallel potentialFoam" not in run
    assert "    Phi  //" not in solution and "potentialFlow" not in solution
    extra = imported_files(found, files)
    if method == "FROM_REST":
        assert extra == {} and "simpleFoam" not in run
        return
    assert run.index("runParallel simpleFoam") < run.index("runParallel pimpleFoam")
    assert "ln -s ../../constant/polyMesh constant/polyMesh" in run
    mrf = extra["mrf/constant/MRFProperties"]
    assert "cellZone        rotating;" in mrf and f"{found.rotation.omega!r}" in mrf
    control = extra["mrf/system/controlDict"]
    assert "application     simpleFoam;" in control and "endTime         40;" in control
    assert "steadyState" in extra["mrf/system/fvSchemes"]
    assert "consistent      yes;" in extra["mrf/system/fvSolution"]
    assert "mrf/constant/dynamicMeshDict" not in extra and "mrf/0/U" not in extra


def test_the_mrf_case_has_the_same_fields() -> None:
    from case_setup.fields import fields

    config = imported(start_up={"method": "STEADY_MRF"})
    found = resolve(config, [], boundary(config))
    files = {**fields(found), **dictionaries(found)}
    extra = imported_files(found, files)
    assert {k for k in extra if k.startswith("mrf/0/")} == {f"mrf/{k}" for k in files
                                                            if k.startswith("0/")}
    assert extra["mrf/0/U"] == files["0/U"]


def test_a_later_setup_removes_the_mrf_files(tmp_path: Path) -> None:
    mesh = tmp_path / "mesh"
    mesh.mkdir()
    (mesh / "boundary").write_text("()", "utf-8")
    files = {"system/controlDict": "a", ALLRUN: "#!/bin/sh\n", "mrf/system/controlDict": "b"}
    case = write_case(tmp_path, files, mesh)
    assert generated_before(case) == {"system/controlDict", ALLRUN, "mrf/system/controlDict"}
    write_case(tmp_path, {"system/controlDict": "a"}, mesh)
    assert not (case / "mrf/system/controlDict").exists() and not (case / ALLRUN).exists()


# --- the swirl inlet --------------------------------------------------------------------------

def test_the_swirl_inlet_from_the_guide_vanes() -> None:
    setup = swirl_setup()
    swirl = setup.inlets["inlet"].swirl
    vr = 0.071 / RING
    assert swirl.radial_velocity == pytest.approx(-vr)
    assert swirl.tangential_velocity == pytest.approx(-vr / math.tan(math.radians(7.8)))
    assert swirl.tangential_velocity == pytest.approx(-4.38, abs=0.01)  # C1 notes: Vt 4.38
    assert swirl.rpm == pytest.approx(-130.7, abs=0.2)  # C1 notes: rpm -130.7, clockwise
    assert swirl.label == "approximate (from vane trailing-edge angle)"
    text = velocity(setup)
    assert "type            cylindricalInletVelocity;" in text
    assert "approximate (from vane trailing-edge angle)" in text
    assert "rpm             constant -130." in text and "axis            (1.0 0.0 0.0);" in text


def test_a_swirl_inlet_needs_an_inlet_around_the_axis() -> None:
    config = imported(inlets={"inlet": SWIRL})
    with pytest.raises(CaseSetupError, match="faces the axis over 40%"):
        resolve(config, [], boundary(config), mesh_areas={"inlet": RING},
                mesh_radii={"inlet": PatchRadius(0.32, 0.4)})


def codes(config: MachineProjectConfig) -> dict[str, str]:
    return {i.code: i.severity.value for i in check_physics(config, [])}


def test_the_swirl_direction_is_never_assumed() -> None:
    inlet = {k: v for k, v in SWIRL.items() if k != "direction"}
    assert codes(imported(inlets={"inlet": inlet}))["SWIRL_DIRECTION_NOT_SET"] == "BLOCKING"


def test_a_runner_turning_against_the_swirl_is_a_warning() -> None:
    against = imported(inlets={"inlet": {**SWIRL, "direction": "POSITIVE"}},
                       rotation={"rotor_zone": {"speed": {"kind": "RPM", "rpm": 406.2},
                                                "direction": "NEGATIVE"}})
    found = codes(against)
    assert found["RUNNER_AGAINST_SWIRL"] == "WARNING"
    with_it = imported(inlets={"inlet": SWIRL},
                       rotation={"rotor_zone": {"speed": {"kind": "RPM", "rpm": 406.2},
                                                "direction": "NEGATIVE"}})
    assert "RUNNER_AGAINST_SWIRL" not in codes(with_it)


def test_a_swirl_inlet_is_for_an_imported_machine_only(tmp_path: Path) -> None:
    config = stl(tmp_path, inlets={"inlet": SWIRL})
    assert codes(config)["SWIRL_INLET_NOT_IMPORTED"] == "BLOCKING"


# --- the guide vane opening -------------------------------------------------------------------

@pytest.mark.parametrize(("operating", "code"), [
    (3.91, None), (9.84, "GUIDE_VANE_OPENING_MISMATCH")])
def test_the_operating_point_is_checked_against_the_meshs_opening(
        operating: float, code: str | None) -> None:
    vanes = {"operating_deg": operating, "mesh_min_deg": 3.6, "mesh_max_deg": 4.7,
             "mesh_source": "confirmed by file name GV-03-91 and measurement 3.6-4.7 deg"}
    found = check_physics(imported(guide_vanes=vanes), [])
    mismatch = [i for i in found if i.code == "GUIDE_VANE_OPENING_MISMATCH"]
    if code is None:
        assert mismatch == []
        return
    assert mismatch[0].severity.value == "WARNING"
    assert "9.84 deg" in mismatch[0].message and "GV-03-91" in mismatch[0].message


def test_a_reversed_opening_range_stops_the_case() -> None:
    vanes = {"operating_deg": 4.0, "mesh_min_deg": 4.7, "mesh_max_deg": 3.6}
    assert codes(imported(guide_vanes=vanes))["GUIDE_VANE_RANGE_INVALID"] == "BLOCKING"


# --- the time step ----------------------------------------------------------------------------

@pytest.mark.parametrize(("degrees", "warned"), [(2.0, False), (2.5, True)])
def test_an_imported_machine_may_step_two_degrees(degrees: float, warned: bool) -> None:
    config = imported(time={**configs.SMOKE_TIME, "degrees_per_step": degrees})
    assert ("ROTATION_STEP_LARGE" in codes(config)) is warned


def test_the_study_labels_and_hours() -> None:
    from app.machines_ui.sections.time_controls import study_label

    assert study_label(1.0, "Rotor-stator pressure pulses, marginal", {1.0: 26.7}, 28) == (
        "Rotor-stator pressure pulses, marginal: 1 deg a step (about 13 steps a guide vane "
        "passing at 28 vanes), about 27 h a revolution")
    found = solve_estimate.estimate(11_671_680, None, 16, 1.0)
    assert found.hours_for(1.0) == pytest.approx(26.7, abs=0.1)
    assert found.hours_for(2.0) == pytest.approx(found.hours_for(1.0) / 2)


# --- the outer loop, from the log ---------------------------------------------------------------

LOG = """Time = 0.0008
PIMPLE: iteration 1
PIMPLE: iteration 5
PIMPLE: not converged within 5 iterations
ExecutionTime = 300 s  ClockTime = 300 s
Time = 0.0016
PIMPLE: iteration 3
PIMPLE: converged in 3 iterations
ExecutionTime = 520 s  ClockTime = 520 s
"""


def test_steps_that_reach_the_outer_limit_are_counted() -> None:
    found = solve_estimate.outer_loop(LOG)
    assert found == solve_estimate.OuterLoop(steps=2, not_converged=1, limit=5)
    assert solve_estimate.outer_loop("") is None
    assert solve_estimate.estimate(1000, None, 16, 1.0, LOG).outer == found


# --- the inlet's radius, measured -----------------------------------------------------------------

def ring(radius: float, height: float, n: int = 72) -> BoundarySurface:
    """A cylinder about +z of n quads, region 'inlet'."""
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    low = np.c_[radius * np.cos(angles), radius * np.sin(angles), np.full(n, -height / 2)]
    points = np.vstack([low, low + [0, 0, height]])
    quads = [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    return BoundarySurface(points, np.arange(0, 4 * n + 1, 4), np.array(quads).ravel(),
                           np.zeros(n, dtype=np.int64), ("inlet",))


def test_the_inlet_radius_and_facing_are_measured(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    monkeypatch.setattr(surface, "load", lambda _: ring(0.32, 0.06))
    found = surface.patch_radii(tmp_path, ["inlet", "absent"], (0, 0, 0), (0, 0, 1))
    assert set(found) == {"inlet"}
    assert found["inlet"].radius == pytest.approx(0.32, rel=1e-3)
    assert found["inlet"].facing == pytest.approx(1.0, abs=1e-6)
    sideways = surface.patch_radii(tmp_path, ["inlet"], (0, 0, 0), (1, 0, 0))["inlet"]
    assert sideways.facing < 0.9


# --- WSL's ceiling --------------------------------------------------------------------------------

class Done:
    def __init__(self, stdout: str, code: int = 0) -> None:
        self.stdout, self.returncode = stdout, code


def test_the_host_memory_is_read_and_wsls_ceiling_derived() -> None:
    host_memory._cache.clear()
    found = host_memory.host_memory(run=lambda *a, **k: Done("66788076 14901904 31093702656"),
                                    clock=lambda: 0.0)
    assert found is not None
    assert found.total_bytes == 66788076 * 1024
    assert found.windows_used_bytes == 66788076 * 1024 - 14901904 * 1024 - 31093702656
    assert found.wsl_ceiling_bytes == pytest.approx(46.0e9, rel=0.01)
    # cached for a minute; then read again
    assert host_memory.host_memory(run=lambda *a, **k: Done("x"), clock=lambda: 30.0) == found
    assert host_memory.host_memory(run=lambda *a, **k: Done("x"), clock=lambda: 61.0) is None


@pytest.mark.parametrize("text", ["", "1 2", "a b c", "100 50 0", "100 90 20480"])
def test_an_unreadable_host_is_none_never_guessed(text: str) -> None:
    assert host_memory.parse(text) is None


def test_no_powershell_is_none() -> None:
    host_memory._cache.clear()

    def missing(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError("powershell.exe")
    assert host_memory.host_memory(run=missing, clock=lambda: 0.0) is None
    host_memory._cache.clear()
