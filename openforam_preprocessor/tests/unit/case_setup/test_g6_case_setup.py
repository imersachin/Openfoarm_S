"""G6: case setup without OpenFOAM (docs/rotating_machinery.md sections 9-11, 14).

The generated files are compared with the files that ran in G0 (hand-run
evidence, tests/fixtures/machines/g0) for the same inputs; the solver runs
themselves are in tests/integration/test_case_setup_real.py.
"""

from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any

import pytest

from case_setup.case import CASE_DIR, render, write_case
from case_setup.dictionaries import dictionaries
from case_setup.fields import fields
from case_setup.physics import (
    AIR,
    PhysicsConfig,
    kinematic_pressure,
    rad_s_to_rpm,
    rpm_to_rad_s,
    step_for_degrees,
    tip_speed_omega,
    turbulence_k,
    turbulence_omega,
)
from case_setup.setup import (
    CaseSetupError,
    PatchKind,
    angular_speed,
    inlet_area,
    patch_kinds,
    resolve,
)
from case_setup.validation import CaseSetupThresholds, check_physics
from machines.assembly import MachineCaseGenerator
from machines.config import MACHINE_SCHEMA_VERSION, MachineProjectConfig
from machines.domains import read_imported
from machines.validation import load_bodies
from tests.fixtures.machines import configs

G0 = Path(__file__).parents[2] / "fixtures/machines/g0"
HAWT_MESH = (("inlet", "patch"), ("outlet", "patch"), ("side", "patch"), ("rotor", "wall"),
             ("rotating_outer_stat", "cyclicAMI"), ("rotating_outer_rot", "cyclicAMI"))


