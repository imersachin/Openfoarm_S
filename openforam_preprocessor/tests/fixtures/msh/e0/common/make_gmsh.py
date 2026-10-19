"""Gmsh meshes for the M1-MSH E0 experiments, written with the gmsh Python
package (4.15.2). Sizes are test values only, not presets.

    python make_gmsh.py box <out.msh> --version 2.2|4.1 [--binary] [--no-groups|--no-walls]
    python make_gmsh.py rotating <out.msh> --version 2.2|4.1
    python make_gmsh.py resave <in.msh> <out.msh> --version 2.2|4.1|1 [--binary]

box: x 0..1, y 0..0.5, z 0..0.5 m, tetrahedra. Physical groups: surfaces inlet
(x 0), outlet (x 1), walls (the other four); volume fluid.

rotating: outer box x -1..3, y and z -1..1 m; a rotating cylinder on the x
axis, r 0.5, x -0.2..0.2, around a stationary-free "rotor" block x +-0.05,
y +-0.4, z +-0.05. The two volumes are NOT fragmented: each keeps its own copy
of the cylinder surface, so the mesh has two disconnected regions joined later
by an AMI pair (as a Fluent mesh with interface zones). Physical groups:
surfaces inlet, outlet, y_min, y_max, z_min, z_max, ami_stat (the outer
volume's cylinder faces), ami_rot (the rotating volume's), rotor; volumes
stationary and rotating.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import gmsh

Bounds = tuple[float, float, float, float, float, float]


def _write(path: Path, version: str, binary: bool) -> None:
    gmsh.option.setNumber("Mesh.MshFileVersion", float(version))
    gmsh.option.setNumber("Mesh.Binary", int(binary))
    path.parent.mkdir(parents=True, exist_ok=True)
    gmsh.write(str(path))


def _surfaces(volume: int) -> list[int]:
    return [tag for dim, tag in gmsh.model.getBoundary([(3, volume)], oriented=False)
            if dim == 2]


def _on(tag: int, test: Callable[[Bounds], bool]) -> bool:
    return test(gmsh.model.getBoundingBox(2, tag))


def _mesh(size_max: float, size_min: float) -> None:
    gmsh.option.setNumber("Mesh.MeshSizeMax", size_max)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size_min)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
    gmsh.model.mesh.generate(3)


def box(path: Path, version: str, binary: bool, groups: bool, walls: bool = True) -> None:
    gmsh.initialize(readConfigFiles=False)
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("box")
        volume = gmsh.model.occ.addBox(0, 0, 0, 1, 0.5, 0.5)
        gmsh.model.occ.synchronize()
        if groups:
            faces = _surfaces(volume)
            inlet = [t for t in faces if _on(t, lambda b: b[3] < 1e-6)]
            outlet = [t for t in faces if _on(t, lambda b: b[0] > 1 - 1e-6)]
            sides = [t for t in faces if t not in inlet + outlet]
            named = [("inlet", inlet), ("outlet", outlet)] + ([("walls", sides)] if walls else [])
            for name, tags in named:
                assert tags, name
                gmsh.model.setPhysicalName(2, gmsh.model.addPhysicalGroup(2, tags), name)
            gmsh.model.setPhysicalName(3, gmsh.model.addPhysicalGroup(3, [volume]), "fluid")
        _mesh(0.08, 0.02)
        _write(path, version, binary)
    finally:
        gmsh.finalize()


def rotating(path: Path, version: str, binary: bool, ungrouped: str | None = None) -> None:
    """`ungrouped`: a box face left without a physical group (M1c checklist)."""
    gmsh.initialize(readConfigFiles=False)
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("rotating")
        occ = gmsh.model.occ
        outer_box = occ.addBox(-1, -1, -1, 4, 2, 2)
        cylinder = occ.addCylinder(-0.2, 0, 0, 0.4, 0, 0, 0.5)
        rotor = occ.addBox(-0.05, -0.4, -0.05, 0.1, 0.8, 0.1)
        copy = occ.copy([(3, cylinder)])[0][1]
        (stationary,), _ = occ.cut([(3, outer_box)], [(3, copy)])
        (rotating_volume,), _ = occ.cut([(3, cylinder)], [(3, rotor)])
        occ.synchronize()
        eps = 1e-6
        outer_faces = _surfaces(stationary[1])
        inner_faces = _surfaces(rotating_volume[1])

        def box_face(index: int, value: float) -> list[int]:
            return [t for t in outer_faces
                    if _on(t, lambda b: abs(b[index] - value) < eps
                           and abs(b[index + 3] - value) < eps)]

        groups = {
            "inlet": box_face(0, -1), "outlet": box_face(0, 3),
            "y_min": box_face(1, -1), "y_max": box_face(1, 1),
            "z_min": box_face(2, -1), "z_max": box_face(2, 1),
        }
        boxed = {t for tags in groups.values() for t in tags}
        groups["ami_stat"] = [t for t in outer_faces if t not in boxed]
        in_rotor = [t for t in inner_faces
                    if _on(t, lambda b: max(abs(v) for v in (b[0], b[3])) < 0.05 + eps
                           and max(abs(v) for v in (b[2], b[5])) < 0.05 + eps)]
        groups["ami_rot"] = [t for t in inner_faces if t not in in_rotor]
        groups["rotor"] = in_rotor
        for name, tags in groups.items():
            assert tags, name
            if name == ungrouped:
                continue
            gmsh.model.setPhysicalName(2, gmsh.model.addPhysicalGroup(2, tags), name)
        for name, volume in (("stationary", stationary[1]), ("rotating", rotating_volume[1])):
            gmsh.model.setPhysicalName(3, gmsh.model.addPhysicalGroup(3, [volume]), name)
        _mesh(0.15, 0.02)
        _write(path, version, binary)
    finally:
        gmsh.finalize()


def resave(source: Path, path: Path, version: str, binary: bool) -> None:
    gmsh.initialize(readConfigFiles=False)
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.open(str(source))
        _write(path, version, binary)
    finally:
        gmsh.finalize()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("box", "rotating", "resave"))
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--version", default="2.2")
    parser.add_argument("--binary", action="store_true")
    parser.add_argument("--no-groups", action="store_true")
    parser.add_argument("--no-walls", action="store_true")  # walls left without a group
    args = parser.parse_args()
    if args.kind == "box":
        box(args.paths[0], args.version, args.binary, not args.no_groups, not args.no_walls)
    elif args.kind == "rotating":
        rotating(args.paths[0], args.version, args.binary)
    else:
        resave(args.paths[0], args.paths[1], args.version, args.binary)


if __name__ == "__main__":
    main()
