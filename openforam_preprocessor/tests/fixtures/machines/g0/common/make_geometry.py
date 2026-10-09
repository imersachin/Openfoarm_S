"""Write the G0 experiment surfaces (metres). Synthetic test geometry only: the
sizes are chosen to make small, fast meshes and are not presets.

    python -m tests.fixtures.machines.g0.common.make_geometry <set> <directory>

Sets:
  hawt     hawt_rotor.stl (hub + 3 twisted blades, axis x, R 0.5),
           domain_cylinder.stl (named regions inlet/outlet/side, axis x)
  duct     duct_named.stl (one ASCII STL, regions inlet/outlet/wall),
           inlet.stl, outlet.stl, wall.stl (one file per patch),
           duct_binary.stl (the same surface as binary STL)
  francis  casing.stl, guide.stl, runner.stl, draft.stl: one closed
           fluid-domain surface per part, each with named regions (axis z)
  pole     blades.stl (the V1 fixture rotor's four blades), pole.stl,
           zone_annulus.stl (regions outer/inner)

Every closed shell is wound outward from the solid it bounds (bodies, vanes,
blades); an outer fluid-domain shell is wound outward from the fluid.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import trimesh

from tests.fixtures.vawt.rotor import rotor_bodies

N_THETA = 96  # segments around every cylinder

Regions = list[tuple[str, trimesh.Trimesh]]


def write_ascii(path: Path, regions: Regions) -> None:
    """ASCII STL with one `solid <name>` block per region."""
    lines: list[str] = []
    for name, mesh in regions:
        lines.append(f"solid {name}")
        for normal, triangle in zip(mesh.face_normals, mesh.vertices[mesh.faces], strict=True):
            lines.append("  facet normal {:.9g} {:.9g} {:.9g}".format(*normal))
            lines.append("    outer loop")
            lines.extend("      vertex {:.9g} {:.9g} {:.9g}".format(*v) for v in triangle)
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def union(regions: Regions) -> trimesh.Trimesh:
    mesh = trimesh.util.concatenate([m for _, m in regions])
    mesh.merge_vertices()
    return mesh


def _ring(radius: float, z: float) -> np.ndarray:
    theta = np.linspace(0.0, 2 * np.pi, N_THETA, endpoint=False)
    return np.column_stack([radius * np.cos(theta), radius * np.sin(theta),
                            np.full(N_THETA, z)])


def _mesh(vertices: Sequence[Sequence[float]] | np.ndarray,
          faces: Sequence[Sequence[int]] | np.ndarray) -> trimesh.Trimesh:
    return trimesh.Trimesh(np.asarray(vertices, dtype=float), np.asarray(faces), process=False)


def side(r0: float, z0: float, r1: float, z1: float, *, inward: bool = False) -> trimesh.Trimesh:
    """Lateral surface of a frustum from (r0 at z0) to (r1 at z1), axis z."""
    v = np.vstack([_ring(r0, z0), _ring(r1, z1)])
    n = N_THETA
    faces = []
    for k in range(n):
        b0, b1, t0, t1 = k, (k + 1) % n, n + k, n + (k + 1) % n
        faces += [(b0, b1, t1), (b0, t1, t0)]
    mesh = _mesh(v, faces)
    if inward:
        mesh.invert()
    return mesh


def disc(radius: float, z: float, *, up: bool) -> trimesh.Trimesh:
    v = np.vstack([[0.0, 0.0, z], _ring(radius, z)])
    n = N_THETA
    faces = [(0, 1 + k, 1 + (k + 1) % n) if up else (0, 1 + (k + 1) % n, 1 + k)
             for k in range(n)]
    return _mesh(v, faces)


def annulus(r_in: float, r_out: float, z: float, *, up: bool) -> trimesh.Trimesh:
    v = np.vstack([_ring(r_in, z), _ring(r_out, z)])
    n = N_THETA
    faces = []
    for k in range(n):
        i0, i1, o0, o1 = k, (k + 1) % n, n + k, n + (k + 1) % n
        faces += [(i0, o0, o1), (i0, o1, i1)] if up else [(i0, o1, o0), (i0, i1, o1)]
    return _mesh(v, faces)


def z_to_x(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Turn a z-axis mesh onto the x axis (cyclic permutation: a proper rotation)."""
    out = mesh.copy()
    out.vertices = np.asarray(mesh.vertices)[:, (2, 0, 1)]
    return out


