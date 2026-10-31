"""C1 on real OpenFOAM v2512: an imported machine's case, as the app writes it,
runs from its Allrun with each start-up (the solver smoke test is in the tests
only, CLAUDE.md), with the guide vanes' swirl at its inlet.

The M2b fixture machine (casing, vanes, runner, outer ring; synthetic, tiny):
the outer ring's far side is a cylinder about the runner's axis, so it takes
the swirl inlet. Two transient steps on 2 ranks; the steady MRF start 5
iterations.

    pytest -m openfoam tests/integration/test_c1_startup_real.py -v -s
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from machines.msh_import.solve_estimate import outer_loop
from machines.service import MachineService
from tests.fixtures.machines.configs import patch
from tests.integration.test_m2b_parts_real import four_parts

TOOLS = ("fluent3DMeshToFoam", "topoSet", "createPatch", "mergeMeshes", "stitchMesh",
         "checkMesh", "decomposePar", "potentialFoam", "simpleFoam", "pimpleFoam",
         "reconstructPar", "foamListTimes", "mpirun")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


def machine(tmp_path: Path, method: str) -> MachineService:
    """The fixture machine with interfaces as in M2b's real test, a swirl inlet on
    the outer ring and the start-up `method`; meshed and set up."""
    service = MachineService(tmp_path / "project")
    data = four_parts(service)
    found = service.interface_candidates(data)
    assert found is not None
    joint = {("CS", "GV"): "AMI", ("GV", "RU"): "ACMI", ("CS", "OR"): "STITCH"}
    data["imported_mesh"]["interfaces"] = [
        {"name": c.name(), "first": list(c.first), "second": list(c.second),
         "joint": joint[(c.parts[0], c.parts[1])]} for c in found if c.suggested]
    taken = {n for c in found for n in (*c.first, *c.second)}
    data["patches"] = [p for p in data["patches"]
                       if p["source"]["ref"] not in taken and p["name"] != "inlet"]
    data["patches"] += [patch("inlet", "INLET", "MESH_PATCH", "OR_far"),
                        *(patch(f"OR_{w}", "WALL", "MESH_PATCH", f"OR_{w}")
                          for w in ("bottom", "top"))]
    data["physics"] = {
        "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
        "turbulence": {"length_scale": 0.05},
        "rotation": {"RU": {"speed": {"kind": "RPM", "rpm": 60.0}, "direction": "NEGATIVE"}},
        "inlets": {"inlet": {"kind": "SWIRL", "volumetric_flow_rate": 0.5,
                             "flow_angle_deg": 20.0, "direction": "NEGATIVE"}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
        "time": {"end_revolutions": 2 / 360, "write_interval": 2, "write_unit": "DEGREES"},
        "start_up": {"method": method, "mrf_iterations": 5},
        "ranks": 2}
    service.save(data)
    config = service.saved_config()
    assert config is not None
    result = service.pipeline.run_sync(service.root, config)
    assert result.succeeded, (result.message, [i.message for i in result.issues])
    assert "RUNNER_AGAINST_SWIRL" not in {i.code for i in result.issues}
    return service


def allrun(case: Path) -> subprocess.CompletedProcess[str]:
    """./Allrun as the owner runs it: the script itself (its shebang), the
    environment without Open MPI's root allowance (Allrun sets it)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OMPI_ALLOW")}
    return subprocess.run(["./Allrun"], cwd=case, capture_output=True, text=True,
                          timeout=600, env=env)


@pytest.mark.parametrize("method", ["POTENTIAL_FLOW", "STEADY_MRF", "FROM_REST"])
def test_each_start_up_runs_from_the_allrun(tmp_path: Path, method: str) -> None:
    service = machine(tmp_path, method)
    case = service.root / "case"
    report = json.loads((service.root / "reports/case_setup.json").read_text("utf-8"))
    swirl = report["inlets"]["inlet"]["swirl"]
    assert swirl["radial_velocity"] < 0 and swirl["rpm"] < 0  # inwards, clockwise about +z
    assert swirl["label"] == "approximate (from vane trailing-edge angle)"
    assert "cylindricalInletVelocity" in (case / "0/U").read_text("utf-8")

    run = allrun(case)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert run.stderr == ""  # no shell errors (a header above the shebang gave three)
    again = allrun(case)  # an earlier run's output: refused, nothing reused
    assert again.returncode == 1 and "An earlier run's output is here" in again.stderr
    solver = (case / "log.pimpleFoam").read_text("utf-8")
    assert "FOAM FATAL" not in solver and "\nEnd\n" in solver, solver[-3000:]
    found = outer_loop(solver)
    assert found is not None and found.steps == 2
    assert solver.count("PIMPLE: iteration 1\n") == 2  # the outer loop runs each step
    started = {"POTENTIAL_FLOW": case / "log.potentialFoam",
               "STEADY_MRF": case / "mrf/log.simpleFoam"}
    for name, log in started.items():
        assert log.is_file() is (name == method), name
    if method == "STEADY_MRF":
        assert "SIMPLE solution" in (case / "mrf/log.simpleFoam").read_text("utf-8") or \
            "End" in (case / "mrf/log.simpleFoam").read_text("utf-8")
        assert (case / "mrf/constant/polyMesh").is_symlink()
        # 0/ holds the MRF solve's last fields (binary, as simpleFoam wrote them)
        assert (case / "0/U").read_bytes() != (case / "mrf/0/U").read_bytes()

    if method == "POTENTIAL_FLOW":  # a failing step stops the run order
        for done in [*case.glob("log.*"), *case.glob("processor*")]:
            shutil.rmtree(done) if done.is_dir() else done.unlink()
        (case / "0/p").unlink()
        failed = allrun(case)
        assert failed.returncode != 0
        assert not (case / "log.pimpleFoam").exists()

    # A later setup removes the start-up's own files (and keeps the solver's).
    if method == "STEADY_MRF":
        raw = service.saved_config()
        assert raw is not None
        data = raw.model_dump(mode="json")
        data["physics"]["start_up"]["method"] = "FROM_REST"
        service.save(data)
        config = service.saved_config()
        assert config is not None
        assert service.pipeline.run_sync(service.root, config).succeeded
        assert not (case / "mrf/system/controlDict").exists()
        assert (case / "mrf/log.simpleFoam").is_file()
