"""Fixture rotor generator: a shaft plus four box blades, with exactly known metrics.

Built along z, then turned onto the requested axis by a cyclic permutation of
coordinates (a proper rotation, so winding is preserved).

    D = 2 * blade_radius + blade_thickness     (largest extent normal to the axis)
    H = shaft_length                           (extent along the axis)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

BLADE_COUNT = 4


@dataclass(frozen=True)
class RotorSpec:
    blade_radius: float = 0.5  # m, blade centre to axis
    blade_chord: float = 0.2
    blade_thickness: float = 0.04
    blade_span: float = 1.0
    shaft_radius: float = 0.03
    shaft_length: float = 1.2
    axis: str = "z"
    centre: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def diameter(self) -> float:
        return 2 * self.blade_radius + self.blade_thickness

    @property
    def span(self) -> float:
        return self.shaft_length


DEFAULT_SPEC = RotorSpec()

# z -> axis by cyclic permutation: new[i] = old[_PERMUTATION[axis][i]]
_PERMUTATION = {"z": (0, 1, 2), "x": (2, 0, 1), "y": (1, 2, 0)}


def rotor_bodies(spec: RotorSpec = DEFAULT_SPEC) -> list[trimesh.Trimesh]:
    """Bodies in a fixed order: shaft, then blades at +u, -u, +v, -v (z-frame x/y)."""
    r, c, t, s = spec.blade_radius, spec.blade_chord, spec.blade_thickness, spec.blade_span
    bodies = [trimesh.creation.cylinder(radius=spec.shaft_radius, height=spec.shaft_length,
                                        sections=32)]
    for centre, extents in (((r, 0, 0), (t, c, s)), ((-r, 0, 0), (t, c, s)),
                            ((0, r, 0), (c, t, s)), ((0, -r, 0), (c, t, s))):
        blade = trimesh.creation.box(extents=extents)
        blade.apply_translation(centre)
        bodies.append(blade)
    order = _PERMUTATION[spec.axis]
    for body in bodies:
        body.vertices = np.asarray(body.vertices)[:, order] + np.asarray(spec.centre)
    return bodies


def rotor_mesh(spec: RotorSpec = DEFAULT_SPEC, *, invert_body: int | None = None,
               scale: float = 1.0) -> trimesh.Trimesh:
    bodies = rotor_bodies(spec)
    if invert_body is not None:
        bodies[invert_body].invert()
    mesh = trimesh.util.concatenate(bodies)
    mesh.vertices = np.asarray(mesh.vertices) * scale
    return mesh


def write_rotor(path: Path, spec: RotorSpec = DEFAULT_SPEC, *, scale: float = 1.0,
                invert_body: int | None = None, binary_solid_header: bool = False) -> Path:
    """Write the fixture rotor as binary STL.

    scale: e.g. 100 for a copy whose numbers are in centimetres.
    binary_solid_header: a binary STL whose 80-byte header starts with "solid",
    which naive readers mistake for ASCII.
    """
    data = rotor_mesh(spec, invert_body=invert_body, scale=scale).export(file_type="stl")
    if binary_solid_header:
        data = b"solid fixture rotor (binary)".ljust(80, b" ") + data[80:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