def hawt_physics(**changes: Any) -> dict[str, Any]:
    """G0 R3's inputs: air, 5 m/s along +x, tip-speed ratio 6 on R 0.5 (60 rad/s),
    I 5 %, L 0.1 m."""
    physics: dict[str, Any] = {
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "turbulence": {"model": "K_OMEGA_SST", "intensity_percent": 5.0, "length_scale": 0.1},
        "rotation": {"rotating": {"speed": {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 6.0,
                                            "radius": 0.5}, "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0.0, 0.0)}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
    }
    physics.update(changes)
    return physics


@pytest.fixture
def hawt(tmp_path: Path) -> dict[str, Any]:
    return {**configs.hawt(tmp_path / "geometry"), "physics": hawt_physics()}


def model(data: dict[str, Any]) -> MachineProjectConfig:
    return MachineProjectConfig.model_validate(data)


def entries(text: str) -> dict[str, dict[str, str]]:
    """boundaryField patch -> {keyword: value}, comments dropped."""
    text = re.sub(r"//[^\n]*", "", text)
    body = text[text.index("boundaryField"):]
    return {name: dict(re.findall(r"(\w+)\s+([^;]+);", block))
            for name, block in re.findall(r"(\w+)\s*\{([^{}]*)\}", body)}


def keyword(text: str, key: str) -> str:
    match = re.search(rf"^{key}\s+([^;]+);", text, re.MULTILINE)
    assert match, key
    return match[1].strip()


def number(text: str) -> float:
    return float(text.removeprefix("uniform").strip())


# --- formulas (section 9; G0 inputs) -------------------------------------------------------

def test_formulas_reproduce_the_g0_inputs() -> None:
    k = turbulence_k(5.0, 5.0)
    assert k == pytest.approx(0.09375)  # G0 R3 0.sst/k
    assert turbulence_omega(k, 0.1) == pytest.approx(5.590169944)  # G0 R3 0.sst/omega
    assert tip_speed_omega(6.0, 5.0, 0.5) == pytest.approx(60.0)
    assert step_for_degrees(1.0, 60.0) == pytest.approx(0.0002908882087)  # G0 R3 deltaT
    assert step_for_degrees(1.0, -60.0) == step_for_degrees(1.0, 60.0)
    assert kinematic_pressure(1200.0, 1.2) == pytest.approx(1000.0)
    assert rpm_to_rad_s(60.0) == pytest.approx(2 * math.pi)
    assert rad_s_to_rpm(rpm_to_rad_s(190.0)) == pytest.approx(190.0)


# --- generated files against G0 ---------------------------------------------------------------

NUMBER = re.compile(r"-?\d+(?:\.\d*)?(?:e[-+]?\d+)?")


def normalised(text: str, internal: str) -> str:
    """$internalField substituted; every number rounded to 9 significant digits."""
    return NUMBER.sub(lambda m: f"{float(m[0]):.9g}", text.replace("$internalField", internal))


def test_hawt_fields_match_the_g0_run(hawt: dict[str, Any]) -> None:
    setup = resolve(model(hawt), [], HAWT_MESH)
    generated = fields(setup)
    for name in ("U", "p", "k", "omega", "nut"):
        g0, text = (G0 / "R3_hawt/solve/0.sst" / name).read_text("utf-8"), generated[f"0/{name}"]
        g0_internal, internal = keyword(g0, "internalField"), keyword(text, "internalField")
        assert normalised(internal, "") == normalised(g0_internal, ""), name
        expected = {p: {k: normalised(v, g0_internal) for k, v in e.items()}
                    for p, e in entries(g0).items()}
        mine = {p: {k: normalised(v, internal) for k, v in e.items()}
                for p, e in entries(text).items()}
        assert set(mine) == {"inlet", "outlet", "side", "rotor"}, name  # cyclicAMI: constraint
        assert mine == expected, name
        assert keyword(text, "dimensions") == keyword(g0, "dimensions")
        assert '#includeEtc "caseDicts/setConstraintTypes"' in text


def test_hawt_dictionaries_match_the_g0_run(hawt: dict[str, Any]) -> None:
    setup = resolve(model(hawt), [], HAWT_MESH)
    files = dictionaries(setup)
    solve = G0 / "R3_hawt/solve/constant"
    for name in ("dynamicMeshDict", "transportProperties"):
        g0, mine = (solve / name).read_text("utf-8"), files[f"constant/{name}"]
        for key in re.findall(r"^(\w+)\s", re.sub(r"(?s)FoamFile\s*\{.*?\}", "", g0), re.M):
            assert normalised(keyword(mine, key), "") == normalised(keyword(g0, key), ""), key
    common = G0 / "common/solver"
    for name in ("fvSchemes", "fvSolution"):
        strip = lambda t: re.sub(r"\s+", " ", re.sub(  # noqa: E731
            r"(?s)FoamFile\s*\{.*?\}|//[^\n]*", "", t)).strip()
        assert strip(files[f"system/{name}"]) == strip((common / name).read_text("utf-8"))
    assert strip(files["constant/turbulenceProperties"]) == strip(
        (common / "turbulenceProperties.sst").read_text("utf-8"))
    control = files["system/controlDict"]
    assert keyword(control, "application") == "pimpleFoam"
    assert keyword(control, "adjustTimeStep") == "yes"
    assert float(keyword(control, "maxCo")) == 2.0
    assert float(keyword(control, "maxDeltaT")) == pytest.approx(0.0002908882087)
    assert float(keyword(control, "endTime")) == pytest.approx(5 * 2 * math.pi / 60)
    assert "AMIWeights" in control


def test_francis_walls_match_the_g0_run(tmp_path: Path) -> None:
    """G0 R4: shroud and casing walls noSlip, blades movingWallVelocity; the
    flow-rate inlet takes its area from the inlet region."""
    data = configs.francis(tmp_path / "geometry")
    data["physics"] = {
        "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
        "turbulence": {"length_scale": 0.03},
        "rotation": {"runner": {"speed": {"kind": "RPM", "rpm": rad_s_to_rpm(20.0)},
                                "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": 0.1}},
        "outlets": {"outlet": {"pressure_pa": 998.0}}}
    config = model(data)
    surfaces = read_imported(config)
    mesh = [(p.name, "wall" if p.type in ("WALL", "ROTATING_WALL") else "patch")
            for p in config.patches]
    setup = resolve(config, surfaces, mesh)
    mine = entries(fields(setup)["0/U"])
    g0 = entries((G0 / "R4_francis/solve_ami/0.sst/U").read_text("utf-8"))
    for patch in ("casing_wall", "guide_wall", "vanes", "runner_wall", "draft_wall",
                  "runner_blades"):
        assert mine[patch] == g0[patch], patch
    assert mine["inlet"] == {"type": "flowRateInletVelocity",
                             "volumetricFlowRate": "constant 0.1"}
    area = inlet_area(config, surfaces, "inlet")
    assert area is not None and setup.inlets["inlet"].speed == pytest.approx(0.1 / area)
    assert number(entries(fields(setup)["0/p"])["outlet"]["value"]) == pytest.approx(1.0)
    dynamic = dictionaries(setup)["constant/dynamicMeshDict"]
    assert keyword(dynamic, "cellZone") == "runner"
    assert float(keyword(dynamic, "omega")) == pytest.approx(20.0)
    assert keyword(dynamic, "axis") == "(0.0 0.0 1.0)"


# --- choices -------------------------------------------------------------------------------

def test_laminar_writes_velocity_and_pressure_only(hawt: dict[str, Any]) -> None:
    hawt["physics"]["turbulence"] = {"model": "LAMINAR"}
    setup = resolve(model(hawt), [], HAWT_MESH)
    assert set(fields(setup)) == {"0/U", "0/p"}
    assert keyword(dictionaries(setup)["constant/turbulenceProperties"],
                   "simulationType") == "laminar"


def test_direction_is_the_sign_of_omega(hawt: dict[str, Any]) -> None:
    hawt["physics"]["rotation"]["rotating"]["direction"] = "NEGATIVE"
    config = model(hawt)
    assert config.physics is not None
    assert angular_speed(config.physics, "rotating") == pytest.approx(-60.0)
    dynamic = dictionaries(resolve(config, [], HAWT_MESH))["constant/dynamicMeshDict"]
    assert float(keyword(dynamic, "omega")) == pytest.approx(-60.0)


def test_rpm_and_explicit_free_stream(hawt: dict[str, Any]) -> None:
    rotation = hawt["physics"]["rotation"]["rotating"]
    rotation["speed"] = {"kind": "RPM", "rpm": 573.0}
    assert resolve(model(hawt), [], HAWT_MESH).rotation.omega == pytest.approx(
        573 * 2 * math.pi / 60)
    rotation["speed"] = {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 4.0, "radius": 0.5,
                         "free_stream_speed": 10.0}
    assert resolve(model(hawt), [], HAWT_MESH).rotation.omega == pytest.approx(80.0)


def test_time_controls(hawt: dict[str, Any]) -> None:
    hawt["physics"]["time"] = {"degrees_per_step": 0.5, "max_courant": 0.5,
                               "end_revolutions": 2, "write_interval": 90,
                               "write_unit": "DEGREES"}
    time = resolve(model(hawt), [], HAWT_MESH).time
    assert time.step_for_angle == pytest.approx(math.radians(0.5) / 60)
    assert time.end_time == pytest.approx(2 * 2 * math.pi / 60)
    assert time.write_interval == pytest.approx(math.radians(90) / 60)
    # Rotor tip in the zone (r 0.6) at 60 rad/s plus 5 m/s inlet; finest zone cell
    # 0.025 / 2**2 (the rotor's max_level).
    assert time.reference_speed == pytest.approx(60 * 0.6 + 5)
    assert time.smallest_cell == pytest.approx(0.025 / 4)
    assert time.courant_estimate == pytest.approx(41 * time.step_for_angle / 0.00625)
    assert time.courant_estimate is not None and time.courant_estimate > 0.5
    assert time.initial_step == pytest.approx(time.step_for_angle * 0.5
                                              / time.courant_estimate)


def test_patches_come_from_the_mesh(hawt: dict[str, Any]) -> None:
    config = model(hawt)
    kinds = dict(patch_kinds(config, HAWT_MESH))
    assert kinds["rotor"] is PatchKind.MOVING_WALL
    assert kinds["side"] is PatchKind.SLIP
    assert kinds["rotating_outer_stat"] is PatchKind.CONSTRAINT
    with pytest.raises(CaseSetupError, match="stray"):
        patch_kinds(config, (*HAWT_MESH, ("stray", "patch")))


def test_split_body_part_moves(tmp_path: Path) -> None:
    data = configs.pole(tmp_path / "geometry")
    kinds = dict(patch_kinds(model(data), [("pole", "wall"), ("pole_rotating", "wall")]))
    assert kinds == {"pole": PatchKind.WALL, "pole_rotating": PatchKind.MOVING_WALL}


def test_inlet_area_of_generated_faces(tmp_path: Path) -> None:
    hawt = model(configs.hawt(tmp_path / "geometry"))
    assert inlet_area(hawt, [], "inlet") == pytest.approx(math.pi * 2.0**2)
    assert inlet_area(hawt, [], "side") == pytest.approx(2 * math.pi * 2.0 * 7.0)
    box = model(configs.pole(tmp_path / "pole"))
    assert inlet_area(box, [], "inlet") == pytest.approx(3.12 * 4.32)
    assert inlet_area(box, [], "nonexistent") is None


def test_output_is_deterministic(hawt: dict[str, Any]) -> None:
    first = resolve(model(hawt), [], HAWT_MESH)
    again = resolve(model(copy.deepcopy(hawt)), [], HAWT_MESH)
    assert {**fields(first), **dictionaries(first)} == {**fields(again), **dictionaries(again)}


# --- validation (section 10, G6 rows) ---------------------------------------------------------

def codes(data: dict[str, Any], thresholds: CaseSetupThresholds | None = None) -> set[str]:
    config = model(data)
    return {i.code for i in check_physics(config, read_imported(config), thresholds)}


def test_valid_physics_has_no_finding(hawt: dict[str, Any]) -> None:
    hawt["physics"]["time"] = {"max_courant": 5.0}
    assert codes(hawt) == set()
    del hawt["physics"]
    assert codes(hawt) == set()


@pytest.mark.parametrize(("change", "code"), [
    (lambda p: p["rotation"].clear(), "ROTATION_NOT_SET"),
    (lambda p: p["rotation"]["rotating"].pop("direction"), "ROTATION_DIRECTION_NOT_SET"),
    (lambda p: p["rotation"]["rotating"]["speed"].update(tip_speed_ratio=0.0),
     "ROTATION_SPEED_ZERO"),
    (lambda p: p["rotation"].update(other=p["rotation"]["rotating"]), "ROTATION_UNKNOWN_ZONE"),
    (lambda p: p["inlets"].clear(), "INLET_VALUE_NOT_SET"),
    (lambda p: p["outlets"].clear(), "OUTLET_VALUE_NOT_SET"),
    (lambda p: p["inlets"].update(side=p["inlets"]["inlet"]), "INLET_VALUE_NOT_INLET"),
    (lambda p: p["outlets"].update(side={"pressure_pa": 0}), "OUTLET_VALUE_NOT_OUTLET"),
    (lambda p: p["turbulence"].pop("length_scale"), "TURBULENCE_LENGTH_SCALE_NOT_SET"),
    (lambda p: p["inlets"].update(inlet2=p["inlets"]["inlet"]), "INLET_VALUE_NOT_INLET"),
])
def test_blocking_findings(hawt: dict[str, Any], change: Any, code: str) -> None:
    change(hawt["physics"])
    config = model(hawt)
    found = {i.code: i for i in check_physics(config, [])}
    assert code in found and found[code].severity.value == "BLOCKING", found
    assert found[code].stage.value == "CASE_SETUP"


def test_tip_speed_ratio_needs_a_free_stream_speed(hawt: dict[str, Any]) -> None:
    hawt["physics"]["inlets"]["inlet"] = {"kind": "FLOW_RATE", "volumetric_flow_rate": 1.0}
    assert "FREE_STREAM_SPEED_NOT_SET" in codes(hawt)


def test_one_rotating_zone_only(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path / "geometry")
    second = copy.deepcopy(data["rotating_zones"][0])
    second["name"] = "second"
    second["shape"].update(axis_min=1.0, axis_max=1.2)
    second["location_in_mesh"] = configs.vec(1.1, 0.3013, 0.0113)
    data["rotating_zones"].append(second)
    data["physics"] = hawt_physics()
    assert "CASE_SETUP_ONE_ZONE" in codes(data)


def test_step_and_courant_warnings(hawt: dict[str, Any]) -> None:
    hawt["physics"]["time"] = {"degrees_per_step": 3.0, "max_courant": 1.0}
    found = {i.code: i for i in check_physics(model(hawt), [])}
    assert found["ROTATION_STEP_LARGE"].severity.value == "WARNING"
    assert found["COURANT_ESTIMATE_HIGH"].severity.value == "WARNING"
    assert found["COURANT_ESTIMATE_HIGH"].details["courant_estimate"] > 1.0
    assert "ROTATION_STEP_LARGE" not in codes(
        hawt, CaseSetupThresholds(max_degrees_per_step=5.0))


def test_flow_rate_needs_a_measurable_inlet(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path / "geometry")
    data["patches"] = [p if p["name"] != "rotor" else {**p, "type": "INLET"}
                       for p in data["patches"]]  # a body as inlet: no area
    data["physics"] = hawt_physics(inlets={
        "inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0, 0)},
        "rotor": {"kind": "FLOW_RATE", "volumetric_flow_rate": 1.0}})
    found = {i.code for i in check_physics(model(data), [])}
    assert "INLET_AREA_UNKNOWN" in found


# --- configuration -----------------------------------------------------------------------------

def test_physics_is_optional_and_versioned(hawt: dict[str, Any]) -> None:
    assert MACHINE_SCHEMA_VERSION == 2
    old = {k: v for k, v in hawt.items() if k != "physics"}
    old["schema_version"] = 1  # a G1-G5 project: migrates, no physics
    config = model(old)
    assert config.schema_version == 2 and config.physics is None
    round_trip = model(model(hawt).model_dump(mode="json"))
    assert round_trip == model(hawt)
    with pytest.raises(ValueError):
        PhysicsConfig.model_validate({"fluid": AIR.model_dump(), "unknown": 1})


def test_physics_never_reaches_a_meshing_input(hawt: dict[str, Any]) -> None:
    """Invariant 14: the meshing cases are identical with any physics."""
    def meshing(data: dict[str, Any]) -> Any:
        config = model(data)
        meshes, _ = load_bodies(config)
        cases = MachineCaseGenerator().render(config, read_imported(config), meshes,
                                              typed=False)
        return ([(c.name, c.dictionaries, c.surfaces) for c in cases.all], cases.retype)

    other = copy.deepcopy(hawt)
    other["physics"] = hawt_physics(fluid={"density": 998.0, "kinematic_viscosity": 1e-6},
                                    turbulence={"model": "LAMINAR"})
    other["physics"]["rotation"]["rotating"]["direction"] = "NEGATIVE"
    none = {k: v for k, v in hawt.items() if k != "physics"}
    assert meshing(hawt) == meshing(other) == meshing(none)


# --- writing the case ----------------------------------------------------------------------------

def test_case_is_written_beside_the_meshes(tmp_path: Path, hawt: dict[str, Any]) -> None:
    mesh = tmp_path / "mesh/constant/polyMesh"
    mesh.mkdir(parents=True)
    rows = "".join(f"    {n}\n    {{\n        type {t};\n        nFaces 1;\n        startFace 0;\n"
                   "    }\n" for n, t in HAWT_MESH)
    (mesh / "boundary").write_text(f"{len(HAWT_MESH)}\n(\n{rows})\n", "utf-8")
    (mesh / "points").write_text("points", "utf-8")
    (mesh / "sets").mkdir()
    (mesh / "sets/skewFaces").write_text("diagnostics", "utf-8")
    root = tmp_path / "project"
    _, files = render(model(hawt), [], mesh)
    case = write_case(root, files, mesh)

    assert case == root / CASE_DIR
    assert (case / "constant/polyMesh/points").read_text("utf-8") == "points"
    assert not (case / "constant/polyMesh/sets").exists()
    assert {p.relative_to(case).as_posix() for p in (case / "0").iterdir()} == {
        "0/U", "0/p", "0/k", "0/omega", "0/nut"}
    (case / "0.01").mkdir()  # solver output: the user's
    (case / "0.01/U").write_text("result", "utf-8")

    hawt["physics"]["turbulence"] = {"model": "LAMINAR"}
    _, laminar = render(model(hawt), [], mesh)
    write_case(root, laminar, mesh)
    assert sorted(p.name for p in (case / "0").iterdir()) == ["U", "p"]
    assert (case / "0.01/U").read_text("utf-8") == "result"