def blade(radii: np.ndarray, chords: np.ndarray, thickness: float, angles_deg: np.ndarray,
          *, axis: np.ndarray, phi: float, primary: str) -> trimesh.Trimesh:
    """A closed lofted plate along the radial direction at azimuth phi.

    Each section is a chord x thickness rectangle. The chord direction is
    cos(a)*primary + sin(a)*secondary, where primary is the tangential
    direction ("tangential", e.g. a wind-turbine blade at angle a from the
    rotor plane) or the axis ("axial", e.g. a guide vane at angle a from the
    axis).
    """
    axis = axis / np.linalg.norm(axis)
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    w = np.cross(axis, u)
    radial = np.cos(phi) * u + np.sin(phi) * w
    tangential = np.cross(axis, radial)
    first, second = (tangential, axis) if primary == "tangential" else (axis, tangential)
    vertices = []
    for r, c, a in zip(radii, chords, np.radians(angles_deg), strict=True):
        chord = np.cos(a) * first + np.sin(a) * second
        thick = np.cross(radial, chord)
        centre = r * radial
        for sc, st in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            vertices.append(centre + sc * c / 2 * chord + st * thickness / 2 * thick)
    faces = []
    for i in range(len(radii) - 1):
        for j in range(4):
            a0, a1 = 4 * i + j, 4 * i + (j + 1) % 4
            b0, b1 = a0 + 4, a1 + 4
            faces += [(a0, a1, b1), (a0, b1, b0)]
    last = 4 * (len(radii) - 1)
    faces += [(0, 2, 1), (0, 3, 2), (last, last + 1, last + 2), (last, last + 2, last + 3)]
    mesh = _mesh(vertices, faces)
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def closed_cylinder(radius: float, z0: float, z1: float) -> trimesh.Trimesh:
    return trimesh.util.concatenate([side(radius, z0, radius, z1), disc(radius, z0, up=False),
                                     disc(radius, z1, up=True)])


def check_closed(name: str, mesh: trimesh.Trimesh) -> None:
    mesh = mesh.copy()
    mesh.merge_vertices()
    assert mesh.is_watertight, f"{name} is not closed"


# --- sets --------------------------------------------------------------------------

def hawt(directory: Path) -> None:
    """Hub (r 0.06, x +-0.08) and three blades r 0.04-0.5, chord 0.10-0.05,
    thickness 0.016, angle to the rotor plane 25 deg (root) to 5 deg (tip).
    Domain cylinder r 2, x -2..5 (test values only, unverified)."""
    x_axis = np.array([1.0, 0.0, 0.0])
    radii = np.linspace(0.04, 0.5, 11)
    chords = np.linspace(0.10, 0.05, 11)
    angles = np.linspace(25.0, 5.0, 11)
    bodies = [z_to_x(closed_cylinder(0.06, -0.08, 0.08))]
    for k in range(3):
        bodies.append(blade(radii, chords, 0.016, angles, axis=x_axis,
                            phi=np.pi / 2 + k * 2 * np.pi / 3, primary="tangential"))
    for body in bodies:
        check_closed("hawt body", body)
    trimesh.util.concatenate(bodies).export(directory / "hawt_rotor.stl")
    domain = [("inlet", z_to_x(disc(2.0, -2.0, up=False))),
              ("outlet", z_to_x(disc(2.0, 5.0, up=True))),
              ("side", z_to_x(side(2.0, -2.0, 2.0, 5.0)))]
    check_closed("domain_cylinder", union(domain))
    write_ascii(directory / "domain_cylinder.stl", domain)


def duct(directory: Path) -> None:
    """A 2.0 x 0.5 x 0.5 duct from x 0 to 2, turned 20 deg about z, so no face
    lies on a background-mesh plane."""
    box = trimesh.creation.box(extents=(2.0, 0.5, 0.5))
    box.apply_translation((1.0, 0.0, 0.0))
    box.apply_transform(trimesh.transformations.rotation_matrix(np.radians(20), (0, 0, 1)))
    normal_x = box.face_normals @ np.array([np.cos(np.radians(20)), np.sin(np.radians(20)), 0])
    parts = {"inlet": normal_x < -0.99, "outlet": normal_x > 0.99}
    parts["wall"] = ~(parts["inlet"] | parts["outlet"])
    regions = [(name, _mesh(box.vertices, box.faces[mask])) for name, mask in parts.items()]
    check_closed("duct", union(regions))
    write_ascii(directory / "duct_named.stl", regions)
    for name, mesh in regions:
        write_ascii(directory / f"{name}.stl", [(name, mesh)])
    union(regions).export(directory / "duct_binary.stl")  # binary: no region names


