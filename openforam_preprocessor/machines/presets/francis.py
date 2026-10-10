"""Francis preset (docs/rotating_machinery.md section 8; decisions K4, K5).

A draft from imported surfaces: the stationary domain parts (casing, guide
vanes, draft tube...) and the runner, an imported rotating zone. You give the
files with their units, the rotation axis, a point on it, and the runner
outlet diameter D, which you confirm (section 8). The draft fills in:

- joints proposed from regions that coincide (machines.joints), to confirm;
- a mesh point inside each surface (K4), away from it and off the
  background-cell faces, editable;
- the cell size from D, and a refinement level for every region that is a
  closed solid on its own (vanes, blades), G0 R4 test values, unverified;
- one patch per region outside the joints, with no type: the types are
  chosen by you (E5), so the draft stays BLOCKING until they are. Types
  suggested by region names come separately.

Every value is data with its source; nothing is saved.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from geometry.metrics import contains_points
from machines.config import ImportedSurface, PatchType
from machines.domains import SurfaceInfo, open_edge_count, read_surface, surface_grid
from machines.joints import JointLimits, ProposedJoint, propose_joints
from machines.patches import suggest_region_types
from machines.presets.hawt import PresetValue
from machines.surface_sampling import surface_points
from vawt.config import Axis, plane_axes

G0_R4_UNVERIFIED = ("G0 R4 test value (0.025 m for the 0.6 m synthetic runner; "
                    "docs/rotating_machinery_notes.md); unverified, not a recommendation")

FRANCIS_VALUES: dict[str, PresetValue] = {
    # Every part and the runner: one cell size, as G0 R4.
    "cell": PresetValue(1.0 / 24.0, "D", G0_R4_UNVERIFIED),
    # A region that is a closed solid on its own (guide vanes, runner blades
    # with their hub) is refined to this level, as G0 R4.
    "solid_level": PresetValue(2.0, "level", G0_R4_UNVERIFIED),
}

ZONE_NAME = "runner"
# Mesh points sit this fraction of a cell beyond a background-cell centre on
# every axis: never on a cell face, nor on a refined cell's face (V0 E4).
POINT_FRACTION = 0.1137
MAX_CANDIDATES = 40_000  # background-cell centres considered per surface
BATCH = 64  # candidates tested for containment at a time


@dataclass(frozen=True)
class FrancisDraft:
    """data: the draft configuration (raw; patch types unchosen).
    joints: proposed, with their measurements, for the user to confirm.
    outlet_diameters: for each runner region in a proposed joint, twice its
        largest distance from the axis: a suggestion for D only.
    suggested_types: region -> type, from the region name alone (E5)."""

    data: dict[str, Any]
    joints: tuple[ProposedJoint, ...]
    outlet_diameters: dict[str, float]
    suggested_types: dict[str, PatchType]
    values: dict[str, PresetValue]


def _point(p: np.ndarray) -> dict[str, float]:
    return {"x": float(p[0]), "y": float(p[1]), "z": float(p[2])}


def inner_point(surface: SurfaceInfo, cell: float) -> np.ndarray:
    """A point inside the closed surface, as far from it as the background
    cells allow (K4): background-cell centres shifted by POINT_FRACTION, the
    farthest from the surface that lies inside it."""
    grid = surface_grid(surface, cell)
    if grid is None or not surface.closed:
        raise ValueError(f"'{surface.name}' is not a closed surface; no mesh point can be "
                         "placed in it.")
    axes = []
    stride = max(1, round((np.prod(grid.cells) / MAX_CANDIDATES) ** (1.0 / 3.0)))
    for i in range(3):
        spacing = grid.spacing(i)
        index = np.arange(0, grid.cells[i], stride)
        axes.append(grid.minimum[i] + (index + 0.5 + POINT_FRACTION) * spacing)
    candidates = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    union = surface.union()
    # Distances only rank the candidates: one point per cell, approximate
    # nearest neighbours (within a factor 2) keep this fast on large surfaces.
    samples, _ = surface_points(union, cell, 2_000_000)
    distance, _ = cKDTree(samples).query(candidates, eps=1.0)
    order = np.argsort(-distance, kind="stable")
    for start in range(0, len(order), BATCH):
        batch = order[start:start + BATCH]
        inside = contains_points(candidates[batch], union.vertices, union.faces)
        if inside.any():
            return candidates[batch[int(np.argmax(inside))]]
    raise ValueError(f"No background-cell centre lies inside '{surface.name}'.")


def outlet_diameter(surface: SurfaceInfo, region: str, axis: Axis,
                    origin: Sequence[float]) -> float:
    """Twice the largest distance of a region's vertices from the axis."""
    u, v = plane_axes(axis)
    mesh = next(r.mesh for r in surface.regions if r.name == region)
    vertices = np.asarray(mesh.vertices, dtype=float)
    radial = np.hypot(vertices[:, u.position] - origin[u.position],
                      vertices[:, v.position] - origin[v.position])
    return 2.0 * float(radial.max())


