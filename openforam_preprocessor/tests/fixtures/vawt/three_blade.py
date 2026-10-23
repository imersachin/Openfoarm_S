"""The U1 E1 rotor: a straight-bladed VAWT of 3 NACA0018 blades (chord 0.1 m,
quarter chord on R 0.5 m, chord tangent to the circle), span 1 m about z.

Its bounding box is not centred on the axis (an odd blade count): the box
centre is (-0.0114, -0.0970) m from the axis and its extent normal to the axis
is 0.918 m, while the swept diameter is 1.018 m. `axis` moves the axis there
(z kept), so a test can tell the true axis from the box centre anywhere.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

CHORD, RADIUS, SPAN, BLADES = 0.1, 0.5, 1.0, 3
SWEPT_DIAMETER = 1.018069  # 2 x the largest distance from the axis (E1 measure)
BOX_EXTENT = 0.918222  # the largest extent normal to the axis (the old measure of D)
BOX_OFFSET = (-0.0114015, -0.0970245)  # bounding-box centre minus the axis, in (x, y)


def _naca00(thickness: float, points: int) -> np.ndarray:
    beta = np.linspace(0.0, np.pi, points)
    x = 0.5 * (1 - np.cos(beta))
    y = 5 * thickness * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3
                         - 0.1036 * x**4)  # closed trailing edge
    upper = np.column_stack([x, y])[:-1]
    lower = np.column_stack([x[::-1], -y[::-1]])[:-1]
    return np.vstack([upper, lower])


def _prism(outline: np.ndarray, z0: float, z1: float, layers: int) -> trimesh.Trimesh:
    """A closed extrusion; the convex section's caps are fans from its centre."""
    n = len(outline)
    rings = [np.column_stack([outline, np.full(n, z)]) for z in np.linspace(z0, z1, layers + 1)]
    centre = outline.mean(axis=0)
    vertices = np.vstack([*rings, [*centre, z0], [*centre, z1]])
    faces = []
    for k in range(layers):
        a, b = k * n, (k + 1) * n
        for i in range(n):
            j = (i + 1) % n
            faces += [[a + i, a + j, b + j], [a + i, b + j, b + i]]
    bottom, top, last = len(vertices) - 2, len(vertices) - 1, layers * n
    for i in range(n):
        j = (i + 1) % n
        faces += [[bottom, j, i], [top, last + i, last + j]]
    mesh = trimesh.Trimesh(vertices, np.array(faces), process=True)
    trimesh.repair.fix_normals(mesh)
    return mesh


def three_blade_rotor(axis: tuple[float, float] = (0.0, 0.0), points: int = 80,
                      layers: int = 20) -> trimesh.Trimesh:
    parts = []
    for k in range(BLADES):
        outline = _naca00(0.18, points) * CHORD
        outline[:, 0] -= 0.25 * CHORD  # quarter chord on the circle
        outline[:, 1] -= RADIUS  # chord along +x at (0, -R): tangent to the circle
        blade = _prism(outline, -SPAN / 2, SPAN / 2, layers)
        blade.apply_transform(trimesh.transformations.rotation_matrix(
            2 * np.pi * k / BLADES, [0, 0, 1]))
        parts.append(blade)
    rotor = trimesh.util.concatenate(parts)
    rotor.apply_translation([axis[0], axis[1], 0.0])
    return rotor


def write_three_blade(path: Path, axis: tuple[float, float] = (0.0, 0.0)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    three_blade_rotor(axis).export(path, file_type="stl")
    return path