def francis(directory: Path) -> None:
    """A Francis-like test passage along z (flow +z), pipe radius 0.3:
    casing z -0.6..-0.3; guide vanes z -0.3..-0.1 (8 vanes, 30 deg to the
    axis); runner z -0.1..0.1 (hub r 0.06 and 5 blades, 40 deg to the axis);
    draft tube z 0.1..0.9 widening to r 0.45. Vanes and blades end 15-30 mm
    short of the pipe wall (no CAD booleans here)."""
    z_axis = np.array([0.0, 0.0, 1.0])
    r = 0.3
    vanes = [blade(np.linspace(0.12, 0.285, 6), np.full(6, 0.08), 0.01, np.full(6, 30.0),
                   axis=z_axis, phi=k * np.pi / 4, primary="axial") for k in range(8)]
    for v in vanes:
        v.apply_translation((0, 0, -0.2))
    blades = [blade(np.linspace(0.05, 0.27, 8), np.full(8, 0.1), 0.012, np.full(8, 40.0),
                    axis=z_axis, phi=k * 2 * np.pi / 5, primary="axial") for k in range(5)]
    runner_body = trimesh.util.concatenate([closed_cylinder(0.06, -0.07, 0.07), *blades])
    for v in vanes:
        check_closed("vane", v)
    for b in blades:
        check_closed("runner blade", b)
    parts: dict[str, Regions] = {
        "casing": [("inlet", disc(r, -0.6, up=False)), ("casing_wall", side(r, -0.6, r, -0.3)),
                   ("casing_out", disc(r, -0.3, up=True))],
        "guide": [("guide_in", disc(r, -0.3, up=False)), ("guide_wall", side(r, -0.3, r, -0.1)),
                  ("guide_out", disc(r, -0.1, up=True)),
                  ("vanes", trimesh.util.concatenate(vanes))],
        "runner": [("runner_in", disc(r, -0.1, up=False)),
                   ("runner_wall", side(r, -0.1, r, 0.1)),
                   ("runner_out", disc(r, 0.1, up=True)), ("runner_blades", runner_body)],
        "draft": [("draft_in", disc(r, 0.1, up=False)), ("draft_wall", side(r, 0.1, 0.45, 0.9)),
                  ("outlet", disc(0.45, 0.9, up=True))],
    }
    for name, regions in parts.items():
        outer = [m for region, m in regions if region not in ("vanes", "runner_blades")]
        check_closed(name, union([("outer", m) for m in outer]))
        write_ascii(directory / f"{name}.stl", regions)


def pole(directory: Path) -> None:
    """The V1 fixture rotor's four blades (axis z, D 1.04) without its shaft;
    a pole r 0.03 from z -2.3 to 2.3 (through the V0 domain, z +-2.16); a
    rotating-zone annulus r 0.08-0.78, z +-0.66 (regions outer: side and
    caps, inner: the hole's side)."""
    blades = trimesh.util.concatenate(rotor_bodies()[1:])
    blades.export(directory / "blades.stl")
    closed_cylinder(0.03, -2.3, 2.3).export(directory / "pole.stl")
    outer = trimesh.util.concatenate([side(0.78, -0.66, 0.78, 0.66),
                                      annulus(0.08, 0.78, -0.66, up=False),
                                      annulus(0.08, 0.78, 0.66, up=True)])
    zone = [("outer", outer), ("inner", side(0.08, -0.66, 0.08, 0.66, inward=True))]
    check_closed("zone_annulus", union(zone))
    write_ascii(directory / "zone_annulus.stl", zone)


SETS = {"hawt": hawt, "duct": duct, "francis": francis, "pole": pole}


def main() -> None:
    name, directory = sys.argv[1], Path(sys.argv[2])
    directory.mkdir(parents=True, exist_ok=True)
    SETS[name](directory)
    for path in sorted(directory.glob("*.stl")):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
