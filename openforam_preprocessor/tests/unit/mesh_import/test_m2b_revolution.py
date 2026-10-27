"""M2b-4: the revolution check over one blade pitch, the solve estimate and the
runner + draft tube study, without OpenFOAM.
"""

from __future__ import annotations

from typing import Any

import pytest

from machines.config import MachineProjectConfig
from machines.msh_import import revolution, solve_estimate, study
from machines.validation import parse_config
from tests.unit.mesh_import.test_m2b_interfaces import with_interfaces


def config_of(data: dict[str, Any]) -> MachineProjectConfig:
    config, issues = parse_config(data)
    assert config is not None, [i.message for i in issues]
    return config


# --- over one blade pitch -----------------------------------------------------------------------

def test_a_rotating_part_of_copies_is_checked_over_one_pitch() -> None:
    config = config_of(with_interfaces())  # the runner: 6 copies of 60 deg
    assert revolution.pitch_of(config) == (60.0, "Runner")
    assert revolution.default_steps(config) == 12
    assert revolution.sweep_label(config, 12) == (
        "over one blade pitch (60 deg, 12 x 5 deg), equivalent to a full revolution because "
        "the Runner mesh repeats every 60 deg")
    files = revolution.revolution_files(config, 12)
    control = files["system/controlDict"]
    assert "endTime         0.16666666666666666;" in control  # a sixth of a turn, 1 turn/s
    assert "deltaT          0.013888888888888888;" in control


def test_the_pitch_sweep_offers_six_steps_only_when_twelve_take_long() -> None:
    config = config_of(with_interfaces())
    assert revolution.sweep_options(config, None) == (12,)
    assert revolution.sweep_options(config, 49.0) == (12,)  # 9.8 min
    assert revolution.sweep_options(config, 85.0) == (12, 6)  # E0: 85 s a step at 23 M
    assert revolution.sweep_label(config, 6).startswith("over one blade pitch (60 deg, 6 x 10")


def test_a_single_files_revolution_files_are_as_before() -> None:
    """The check's key hashes these files: a finished check stays current."""
    from tests.fixtures.msh import configs

    config = config_of(configs.rotating(configs.mesh_file("rotating_41.msh")))
    control = revolution.revolution_files(config, 36)["system/controlDict"]
    assert "endTime         1;\ndeltaT          0.027777777777777776;" in control


def test_a_whole_ring_rotating_part_is_checked_over_a_revolution() -> None:
    data = with_interfaces()
    runner = data["imported_mesh"]["parts"][2]
    runner.update(passages=1, periodic=[])
    config = config_of(data)
    assert revolution.pitch_of(config) is None
    assert revolution.sweep_label(config, 36) == revolution.over_revolution(36)
    assert revolution.sweep_options(config, None) == revolution.sweeps(None)


# --- the solve estimate -------------------------------------------------------------------------

LOG = "\n".join(f"Time = {i}\n...\nExecutionTime = {t} s  ClockTime = {int(t)} s"
                for i, t in enumerate((185.2, 342.4, 470.1, 644.3), start=1))


def test_the_time_a_step_is_measured_from_the_case_log_without_the_first_step() -> None:
    assert solve_estimate.step_seconds(LOG) == pytest.approx([157.2, 127.7, 174.2])
    found = solve_estimate.estimate(11_671_680, 30 * 10**9, 16, 1.0, LOG)
    assert found.measured and found.seconds_per_step == pytest.approx(157.2)
    assert found.hours_per_revolution == pytest.approx(found.seconds_per_step * 360 / 3600)


def test_without_a_log_the_time_is_scaled_and_said_so() -> None:
    found = solve_estimate.estimate(23_000_000, None, 16, 1.0)
    assert not found.measured and found.basis.startswith("scaled from")
    assert found.fits is None


def test_the_memory_is_compared_with_what_wsl_has_free() -> None:
    big = solve_estimate.estimate(22_984_188, 31 * 10**9, 16, 1.0)
    assert big.ram_bytes == pytest.approx(1.25 * solve_estimate.RAM_PER_CELL * 22_984_188)
    assert big.fits is False
    assert solve_estimate.estimate(1_000_000, 31 * 10**9, 16, 1.0).fits is True


# --- the runner + draft tube study --------------------------------------------------------------

def test_the_study_keeps_the_rotating_part_and_the_outlets() -> None:
    config = config_of(with_interfaces("AMI", "ACMI"))
    assert study.study_parts(config) == ("RU",)  # the runner holds the fixture's outlet
    data = with_interfaces("AMI", "ACMI")
    data["patches"] = [p for p in data["patches"] if p["name"] != "outlet"]
    data["patches"].append({"name": "outlet", "type": "OUTLET",
                            "source": {"kind": "MESH_PATCH", "ref": "GV_top"}})
    data["patches"] = [p for p in data["patches"] if p["name"] != "GV_top"]
    assert study.study_parts(config_of(data)) == ("GV", "RU")


def test_the_study_takes_its_inlet_from_the_sides_left_without_a_neighbour() -> None:
    data = with_interfaces("AMI", "ACMI")
    data["physics"] = {"inlets": {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": 0.6}},
                       "outlets": {"outlet": {"pressure_pa": 0.0}},
                       "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
                       "turbulence": {"length_scale": 0.05},
                       "rotation": {"RU": {"speed": {"kind": "RPM", "rpm": 60.0},
                                           "direction": "NEGATIVE"}},
                       "time": {"end_revolutions": 0.1, "write_interval": 1,
                                "write_unit": "DEGREES"}}
    raw, notes = study.study_draft(config_of(data), {"RU_run_in": 1.5, "GV_vane_out": 1.5},
                                   "demo study") or ({}, [])
    assert [p["prefix"] for p in raw["imported_mesh"]["parts"]] == ["RU"]
    assert raw["imported_mesh"]["interfaces"] == []
    inlet = next(p for p in raw["patches"] if p["type"] == "INLET")
    assert inlet["source"]["ref"] == "RU_run_in"
    assert raw["physics"]["inlets"] == {"RU_run_in": {"kind": "FLOW_RATE",
                                                      "volumetric_flow_rate": 0.6}}
    assert any("split by area" in n for n in notes)
    assert parse_config(raw)[0] is not None


def test_a_revolution_takes_the_time_step_the_solver_took() -> None:
    """The reference case: maxCo cut 1 deg (166.7 us at 1000 rpm) to about 1 us."""
    log = "deltaT = 0.000166667\n" + "".join(
        f"deltaT = {d}\nExecutionTime = {t} s  ClockTime = 0 s\n"
        for d, t in ((4.07e-06, 82.8), (9.0e-07, 153.9), (6.7e-07, 224.9), (7.6e-07, 295.1)))
    found = solve_estimate.estimate(11_671_680, None, 16, 1.0, log, rpm=1000.0)
    assert found.step_from_log
    assert found.degrees_per_step == pytest.approx(9.0e-07 * 6000, rel=0.1)
    at_one_degree = solve_estimate.estimate(11_671_680, None, 16, 1.0, log)
    assert found.hours_per_revolution / at_one_degree.hours_per_revolution > 50