def francis_draft(base: Mapping[str, Any], diameter: float,
                  limits: JointLimits | None = None) -> FrancisDraft:
    """A Francis draft. Nothing is saved.

    base: "project_name"; "axis" (x, y or z) and "origin" (a point on the
    rotation axis); "runner": {"format", "files"} (optionally "name"); "parts":
    [{"name", "format", "files"}, ...]. Files are StlSource data, units
    required. diameter: the runner outlet diameter D, confirmed by the user.
    Raises pydantic.ValidationError for incomplete surfaces and ValueError for
    surfaces that cannot be read or are not closed.
    """
    if not diameter > 0.0:
        raise ValueError("The runner outlet diameter must be positive.")
    axis = Axis(base["axis"])
    origin = base["origin"]
    origin_xyz = (float(origin["x"]), float(origin["y"]), float(origin["z"]))
    cell = FRANCIS_VALUES["cell"].value * diameter
    runner = base["runner"]
    zone_name = runner.get("name", ZONE_NAME)

    def surface_of(data: Mapping[str, Any]) -> ImportedSurface:
        return ImportedSurface.model_validate({"format": data["format"],
                                               "files": data["files"]})

    parts = [(p["name"], surface_of(p)) for p in base["parts"]]
    read = [read_surface(f"domain.parts.{i}", name, s) for i, (name, s) in enumerate(parts)]
    zone = read_surface("rotating_zones.0", zone_name, surface_of(runner))
    surfaces = [*read, zone]
    for s in surfaces:
        if not s.closed:
            raise ValueError(f"'{s.name}' cannot be drafted: it is unreadable or not closed. "
                             "validate_machine names the problem.")
    joints = propose_joints(surfaces, {s.owner: cell for s in surfaces}, limits)
    joined = {name for j in joints for name in (j.first, j.second)}
    regions = [r.name for s in surfaces for r in s.regions if r.name not in joined]
    runner_regions = {r.name for r in zone.regions}
    diameters = {name: outlet_diameter(zone, name, axis, origin_xyz)
                 for j in joints for name in (j.first, j.second) if name in runner_regions}
    level = int(FRANCIS_VALUES["solid_level"].value)

    def refinement(surface: SurfaceInfo) -> dict[str, int]:
        return {r.name: level for r in surface.regions
                if r.name not in joined and open_edge_count([r.mesh]) == 0}

    data: dict[str, Any] = {
        "project_name": base["project_name"], "machine": "FRANCIS",
        "rotating_zones": [{
            "name": zone_name, "axis": axis.value,
            "shape": {"kind": "IMPORTED", "format": runner["format"],
                      "files": copy.deepcopy(runner["files"]), "origin": dict(origin),
                      "refinement": refinement(zone)},
            "cell_size": cell, "location_in_mesh": _point(inner_point(zone, cell))}],
        "domain": {"kind": "IMPORTED", "parts": [
            {"name": name, "format": p["format"], "files": copy.deepcopy(p["files"]),
             "refinement": refinement(info), "cell_size": cell,
             "location_in_mesh": _point(inner_point(info, cell))}
            for (name, _), p, info in zip(parts, base["parts"], read, strict=True)]},
        # Types are not chosen here (E5): the draft stays BLOCKING until they are.
        "patches": [{"name": r, "source": {"kind": "REGION", "ref": r}} for r in regions],
        "joints": [{"first": j.first, "second": j.second} for j in joints],
    }
    return FrancisDraft(data, joints, diameters, suggest_region_types(regions),
                        dict(FRANCIS_VALUES))


def with_types(data: Mapping[str, Any],
               types: Mapping[str, PatchType | str]) -> dict[str, Any]:
    """The draft with the chosen types set on the named patches."""
    result = copy.deepcopy(dict(data))
    for patch in result["patches"]:
        if patch["name"] in types:
            patch["type"] = PatchType(types[patch["name"]]).value
    return result
