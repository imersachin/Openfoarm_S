"""Fixture rotor refined to a target triangle count (V0 baselines only).

Same shapes as tests/fixtures/vawt/rotor.py; the shaft gets more sections and
every face is split k times (each split x4), so the result stays closed and
outward-wound. The count is close to, not exactly, the target.
"""

from __future__ import annotations

import numpy as np
import trimesh

from tests.fixtures.vawt.rotor import DEFAULT_SPEC, rotor_bodies

_BOX_FACES = 4 * 12  # four box blades


def dense_rotor(target_faces: int) -> trimesh.Trimesh:
    levels = max(0, int(np.floor(np.log(target_faces / 200.0) / np.log(4.0))))
    sections = max(8, round((target_faces / 4**levels - _BOX_FACES) / 4))
    bodies = rotor_bodies(DEFAULT_SPEC)
    bodies[0] = trimesh.creation.cylinder(
        radius=DEFAULT_SPEC.shaft_radius, height=DEFAULT_SPEC.shaft_length, sections=sections
    )
    mesh = trimesh.util.concatenate(bodies)
    for _ in range(levels):
        mesh = mesh.subdivide()
    return mesh
