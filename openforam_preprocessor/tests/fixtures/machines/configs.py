"""Machine configurations (raw dicts) on the G0 synthetic geometry.

Sizes follow the G0 experiments (tests/fixtures/machines/g0): test values only,
not presets. Each builder writes its STL set into `directory`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.fixtures.machines.g0.common import make_geometry


def stl(path: Path) -> dict[str, Any]:
    return {"source_path": str(path), "source_units": "m"}


def vec(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": x, "y": y, "z": z}


def patch(name: str, kind: str, source: str, ref: str) -> dict[str, Any]:
    return {"name": name, "type": kind, "source": {"kind": source, "ref": ref}}


def hawt(directory: Path) -> dict[str, Any]:
    """G0 R3: rotor R 0.5 on axis x; disc zone r 0.6, x +-0.12; cylinder domain
    r 2, x -2..5 (inlet at x -2)."""
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.hawt(directory)
    return {
        "project_name": "HAWT", "machine": "HAWT", "flow_axis": "x",
        "bodies": [{"name": "rotor", "source": stl(directory / "hawt_rotor.stl"),
                    "motion": "ROTATING", "zone": "rotating"}],
        "rotating_zones": [{
            "name": "rotating", "axis": "x",
            "shape": {"kind": "CYLINDER", "centre_u": 0.0, "centre_v": 0.0,
                      "axis_min": -0.12, "axis_max": 0.12, "diameter": 1.2},
            "cell_size": 0.025, "location_in_mesh": vec(0.0013, 0.3013, 0.0113)}],
        "domain": {"kind": "CYLINDER", "axis": "x", "centre_u": 0.0, "centre_v": 0.0,
                   "axis_min": -2.0, "axis_max": 5.0, "diameter": 4.0, "cell_size": 0.1,
                   "location_in_mesh": vec(-1.4313, 0.0113, 0.0213)},
        "patches": [patch("rotor", "ROTATING_WALL", "BODY", "rotor"),
                    patch("inlet", "INLET", "DOMAIN_FACE", "axis_min"),
                    patch("outlet", "OUTLET", "DOMAIN_FACE", "axis_max"),
                    patch("side", "SLIP", "DOMAIN_FACE", "side")],
    }


def pole(directory: Path, *, motion: str = "SPLIT",
         hole_diameter: float | None = None) -> dict[str, Any]:
    """G0 R5: the fixture rotor's blades (axis z, D 1.04) in a zone r 0.78,
    z +-0.66; a pole r 0.03, z +-2.3, through the SIMPLE preset box (z +-2.16).
    motion SPLIT: split pole; STATIONARY with hole_diameter: annular zone."""
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.pole(directory)
    shape: dict[str, Any] = {"kind": "CYLINDER", "centre_u": 0.0, "centre_v": 0.0,
                             "axis_min": -0.66, "axis_max": 0.66, "diameter": 1.56}
    if hole_diameter is not None:
        shape["hole_diameter"] = hole_diameter
    pole_body: dict[str, Any] = {"name": "pole", "source": stl(directory / "pole.stl"),
                                 "motion": motion}
    if motion != "STATIONARY":
        pole_body["zone"] = "rotating"
    return {
        "project_name": "Pole", "machine": "VAWT_POLE", "flow_axis": "x",
        "bodies": [{"name": "blades", "source": stl(directory / "blades.stl"),
                    "motion": "ROTATING", "zone": "rotating"}, pole_body],
        "rotating_zones": [{"name": "rotating", "axis": "z", "shape": shape,
                            "cell_size": 1.04 / 22,
                            "location_in_mesh": vec(0.65, 0.0113, 0.0137)}],
        "domain": {"kind": "BOX",
                   "bounds": {"minimum": vec(-3.12, -1.56, -2.16),
                              "maximum": vec(7.28, 1.56, 2.16)},
                   "cell_size": 1.04 / 9, "location_in_mesh": vec(-1.95, 0.0113, 0.0137)},
        "patches": [patch("blades", "ROTATING_WALL", "BODY", "blades"),
                    patch("pole", "WALL", "BODY", "pole"),
                    patch("inlet", "INLET", "DOMAIN_FACE", "x_min"),
                    patch("outlet", "OUTLET", "DOMAIN_FACE", "x_max"),
                    *(patch(f"side_{face}", "SLIP", "DOMAIN_FACE", face)
                      for face in ("y_min", "y_max", "z_min", "z_max"))],
    }


def francis(directory: Path) -> dict[str, Any]:
    """G0 R4: casing, guide vanes and draft tube are stationary imported parts,
    the runner an imported zone (axis z); three joints, all AMI."""
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.francis(directory)

    def part(name: str, point: tuple[float, float, float]) -> dict[str, Any]:
        return {"name": name, "format": "NAMED_REGIONS", "files": [stl(directory / f"{name}.stl")],
                "cell_size": 0.02, "location_in_mesh": vec(*point)}

    return {
        "project_name": "Francis", "machine": "FRANCIS",
        "rotating_zones": [{
            "name": "runner", "axis": "z",
            "shape": {"kind": "IMPORTED", "format": "NAMED_REGIONS",
                      "files": [stl(directory / "runner.stl")], "origin": vec(0, 0, 0)},
            "cell_size": 0.02, "location_in_mesh": vec(0.2013, 0.0113, 0.0013)}],
        "domain": {"kind": "IMPORTED", "parts": [
            part("casing", (0.0113, 0.0113, -0.45)), part("guide", (0.0113, 0.0113, -0.2)),
            part("draft", (0.0113, 0.0113, 0.5))]},
        "patches": [patch("inlet", "INLET", "REGION", "inlet"),
                    patch("outlet", "OUTLET", "REGION", "outlet"),
                    *(patch(name, "WALL", "REGION", name)
                      for name in ("casing_wall", "guide_wall", "vanes", "runner_wall",
                                   "draft_wall")),
                    patch("runner_blades", "ROTATING_WALL", "REGION", "runner_blades")],
        "joints": [{"first": "casing_out", "second": "guide_in"},
                   {"first": "guide_out", "second": "runner_in"},
                   {"first": "runner_out", "second": "draft_in"}],
    }


def duct(directory: Path) -> dict[str, Any]:
    """G0 R2's duct as an imported domain, one file per patch (regions known
    from the file names), with a small cylinder zone inside."""
    directory.mkdir(parents=True, exist_ok=True)
    make_geometry.duct(directory)
    return {
        "project_name": "Duct", "machine": "CUSTOM",
        "rotating_zones": [{
            "name": "rotating", "axis": "x",
            # On the duct's centre line (turned 20 deg about z), around x = 0.94.
            "shape": {"kind": "CYLINDER", "centre_u": 0.342, "centre_v": 0.0,
                      "axis_min": 0.89, "axis_max": 0.99, "diameter": 0.2},
            "cell_size": 0.01, "location_in_mesh": vec(0.9413, 0.3923, 0.0113)}],
        "domain": {"kind": "IMPORTED", "parts": [{
            "name": "duct", "format": "ONE_FILE_PER_PATCH",
            "files": [stl(directory / f"{name}.stl") for name in ("inlet", "outlet", "wall")],
            "cell_size": 0.05, "location_in_mesh": vec(0.2813, 0.1013, 0.0113)}]},
        "patches": [patch("inlet", "INLET", "REGION", "inlet"),
                    patch("outlet", "OUTLET", "REGION", "outlet"),
                    patch("wall", "WALL", "REGION", "wall")],
    }
