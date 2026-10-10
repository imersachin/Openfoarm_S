"""Rotating zones: interface names, zone surfaces and grids (G3; decisions F2-F4).

A cylinder zone is meshed on its own (case zone_<name>) and cut out of the
domain mesh; after merging, each interface becomes a cyclicAMI pair.

Interfaces of a zone: `outer` (side and end caps) and, for an annular zone,
`inner` (the hole's wall). Final pair names: <zone>_<interface>_stat (domain
side) and <zone>_<interface>_rot (zone side). The meshes carry them first as
<pair name>_src: createPatch moves faces into a patch that already exists
without changing its type, so the cyclicAMI patches must be new names.

A split body's part inside its zone is the patch <body>_rotating (F3).

Joints between imported surfaces (G5; K1) are cyclicAMI pairs named after
their two regions, meshed first as <region>_src for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from machines.config import (
    BodyConfig,
    CylinderZone,
    MachineProjectConfig,
    Motion,
    RotatingZone,
)
from machines.domains import background_grid
from vawt.case_generator import Grid
from vawt.config import plane_axes

OUTER, INNER = "outer", "inner"
SOURCE_SUFFIX = "_src"


@dataclass(frozen=True)
class AmiPair:
    """Two patches createPatch makes one cyclicAMI pair, each from <name>_src."""

    first: str
    second: str


@dataclass(frozen=True)
class Interface:
    zone: str
    region: str  # OUTER or INNER: the zone surface region

    @property
    def stationary(self) -> str:
        return f"{self.zone}_{self.region}_stat"

    @property
    def rotating(self) -> str:
        return f"{self.zone}_{self.region}_rot"

    @property
    def pair(self) -> AmiPair:
        return AmiPair(self.stationary, self.rotating)


def interfaces(zone: RotatingZone) -> tuple[Interface, ...]:
    """The generated interfaces of a cylinder zone. An imported zone has none:
    it meets the domain parts at joints (G5)."""
    shape = zone.shape
    if not isinstance(shape, CylinderZone):
        return ()
    hole = shape.hole_diameter is not None
    return (Interface(zone.name, OUTER), *((Interface(zone.name, INNER),) if hole else ()))


def joint_pairs(config: MachineProjectConfig) -> tuple[AmiPair, ...]:
    """Joints between imported surfaces, named after their regions (K1)."""
    return tuple(AmiPair(j.first, j.second) for j in config.joints)


def split_patch(body: BodyConfig) -> str:
    """The patch of a split body's part inside its zone."""
    return f"{body.name}_rotating"


def generated_names(config: MachineProjectConfig) -> dict[str, str]:
    """Patch names the pipeline creates -> what creates them."""
    names: dict[str, str] = {}
    for zone in config.rotating_zones:
        for face in interfaces(zone):
            for name in (face.stationary, face.rotating):
                names[name] = f"interface of zone '{zone.name}'"
                names[name + SOURCE_SUFFIX] = f"interface of zone '{zone.name}'"
    for body in config.bodies:
        if body.motion is Motion.SPLIT:
            names[split_patch(body)] = f"split body '{body.name}'"
    for pair in joint_pairs(config):
        for name in (pair.first, pair.second):
            names[name] = f"joint '{pair.first}' / '{pair.second}'"
            names[name + SOURCE_SUFFIX] = f"joint '{pair.first}' / '{pair.second}'"
    return names


def zone_surface(zone: RotatingZone) -> list[tuple[str, trimesh.Trimesh]]:
    """A cylinder zone as regions OUTER (side and caps) and, with a hole, INNER
    (the hole's wall), with normals pointing out of the zone."""
    shape = zone.shape
    assert isinstance(shape, CylinderZone)
    n = shape.segments
    u, v = plane_axes(zone.axis)
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    radius = shape.diameter / 2.0
    hole = (shape.hole_diameter or 0.0) / 2.0

    def ring(r: float, along: float) -> np.ndarray:
        points = np.zeros((n, 3))
        points[:, u.position] = shape.centre_u + r * np.cos(theta)
        points[:, v.position] = shape.centre_v + r * np.sin(theta)
        points[:, zone.axis.position] = along
        return points

    k = np.arange(n)
    nxt = (k + 1) % n

    def wall(r: float) -> trimesh.Trimesh:
        return trimesh.Trimesh(
            np.vstack([ring(r, shape.axis_min), ring(r, shape.axis_max)]),
            np.vstack([np.column_stack([k, nxt, n + nxt]), np.column_stack([k, n + nxt, n + k])]),
            process=False)

    def cap(along: float, up: bool) -> trimesh.Trimesh:
        if hole > 0.0:
            vertices = np.vstack([ring(hole, along), ring(radius, along)])
            faces = np.vstack([np.column_stack([k, n + k, n + nxt]),
                               np.column_stack([k, n + nxt, nxt])])
        else:
            centre = np.zeros((1, 3))
            centre[0, u.position], centre[0, v.position] = shape.centre_u, shape.centre_v
            centre[0, zone.axis.position] = along
            vertices = np.vstack([centre, ring(radius, along)])
            faces = np.column_stack([np.zeros(n, int), 1 + k, 1 + nxt])
        mesh = trimesh.Trimesh(vertices, faces, process=False)
        if not up:
            mesh.invert()
        return mesh

    outer = trimesh.util.concatenate([wall(radius), cap(shape.axis_min, False),
                                      cap(shape.axis_max, True)])
    regions = [(OUTER, outer)]
    if hole > 0.0:
        inner = wall(hole)
        inner.invert()  # the hole's wall faces the axis: out of the annulus
        regions.append((INNER, inner))
    # As machines.domains.cylinder_surface: the y-axis plane frame is a mirror
    # image, so the winding is set from the closed union's volume.
    union = trimesh.util.concatenate([m for _, m in regions])
    union.merge_vertices()
    if union.volume < 0:
        for _, mesh in regions:
            mesh.invert()
    return regions


def zone_grid(zone: RotatingZone) -> Grid:
    """The zone's background block (decision E1), with the zone's cell size."""
    union = trimesh.util.concatenate([m for _, m in zone_surface(zone)])
    return background_grid(union.bounds[0], union.bounds[1], zone.cell_size)
