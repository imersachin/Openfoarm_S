"""E4 helpers: the G7 folder checks and the G6 case setup on an imported mesh.

    python e4_case.py folder <case> <checkMesh log>
    python e4_case.py setup <mesh case> <project root>

There is no imported-mesh configuration yet (M1b). For case setup, the
configuration describes the same geometry the Gmsh file was built from
(make_gmsh.py rotating): a box domain and a cylinder zone, so the existing
G6 code can read the zone radius and inlet area. Patch names match the
mesh's patches; the zone name matches its cell zone. Evidence only.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import trimesh

from case_setup.case import render, write_case
from machines.mesh_folder import MeshExpectation, check_mesh_folder
from machines.validation import parse_config
from vawt.config import StrictMeshQualityLimits

POLY_MESH = "constant/polyMesh"
SMOKE_TIME = {"end_revolutions": 10 / 360, "write_interval": 10, "write_unit": "DEGREES"}


def vec(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": x, "y": y, "z": z}


def patch(name: str, kind: str, source: str, ref: str) -> dict[str, Any]:
    return {"name": name, "type": kind, "source": {"kind": source, "ref": ref}}


def folder(case: Path, log: Path) -> None:
    expected = MeshExpectation(
        name="imported",
        patches={"inlet": "patch", "outlet": "patch", "y_min": "patch", "y_max": "patch",
                 "z_min": "patch", "z_max": "patch", "rotor": "wall",
                 "AMI1": "cyclicAMI", "AMI2": "cyclicAMI"},
        cell_zones=("rotating", "stationary"), regions=2, ami_pairs=(("AMI1", "AMI2"),))
    report = check_mesh_folder(case, expected=expected, limits=StrictMeshQualityLimits(),
                               check_mesh_log=log.read_text(encoding="utf-8"))
    print("patches:", ", ".join(f"{p.name} {p.patch_type} {p.n_faces}" for p in report.patches))
    print("cell zones:", ", ".join(report.cell_zones))
    print("AMI pairs:", report.ami_pairs)
    print("regions:", report.regions)
    quality = report.quality
    print("validity:", None if quality is None else quality.validity.value,
          "quality:", None if quality is None else quality.quality.value)
    for issue in report.issues:
        print(f"issue: {issue.severity.value} {issue.code}: {issue.message}")
    if not report.issues:
        print("issues: none")


def config(root: Path) -> dict[str, Any]:
    rotor = root / "inputs" / "rotor.stl"
    rotor.parent.mkdir(parents=True, exist_ok=True)
    box = trimesh.creation.box(extents=(0.1, 0.8, 0.1))
    box.export(rotor)
    faces = {"inlet": ("INLET", "x_min"), "outlet": ("OUTLET", "x_max"),
             "y_min": ("SLIP", "y_min"), "y_max": ("SLIP", "y_max"),
             "z_min": ("SLIP", "z_min"), "z_max": ("SLIP", "z_max")}
    return {
        "project_name": "E4", "machine": "CUSTOM", "flow_axis": "x",
        "bodies": [{"name": "rotor", "source": {"source_path": str(rotor), "source_units": "m"},
                    "motion": "ROTATING", "zone": "rotating"}],
        "rotating_zones": [{
            "name": "rotating", "axis": "x",
            "shape": {"kind": "CYLINDER", "centre_u": 0.0, "centre_v": 0.0,
                      "axis_min": -0.2, "axis_max": 0.2, "diameter": 1.0},
            "cell_size": 0.05, "location_in_mesh": vec(0.1, 0.3, 0.3)}],
        "domain": {"kind": "BOX", "bounds": {"minimum": vec(-1, -1, -1), "maximum": vec(3, 1, 1)},
                   "cell_size": 0.15, "location_in_mesh": vec(2, 0.5, 0.5)},
        "patches": [*(patch(n, k, "DOMAIN_FACE", r) for n, (k, r) in faces.items()),
                    patch("rotor", "ROTATING_WALL", "BODY", "rotor")],
        "physics": {
            "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
            "turbulence": {"length_scale": 0.1},
            "rotation": {"rotating": {"speed": {"kind": "RPM", "rpm": 300.0},
                                      "direction": "POSITIVE"}},
            "inlets": {"inlet": {"kind": "VELOCITY", "velocity": vec(5.0, 0.0, 0.0)}},
            "outlets": {"outlet": {"pressure_pa": 0.0}},
            "time": SMOKE_TIME,
        },
    }


def digest(mesh: Path) -> dict[str, str]:
    return {str(p.relative_to(mesh)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(mesh.rglob("*")) if p.is_file() and "sets" not in p.parts}


def setup(mesh_case: Path, root: Path) -> None:
    parsed, issues = parse_config(config(root))
    for issue in issues:
        print(f"config issue: {issue.code}: {issue.message}")
    assert parsed is not None
    mesh = mesh_case / POLY_MESH
    before = digest(mesh)
    case_setup, files = render(parsed, [], mesh)
    case = write_case(root, files, mesh)
    after = digest(mesh)
    print(f"omega {case_setup.rotation.omega:.6f} rad/s, radius {case_setup.rotation.radius}, "
          f"zone {case_setup.rotation.zone}")
    print("patch kinds:", ", ".join(f"{n} {k.value}" for n, k in case_setup.patches))
    print("files:", ", ".join(sorted(files)))
    print(f"imported mesh files unchanged by case setup: {before == after} "
          f"({len(before)} files)")
    copied = digest(case / POLY_MESH)
    print(f"case mesh is a byte-identical copy: {copied == before}")


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    if command == "folder":
        folder(Path(args[0]), Path(args[1]))
    else:
        setup(Path(args[0]), Path(args[1]))
