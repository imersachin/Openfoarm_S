"""G6 on real OpenFOAM v2512: mesh, case setup, and a solver smoke test.

**Synthetic geometry only** (the G0 HAWT rotor and the G0 R4 Francis
passage). The solver runs a few time steps: a run that completes shows the
case is consistent, not that its results are correct (invariant 15).

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_case_setup_real.py -v -s
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from case_setup.case import CASE_DIR
from case_setup.setup import inlet_area
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.operations import (
    ASSEMBLE,
    CASE_SETUP,
    CHECK_MESH,
    MACHINE_EXECUTABLES,
    VALIDATE,
    VALIDATE_MESH,
    mesh_operation,
)
from machines.pipeline import CASE_SETUP_REPORT, MachinePipeline, MachineRunResult
from tests.fixtures.machines import configs
from tests.integration.test_francis_pipeline_real import francis_config
from visualization.foam_reader import read_points

SOLVER_TOOLS = ("pimpleFoam", "foamDictionary", "foamFormatConvert", "postProcess")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in (*MACHINE_EXECUTABLES,
                                                              *SOLVER_TOOLS)),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
DOMAIN, ZONE = mesh_operation("domain"), mesh_operation("zone_rotating")
SMOKE_TIME = {"end_revolutions": 10 / 360, "write_interval": 10, "write_unit": "DEGREES"}


def run(command: list[str], case: Path) -> subprocess.CompletedProcess[str]:
    # From the case directory (G0 G11: the repository's name ends in "processor").
    return subprocess.run(command, cwd=case, capture_output=True, text=True, timeout=1800,
                          check=False)


def solve(case: Path) -> dict[str, Any]:
    """pimpleFoam to the end time; the log's step count, Courant numbers and
    AMI weights."""
    result = run(["pimpleFoam"], case)
    log = result.stdout + result.stderr
    (case / "log.pimpleFoam").write_text(log, "utf-8")
    assert result.returncode == 0, log[-3000:]
    assert re.search(r"^End\s*$", log, re.M), log[-3000:]
    courant = [float(m) for m in re.findall(r"Courant Number mean: \S+ max: (\S+)", log)]
    weights = [(float(a), float(b)) for a, b in re.findall(
        r"sum\(weights\) min:(\S+) max:(\S+)", log)]
    return {"steps": len(re.findall(r"^Time = ", log, re.M)), "courant": courant,
            "weights": weights}


def digest(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def rotation_angles(case: Path, axis: int) -> tuple[float, np.ndarray]:
    """(last time, angle each moved point turned about the axis), as G0's
    check_rotation.sh: the points are converted to ASCII in a copy."""
    copied = case.parent / "rotation_check"
    shutil.rmtree(copied, ignore_errors=True)
    shutil.copytree(case, copied)
    assert run(["foamDictionary", "system/controlDict", "-entry", "writeFormat", "-set",
                "ascii"], copied).returncode == 0
    times = sorted((p for p in copied.iterdir() if re.fullmatch(r"[\d.e+-]+", p.name)
                    and float(p.name) > 0), key=lambda p: float(p.name))
    last = times[-1]
    assert run(["foamFormatConvert", "-time", last.name], copied).returncode == 0
    before = read_points(copied / "constant/polyMesh/points")
    after = read_points(last / "polyMesh/points")
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    moved = np.linalg.norm(after - before, axis=1) > 1e-9
    moved &= np.hypot(before[:, i], before[:, j]) > 0.05
    turned = np.angle(np.exp(1j * (np.arctan2(after[moved, j], after[moved, i])
                                   - np.arctan2(before[moved, j], before[moved, i]))))
    return float(last.name), np.degrees(turned)


def hawt_physics(**changes: Any) -> dict[str, Any]:
    """G0 R3's inputs (air, 5 m/s, tip-speed ratio 6 on R 0.5: 60 rad/s); 10 degrees."""
    physics: dict[str, Any] = {
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "turbulence": {"length_scale": 0.1},
        "rotation": {"rotating": {"speed": {"kind": "TIP_SPEED_RATIO", "tip_speed_ratio": 6.0,
                                            "radius": 0.5}, "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "VELOCITY", "velocity": configs.vec(5.0, 0.0, 0.0)}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
        "time": SMOKE_TIME,
    }
    physics.update(changes)
    return physics


def timed(pipeline: MachinePipeline, root: Path,
          data: dict[str, Any]) -> tuple[MachineRunResult, float]:
    start = time.monotonic()
    result = pipeline.run_sync(root, MachineProjectConfig.model_validate(data))
    return result, time.monotonic() - start


def test_hawt_case_runs_and_physics_reuses_the_mesh(tmp_path: Path) -> None:
    data = {**configs.hawt(tmp_path / "geometry"), "physics": hawt_physics()}
    root = tmp_path / "project"
    pipeline = MachinePipeline()

    first, _ = timed(pipeline, root, data)
    assert first.succeeded, [(i.code, i.message) for i in first.issues if i.is_stopping]
    assert first.executed[-1] == CASE_SETUP
    case = root / CASE_DIR

    # k-omega SST: the solver completes the steps; the time step adjusts to maxCo 2.
    sst = solve(case)
    assert sst["steps"] >= 10
    # maxCo is enforced from the previous step's Courant number: starting from
    # rest, one early step overshoots (3.8 seen) before the step settles at 2.
    assert sst["courant"][-1] <= 2.0 * 1.05, sst["courant"]
    assert all(0.85 <= low <= high <= 1.5 for low, high in sst["weights"]), sst["weights"]
    # Rotation: POSITIVE turns the zone right-handed about +x by omega * t (G0 sign).
    end, turned = rotation_angles(case, axis=0)
    assert turned.size > 1000
    assert np.allclose(turned, math.degrees(60.0 * end), atol=1e-3), (turned.min(),
                                                                       turned.max())
    print(f"\nHAWT SST: {sst['steps']} steps, max Courant {max(sst['courant']):.3g} "
          f"(last {sst['courant'][-1]:.3g}), "
          f"AMI {min(w[0] for w in sst['weights']):.3g}-{max(w[1] for w in sst['weights']):.3g}, "
          f"turned {turned.mean():.4f} deg (omega t = {math.degrees(60.0 * end):.4f})")

    # Physics only (laminar): case setup re-runs; every mesh file is byte-identical.
    meshes = digest(root / "cases")
    laminar = copy.deepcopy(data)
    laminar["physics"]["turbulence"] = {"model": "LAMINAR"}
    changed, seconds = timed(pipeline, root, laminar)
    assert changed.succeeded
    assert changed.executed == (VALIDATE, "machine_generate_cases", VALIDATE_MESH, CASE_SETUP)
    assert set(changed.reused) == {DOMAIN, ZONE, ASSEMBLE, CHECK_MESH}
    assert digest(root / "cases") == meshes
    assert sorted(p.name for p in (case / "0").iterdir()) == ["U", "p"]
    for stale in (p for p in case.iterdir() if re.fullmatch(r"[\d.e+-]+", p.name)
                  and p.name != "0"):
        shutil.rmtree(stale)  # the SST results: not for this setup
    assert solve(case)["steps"] >= 10
    print(f"HAWT physics-only change: {seconds:.2f} s, executed {changed.executed}")

    # Patch type only (P2, H3 kept): no snappyHexMesh part mesh re-runs.
    walls = copy.deepcopy(laminar)
    walls["patches"][3]["type"] = "WALL"  # side: SLIP -> WALL
    typed, seconds = timed(pipeline, root, walls)
    assert typed.succeeded
    assert set(typed.reused) == {DOMAIN, ZONE}
    assert {ASSEMBLE, CHECK_MESH, CASE_SETUP} <= set(typed.executed)
    assert typed.run_record_path is not None
    record = json.loads(typed.run_record_path.read_text("utf-8"))
    commands = [(Path(c["argv"][0]).name, round(c["duration_seconds"], 2))
                for c in record["commands"]]
    assert {name for name, _ in commands} <= {"mergeMeshes", "createPatch", "foamDictionary",
                                              "checkMesh", "postProcess"}
    print(f"HAWT patch-type change: {seconds:.2f} s in all; commands {commands}")


def test_francis_flow_rate_inlet(tmp_path: Path) -> None:
    """P4: flowRateInletVelocity on the G0 R4 passage, Q = 1 m/s x the inlet
    area (G0 R4's inlet speed). The inlet carries Q after the steps."""
    config = francis_config(tmp_path / "geometry")
    data = config.model_dump(mode="json")
    root = tmp_path / "project"
    pipeline = MachinePipeline()
    area = inlet_area(config, read_imported(config), "inlet")
    assert area is not None
    flow = 1.0 * area
    data["physics"] = {
        "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
        "turbulence": {"length_scale": 0.03},
        "rotation": {"runner": {"speed": {"kind": "RPM", "rpm": 20 * 60 / (2 * math.pi)},
                                "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": flow}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
        "time": SMOKE_TIME,
    }

    result, _ = timed(pipeline, root, data)
    assert result.succeeded, [(i.code, i.message) for i in result.issues if i.is_stopping]
    report = json.loads((root / CASE_SETUP_REPORT).read_text("utf-8"))
    assert report["inlets"]["inlet"]["speed"] == pytest.approx(1.0)
    case = root / CASE_DIR
    solved = solve(case)
    assert solved["steps"] >= 10
    assert all(0.85 <= low <= high <= 1.5 for low, high in solved["weights"])

    fluxes = {}
    for patch in ("inlet", "outlet"):
        out = run(["postProcess", "-func", f"flowRatePatch(name={patch})", "-latestTime"], case)
        assert out.returncode == 0, out.stdout[-2000:]
        match = re.search(rf"sum\({patch}\) of phi = (\S+)", out.stdout)
        assert match, out.stdout[-2000:]
        fluxes[patch] = float(match[1])
    assert fluxes["inlet"] == pytest.approx(-flow, rel=1e-5)  # inflow is negative; 6 digits printed
    assert fluxes["outlet"] == pytest.approx(flow, rel=1e-3)
    print(f"\nFrancis flow rate {flow:.6g} m3/s: {solved['steps']} steps, max Courant "
          f"{max(solved['courant']):.3g}, inlet {fluxes['inlet']:.6g}, "
          f"outlet {fluxes['outlet']:.6g}")
