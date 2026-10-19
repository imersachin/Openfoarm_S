"""Imported-mesh configurations (raw dicts) on the M1 test meshes (tests/fixtures/msh/m1).

Test values only. The meshes are the coarse versions of the E0 geometry:
box x 0..1, y and z 0..0.5 (inlet, outlet, walls); rotating box x -1..3,
y and z -1..1 with a cylinder zone r 0.5 on x (stationary, rotating; the
zone surface twice: ami_stat, ami_rot; the rotor block).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from machines.msh_import.detect import file_sha256
from tests.fixtures.machines.configs import patch, vec

M1 = Path(__file__).parent / "m1"
SMOKE_TIME = {"end_revolutions": 10 / 360, "write_interval": 10, "write_unit": "DEGREES"}
ROTATING_SIDES = ("y_min", "y_max", "z_min", "z_max")


def mesh_file(name: str, directory: Path | None = None) -> Path:
    """A test mesh, copied into `directory` when given (so a test may change it)."""
    source = M1 / name
    if directory is None:
        return source
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(source.read_bytes())
    return target


def physics(zone: str = "rotor_zone") -> dict[str, Any]:
    return {
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "turbulence": {"length_scale": 0.1},
        "rotation": {zone: {"speed": {"kind": "RPM", "rpm": 300.0}, "direction": "POSITIVE"}},
        "inlets": {"inlet": {"kind": "VELOCITY", "velocity": vec(5.0, 0.0, 0.0)}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
        "time": copy.deepcopy(SMOKE_TIME),
    }


def rotating(path: Path, *, units: str = "m", with_physics: bool = True,
             ami: tuple[str, str] = ("ami_stat", "ami_rot"),
             cell_zone: str = "rotating") -> dict[str, Any]:
    """The rotating test mesh (Gmsh rotating_41.msh or Fluent fluent_rotating.msh)."""
    data: dict[str, Any] = {
        "project_name": "msh rotating", "machine": "CUSTOM", "rotating_zones": [],
        "imported_mesh": {
            "source_path": str(path), "source_units": units, "sha256": file_sha256(path),
            "zones": [{"name": "rotor_zone", "cell_zone": cell_zone, "axis": "x",
                       "origin": vec(0, 0, 0)}],
            "ami_pairs": [{"first": ami[0], "second": ami[1]}]},
        "patches": [patch("inlet", "INLET", "MESH_PATCH", "inlet"),
                    patch("outlet", "OUTLET", "MESH_PATCH", "outlet"),
                    *(patch(n, "SLIP", "MESH_PATCH", n) for n in ROTATING_SIDES),
                    patch("rotor", "ROTATING_WALL", "MESH_PATCH", "rotor")],
    }
    if with_physics:
        data["physics"] = physics()
    return data


def box(path: Path, *, units: str = "m") -> dict[str, Any]:
    """The box test mesh: no rotating zone (for the file and patch checks)."""
    return {
        "project_name": "msh box", "machine": "CUSTOM", "rotating_zones": [],
        "imported_mesh": {"source_path": str(path), "source_units": units,
                          "sha256": file_sha256(path)},
        "patches": [patch("inlet", "INLET", "MESH_PATCH", "inlet"),
                    patch("outlet", "OUTLET", "MESH_PATCH", "outlet"),
                    patch("walls", "WALL", "MESH_PATCH", "walls")],
    }
