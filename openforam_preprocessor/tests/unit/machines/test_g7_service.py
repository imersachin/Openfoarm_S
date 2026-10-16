"""G7: MachineService, the only interface of the machines UI (fake OpenFOAM)."""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

import pytest

from machines.operations import CASE_SETUP, MACHINE_EXECUTABLES
from machines.pipeline import MachinePipeline
from machines.profiles import MachineProfile, default_registry
from machines.project_store import (
    LAST_MESHED_PATH,
    MachineUiSettings,
    WallReference,
    read_last_meshed,
)
from machines.service import (
    FLUENT_DROPPED,
    PHYSICS_LEFT_OUT,
    UNTYPED_LEFT_OUT,
    WAKE_DROPPED,
    MachineService,
    _CheckCache,
    displayable,
    list_machine_projects,
    machines_projects_root,
)
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from tests.fixtures.machines.g0.common import make_geometry
from tests.fixtures.vawt.rotor import RotorSpec, write_rotor
from vawt.runtime import RunRegistry
from vawt.service import PreviewView, RunView, SectionState


def get(profile_id: str) -> MachineProfile:
    found = default_registry().get(profile_id)
    assert found is not None
    return found


def physics(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "turbulence": {"length_scale": 0.1},
        "rotation": {"rotating": {"speed": {"kind": "RPM", "rpm": 300.0},
                                  "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0.0, 0.0)}},
        "outlets": {"outlet": {}},
    }
    data.update(overrides)
    return data


@pytest.fixture
def service(tmp_path: Path) -> MachineService:
    env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)
    pipeline = MachinePipeline(FakeMachineRunner(), environment=env,
                               system_probe=lambda _: PLENTY)
    return MachineService(tmp_path / "project", pipeline=pipeline, env=env,
                          registry=RunRegistry(), system_probe=lambda _: PLENTY,
                          check_cache=_CheckCache())


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return configs.hawt(tmp_path / "geometry")


def rotor_source(path: Path) -> dict[str, Any]:
    return {"source_path": str(path), "source_units": "m"}


# --- presets -----------------------------------------------------------------------------

def test_hawt_preset(service: MachineService, tmp_path: Path) -> None:
    make_geometry.hawt(tmp_path)
    result = service.draft_from_preset(get("hawt"), {
        "project_name": "H", "source": rotor_source(tmp_path / "hawt_rotor.stl"), "axis": "x"})
    assert result.draft is not None, result.issues
    assert (result.draft["machine"], result.draft["flow_axis"]) == ("HAWT", "x")
    assert not service.validate(result.draft).issues or not any(
        i.is_stopping for i in service.validate(result.draft).issues)


def test_vawt_preset_leaves_out_what_only_the_vawt_app_meshes(service: MachineService,
                                                             tmp_path: Path) -> None:
    path = write_rotor(tmp_path / "rotor.stl", RotorSpec(axis="z"))
    result = service.draft_from_preset(get("vawt"), {
        "project_name": "V", "source": rotor_source(path), "axis": "z", "flow_axis": "x"})
    assert result.draft is not None, result.issues
    assert result.draft["wake"] is None and result.draft["export"]["fluent_msh"] is False
    assert WAKE_DROPPED in result.notes and FLUENT_DROPPED in result.notes
    assert not any(i.is_stopping for i in service.validate(result.draft).issues)


def francis_inputs(directory: Path, **slots: str) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.francis(directory)
    files = {"spiral_casing": "casing", "guide_vanes": "guide", "runner": "runner",
             "draft_tube": "draft", **slots}
    return {"project_name": "F", "axis": "z", "origin": configs.vec(0, 0, 0), "diameter": 0.6,
            "slots": {k: rotor_source(directory / f"{v}.stl") for k, v in files.items()}}


def test_francis_preset_from_named_slots(service: MachineService, tmp_path: Path) -> None:
    result = service.draft_from_preset(get("francis"), francis_inputs(tmp_path / "g"))
    assert result.draft is not None, result.issues
    assert [p["name"] for p in result.draft["domain"]["parts"]] == [
        "spiral_casing", "guide_vanes", "draft_tube"]  # passage order; no stay vanes given
    assert result.draft["rotating_zones"][0]["name"] == "runner"
    assert [(a, b) for a, b, _ in result.joints] == [
        ("casing_out", "guide_in"), ("guide_out", "runner_in"), ("draft_in", "runner_out")]
    assert result.outlet_diameters["runner_out"] == pytest.approx(0.6)
    assert result.suggested_types == {"inlet": "INLET", "outlet": "OUTLET"}
    assert all("type" not in p for p in result.draft["patches"])  # chosen by the user (E5)


def test_francis_needs_every_required_slot(service: MachineService, tmp_path: Path) -> None:
    inputs = francis_inputs(tmp_path / "g")
    del inputs["slots"]["guide_vanes"]
    result = service.draft_from_preset(get("francis"), inputs)
    assert result.draft is None
    (issue,) = result.issues
    assert issue.code == "FRANCIS_SLOT_MISSING" and issue.details["slots"] == ["Guide vanes"]


def test_custom_has_no_preset(service: MachineService) -> None:
    result = service.draft_from_preset(get("custom"), {})
    assert result.draft is None and [i.code for i in result.issues] == ["NO_PRESET"]


# --- sections --------------------------------------------------------------------------------

def test_sections_of_a_hawt_draft(service: MachineService, hawt: dict[str, Any]) -> None:
    sections = service.analyse(hawt, get("hawt")).sections
    assert {k: sections[k].state for k in ("project", "bodies", "zones", "domain", "patches")} \
        == dict.fromkeys(("project", "bodies", "zones", "domain", "patches"), SectionState.READY)
    assert {sections[k].state for k in ("fluid", "boundary", "rotation", "time")} == {
        SectionState.EMPTY}  # no physics: mesh only


def test_incomplete_physics_keeps_the_setup_checks(service: MachineService,
                                                  hawt: dict[str, Any]) -> None:
    draft = {**hawt, "physics": physics(rotation={"rotating": {"direction": "POSITIVE"}})}
    analysis = service.analyse(draft, get("hawt"))
    assert analysis.outcome.config is None  # cannot be saved as it is
    assert analysis.sections["rotation"].state is SectionState.INCOMPLETE
    assert "MULTIPLE_COMPONENTS" in [i.code for i in analysis.sections["bodies"].issues]


def test_unset_direction_is_incomplete_not_wrong(service: MachineService,
                                                 hawt: dict[str, Any]) -> None:
    draft = {**hawt, "physics": physics(rotation={"rotating": {"speed": {
        "kind": "RPM", "rpm": 300.0}}})}
    rotation = service.analyse(draft, get("hawt")).sections["rotation"]
    assert rotation.state is SectionState.INCOMPLETE
    assert [i.code for i in rotation.issues] == ["ROTATION_DIRECTION_NOT_SET"]


def test_francis_findings_go_to_its_own_sections(service: MachineService,
                                                 tmp_path: Path) -> None:
    drafted = service.draft_from_preset(get("francis"), francis_inputs(tmp_path / "g")).draft
    assert drafted is not None
    sections = service.analyse(drafted, get("francis")).sections
    assert sections["patches"].state is SectionState.INCOMPLETE  # types not chosen yet
    assert sections["parts"].state is SectionState.READY


def test_settings_changed_since_the_last_mesh_are_stale(service: MachineService,
                                                        hawt: dict[str, Any]) -> None:
    from machines.config import MachineProjectConfig
    from machines.project_store import write_last_meshed

    write_last_meshed(service.root, "run", MachineProjectConfig.model_validate(hawt), "passed")
    changed = copy.deepcopy(hawt)
    changed["domain"]["cell_size"] = 0.2
    domain = service.analyse(changed, get("hawt")).sections["domain"]
    assert domain.state is SectionState.STALE and domain.stale_because == ("domain.cell_size",)


def test_checks_are_reused_while_only_physics_changes(service: MachineService,
                                                      hawt: dict[str, Any]) -> None:
    service.validate(hawt)
    service.validate({**hawt, "physics": physics()})
    service.validate({**hawt, "physics": physics(outlets={"outlet": {"pressure_pa": 5.0}})})
    assert service.checks.runs == 1
    service.validate({**hawt, "max_global_cells": 1_000_000})
    assert service.checks.runs == 2


# --- files and settings --------------------------------------------------------------------

def test_uploads_of_named_slots_never_collide(service: MachineService) -> None:
    a = service.store_source_file("part.stl", b"a", "spiral_casing")
    b = service.store_source_file("part.stl", b"b", "draft_tube")
    assert a != b and a.read_bytes() == b"a" and b.read_bytes() == b"b"
    assert a.parent.name == "spiral_casing"
    with pytest.raises(ValueError, match="Only .stl"):
        service.store_source_file("part.obj", b"")
    with pytest.raises(ValueError, match="not a folder"):
        service.store_source_file("part.stl", b"", "../outside")


def test_profile_choice_and_fallback(service: MachineService, hawt: dict[str, Any]) -> None:
    assert service.profile() is None
    service.choose_profile("francis")
    assert service.profile() == get("francis")
    with pytest.raises(ValueError, match="Unknown machine profile"):
        service.choose_profile("kaplan")
    service.save_ui_settings(MachineUiSettings())  # no profile recorded: from the machine
    assert service.save(hawt).saved
    assert service.profile() == get("hawt")


def test_projects_folder(tmp_path: Path, service: MachineService,
                         hawt: dict[str, Any]) -> None:
    assert machines_projects_root({"MACHINES_PROJECTS_DIR": str(tmp_path)}) == tmp_path
    assert service.save(hawt).saved
    assert list_machine_projects(tmp_path) == [service.root]


# --- estimates ------------------------------------------------------------------------------

def test_wall_estimate_both_ways(service: MachineService, hawt: dict[str, Any]) -> None:
    draft = {**hawt, "physics": physics()}
    reference = WallReference(length_m=0.1, speed_m_s=40.0, target_y_plus=1.0)
    result = service.wall_estimate(draft, reference, 2e-5)
    assert result.forward is not None and result.reverse is not None
    back = service.wall_estimate(draft, reference, result.reverse.first_layer_thickness)
    assert back.forward is not None and back.forward.y_plus == pytest.approx(1.0)
    assert service.wall_estimate(hawt, reference).notes  # no fluid yet
    assert service.wall_estimate(draft, WallReference()).notes  # no reference yet


def test_reference_speeds_are_labelled(service: MachineService, hawt: dict[str, Any]) -> None:
    rpm = service.speed_suggestions({**hawt, "physics": physics()})
    labels = {s.label: s.speed for s in rpm}
    assert labels["inlet speed (stationary walls)"] == pytest.approx(5.0)
    tip = 300.0 * 2 * 3.141592653589793 / 60 * 0.6  # omega x zone radius 0.6 m
    assert labels["rotating: omega x zone radius (an upper bound of the tip speed)"] == \
        pytest.approx((tip ** 2 + 25.0) ** 0.5)
    tsr = service.speed_suggestions({**hawt, "physics": physics(rotation={"rotating": {
        "speed": {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 6.0, "radius": 0.5},
        "direction": "NEGATIVE"}})})
    assert [s.speed for s in tsr][1] == pytest.approx((30.0 ** 2 + 5.0 ** 2) ** 0.5)


def test_operating_reference(service: MachineService, hawt: dict[str, Any]) -> None:
    draft = {**hawt, "physics": physics(inlets={"inlet": {
        "kind": "FLOW_RATE", "volumetric_flow_rate": 4.0}})}
    values = service.operating_reference(draft, MachineUiSettings(rated_net_head_m=16.0))
    assert values["omega_rad_s"] == pytest.approx(300 * 2 * 3.141592653589793 / 60)
    assert values["specific_speed_nq"] == pytest.approx(300 * 2.0 / 8.0)  # 300 x 4^0.5 / 16^0.75
    assert "specific_speed_nq" not in service.operating_reference(draft, MachineUiSettings())


# --- display tolerance ------------------------------------------------------------------------

def test_the_pane_draws_a_draft_with_incomplete_physics_or_untyped_patches(
        hawt: dict[str, Any], tmp_path: Path, service: MachineService) -> None:
    incomplete = {**hawt, "physics": physics(rotation={"rotating": {}})}
    config, notes = displayable(incomplete)
    assert config is not None and config.physics is None and notes == (PHYSICS_LEFT_OUT,)
    drafted = service.draft_from_preset(get("francis"), francis_inputs(tmp_path / "g")).draft
    assert drafted is not None
    config, notes = displayable(drafted)
    assert config is not None and config.patches == () and UNTYPED_LEFT_OUT in notes
    key = service.preview_key(PreviewView.SETUP, drafted)
    assert key.signature is not None and UNTYPED_LEFT_OUT in key.notes
    assert displayable({"project_name": "x"}) == (None, ())


def test_rotation_arrow_follows_the_physics(service: MachineService,
                                            hawt: dict[str, Any]) -> None:
    unset = service.preview(PreviewView.SETUP, hawt)
    assert "rotating: rotation not set" in [a.label for a in unset.arrows]
    data = service.preview(PreviewView.SETUP, {**hawt, "physics": physics()})
    rotation = next(a for a in data.arrows if a.kind == "rotation")
    assert rotation.defined and rotation.head is not None
    assert rotation.label.startswith("rotating: positive about +x")


# --- runs ----------------------------------------------------------------------------------

def wait_for_end(service: MachineService) -> RunView:
    deadline = time.monotonic() + 60
    while service.run_status().state is RunView.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return service.run_status().state


def test_a_run_meshes_and_sets_up_the_case_shown_separately(
        service: MachineService, hawt: dict[str, Any]) -> None:
    assert service.save({**hawt, "physics": physics()}).saved
    plan = service.plan()
    assert plan.case_setup and plan.operations[-1] == CASE_SETUP and plan.from_scratch
    assert service.preflight().assessment is not None
    started = service.start_run()
    assert started.started, started.issues
    assert wait_for_end(service) is RunView.SUCCEEDED
    stages = service.last_stages()
    assert stages["mesh"]["status"] == "SUCCESS"
    assert stages["case_setup"]["status"] == "SUCCESS"
    reports = service.reports()
    assert reports["mesh"] is not None and reports["case_setup"]["status"] == "SUCCESS"
    assert "case/0/U" in service.case_files()
    assert (service.root / LAST_MESHED_PATH).is_file() and read_last_meshed(service.root)
    # The fake OpenFOAM writes placeholder mesh files: the view says so (a real
    # mesh is drawn in tests/integration/test_g7_machines_real.py).
    mesh = service.preview(PreviewView.MESH)
    assert [i.code for i in mesh.issues] == ["VIEW_MESH_UNAVAILABLE"]


def test_a_run_without_physics_meshes_only(service: MachineService,
                                           hawt: dict[str, Any]) -> None:
    assert service.save(hawt).saved
    assert service.start_run().started
    assert wait_for_end(service) is RunView.SUCCEEDED
    stages = service.last_stages()
    assert stages["mesh"]["status"] == "SUCCESS"
    assert (stages["case_setup"]["status"], stages["case_setup"]["reason"]) == (
        "SKIPPED", "NO_PHYSICS")


def test_a_run_needs_a_saved_configuration(service: MachineService) -> None:
    result = service.start_run()
    assert not result.started and result.issues[-1].code == "NO_SAVED_CONFIGURATION"
