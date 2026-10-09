"""G3 assembled meshes against real OpenFOAM v2512, on the G0 geometry.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_machine_assembly_real.py -v

Each case runs machines.assembly.meshing_steps (domain, every zone, merge,
createPatch, checkMesh, postProcess AMIWeights) and the result checks. The
last tests break steps on purpose to show each result check firing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from machines.assembly import MachineCaseGenerator, MachineCases, meshing_steps
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.mesh_checks import cell_zone_names, check_assembly, parse_ami_weights
from machines.validation import load_bodies, validate_machine
from mesh.parser import CheckMeshParser
from tests.fixtures.machines import configs
from tests.fixtures.machines.g0.common import make_geometry
from visualization.foam_reader import read_boundary

TOOLS = ("blockMesh", "snappyHexMesh", "surfaceFeatureExtract", "checkMesh", "topoSet",
         "mergeMeshes", "createPatch", "postProcess")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
GEN = MachineCaseGenerator()


class Result:
    def __init__(self, root: Path, cases: MachineCases, logs: dict[str, str]) -> None:
        self.root, self.cases, self.logs = root, cases, logs
        self.merged = root / cases.merged.root

    def faces(self) -> dict[str, tuple[str, int]]:
        return {p.name: (p.patch_type, p.n_faces)
                for p in read_boundary(self.merged / "constant/polyMesh/boundary")}

    def checks(self, **kwargs: Any) -> list[tuple[str, str]]:
        issues = check_assembly(self.cases, self.merged, self.logs["merged_checkMesh"],
                                self.logs.get("merged_amiWeights"), **kwargs)
        return [(i.code, i.severity.value) for i in issues]


def build(root: Path, data: dict[str, Any], skip: tuple[str, ...] = ()) -> Result:
    result = validate_machine(data)
    stops = [(i.code, i.message) for i in result.issues if i.is_stopping]
    assert not stops, stops
    config = MachineProjectConfig.model_validate(data)
    meshes, _ = load_bodies(config)
    cases = GEN.render(config, read_imported(config), meshes)
    GEN.write(root, cases)
    logs: dict[str, str] = {}
    for step in meshing_steps(cases):
        if step.name in skip:
            continue
        case = root / step.case
        if step.kind == "copy_mesh":
            shutil.copytree(root / step.source / "constant/polyMesh",
                            case / "constant/polyMesh")
            continue
        done = subprocess.run(step.argv, capture_output=True, text=True, check=False, cwd=case)
        logs[step.name] = done.stdout + done.stderr
        (case / f"{step.name}.log").write_text(logs[step.name], "utf-8")
        assert done.returncode == 0, f"{step.name} failed:\n{done.stderr[-3000:]}"
    return Result(root, cases, logs)


def ok(result: Result) -> None:
    metrics = CheckMeshParser().parse_text(result.logs["merged_checkMesh"])
    assert metrics.mesh_ok, result.logs["merged_checkMesh"][-3000:]


# --- machines ---------------------------------------------------------------------------------

def hawt(directory: Path, domain: str = "CYLINDER") -> dict[str, Any]:
    data = configs.hawt(directory)
    data["domain"]["cell_size"] = 0.125
    if domain == "BOX":
        data["domain"] = {"kind": "BOX", "cell_size": 0.125,
                          "bounds": {"minimum": configs.vec(-2, -2, -2),
                                     "maximum": configs.vec(5, 2, 2)},
                          "location_in_mesh": configs.vec(-1.4313, 0.0113, 0.0213)}
        data["patches"] = [data["patches"][0],
                           configs.patch("inlet", "INLET", "DOMAIN_FACE", "x_min"),
                           configs.patch("outlet", "OUTLET", "DOMAIN_FACE", "x_max"),
                           *(configs.patch(f, "SLIP", "DOMAIN_FACE", f)
                             for f in ("y_min", "y_max", "z_min", "z_max"))]
    return data


@pytest.mark.parametrize("domain", ["CYLINDER", "BOX"])
def test_hawt_rotor_in_its_disc_zone(tmp_path: Path, domain: str) -> None:
    result = build(tmp_path / "project", hawt(tmp_path / "geometry", domain))
    faces = result.faces()

    ok(result)
    assert faces["rotating_outer_stat"][0] == faces["rotating_outer_rot"][0] == "cyclicAMI"
    assert faces["rotating_outer_stat"][1] > 0 and faces["rotating_outer_rot"][1] > 0
    assert faces["rotor"] == ("wall", faces["rotor"][1]) and faces["rotor"][1] > 0
    assert not any(name.endswith("_src") and n for name, (_, n) in faces.items())
    assert cell_zone_names(result.merged) == {"rotating"}
    assert result.checks() == []
    (weights,) = parse_ami_weights(result.logs["merged_amiWeights"])
    assert 0.85 <= weights.minimum <= weights.maximum <= 1.5


def test_split_pole(tmp_path: Path) -> None:
    result = build(tmp_path / "project", configs.pole(tmp_path / "geometry", motion="SPLIT"))
    faces = result.faces()

    ok(result)
    assert faces["pole"][1] > 0 and faces["pole_rotating"] == ("wall", faces["pole_rotating"][1])
    assert faces["pole_rotating"][1] > 0
    assert {"rotating_outer_stat", "rotating_outer_rot"} <= set(faces)
    # Known since G0 R5: a body crossing an interface lowers the AMI weights.
    # Here 0.52 at the start position (G0: 0.83); the warning reports it.
    assert result.checks() == [("AMI_WEIGHTS_OUT_OF_RANGE", "WARNING")]
    (weights,) = parse_ami_weights(result.logs["merged_amiWeights"])
    assert weights.minimum < 0.85


def test_stationary_pole_in_an_annular_zone(tmp_path: Path) -> None:
    data = configs.pole(tmp_path / "geometry", motion="STATIONARY", hole_diameter=0.16)
    result = build(tmp_path / "project", data)
    faces = result.faces()

    ok(result)
    pairs = ("rotating_outer_stat", "rotating_outer_rot", "rotating_inner_stat",
             "rotating_inner_rot")
    assert all(faces[p][0] == "cyclicAMI" and faces[p][1] > 0 for p in pairs)
    assert faces["pole"][1] > 0 and "pole_rotating" not in faces
    assert result.checks() == []
    assert len(parse_ami_weights(result.logs["merged_amiWeights"])) == 2


def multi_body(directory: Path) -> dict[str, Any]:
    """The G0 HAWT rotor as two rotating bodies (hub, blades) with layers on both,
    and a stationary tower downstream of the zone."""
    data = hawt(directory)
    rotor = trimesh.load(directory / "hawt_rotor.stl", force="mesh")
    parts = sorted(rotor.split(only_watertight=False), key=lambda m: -m.volume)
    parts[0].export(directory / "hub.stl")
    trimesh.util.concatenate(parts[1:]).export(directory / "blades.stl")
    tower = make_geometry.closed_cylinder(0.05, -1.9, -0.3)
    tower.apply_translation((0.5, 0.0, 0.0))
    tower.export(directory / "tower.stl")
    layers = {"enabled": True, "count": 2}
    data["bodies"] = [
        {"name": "hub", "source": configs.stl(directory / "hub.stl"), "motion": "ROTATING",
         "zone": "rotating", "layers": layers},
        {"name": "blades", "source": configs.stl(directory / "blades.stl"),
         "motion": "ROTATING", "zone": "rotating", "layers": layers},
        {"name": "tower", "source": configs.stl(directory / "tower.stl"),
         "motion": "STATIONARY"}]
    data["patches"] = [configs.patch("hub", "ROTATING_WALL", "BODY", "hub"),
                       configs.patch("blades", "ROTATING_WALL", "BODY", "blades"),
                       configs.patch("tower", "WALL", "BODY", "tower"), *data["patches"][1:]]
    return data


def test_several_bodies_rotating_and_stationary(tmp_path: Path) -> None:
    result = build(tmp_path / "project", multi_body(tmp_path / "geometry"))
    faces = result.faces()
    snappy = result.logs["zone_rotating_snappyHexMesh"]

    ok(result)
    assert all(faces[p][0] == "wall" and faces[p][1] > 0 for p in ("hub", "blades", "tower"))
    assert result.checks() == []
    # snappyHexMesh's layer table: patch, faces, target layers, mean layers, ...
    rows = {m[1]: (int(m[2]), float(m[3])) for m in re.finditer(
        r"^(hub|blades)\s+\d+\s+(\d+)\s+([\d.]+)", snappy, re.M)}
    assert rows.keys() == {"hub", "blades"}
    assert all(target == 2 and mean > 0.5 for target, mean in rows.values())
    assert np.all([faces[p][1] > 0 for p in ("inlet", "outlet", "side")])


# --- result checks firing ---------------------------------------------------------------------

def test_result_checks_fire_on_broken_assemblies(tmp_path: Path) -> None:
    data = hawt(tmp_path / "geometry")
    no_zone = build(tmp_path / "no_topoSet", data, skip=("zone_rotating_topoSet",))
    not_merged = build(tmp_path / "not_merged", data, skip=("merged_add_zone_rotating",))

    assert ("CELL_ZONE_MISSING", "ERROR") in no_zone.checks()
    found = not_merged.checks()
    assert ("REGION_COUNT_UNEXPECTED", "ERROR") in found  # 1 region, 2 parts
    assert ("DOMAIN_PATCH_MISSING", "ERROR") in found  # the rotor and the zone side
    assert ("CELL_ZONE_MISSING", "ERROR") in found
    assert ("AMI_WEIGHTS_OUT_OF_RANGE", "WARNING") in no_zone.checks(ami_range=(0.999, 1.001))
