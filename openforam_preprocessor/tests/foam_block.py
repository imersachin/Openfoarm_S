"""Writes an ASCII polyMesh of nx x ny x nz unit hexahedra (V6 display tests).

Boundary faces are grouped by box face (x_min, x_max, y_min, y_max, z_min,
z_max) into the named patches given, in that order. Boundary normals point
out of the block; internal faces point from the lower cell to the higher.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")


def _header(cls: str, obj: str) -> str:
    return ("FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            f"    class       {cls};\n    object      {obj};\n}}\n\n")


def write_block_mesh(case: Path, n: Sequence[int] = (3, 2, 2),
                     spacing: float = 1.0, origin: Sequence[float] = (0.0, 0.0, 0.0),
                     patches: Mapping[str, tuple[str, str]] | None = None) -> Path:
    """patches: box face -> (patch name, OpenFOAM type); default inlet x_min,
    outlet x_max, walls elsewhere. Returns constant/polyMesh."""
    nx, ny, nz = n
    patches = patches or {"x_min": ("inlet", "patch"), "x_max": ("outlet", "patch"),
                          **{f: ("walls", "wall") for f in FACES[2:]}}

    def p(i: int, j: int, k: int) -> int:
        return i + (nx + 1) * (j + (ny + 1) * k)

    def c(i: int, j: int, k: int) -> int:
        return i + nx * (j + ny * k)

    points = [(origin[0] + i * spacing, origin[1] + j * spacing, origin[2] + k * spacing)
              for k in range(nz + 1) for j in range(ny + 1) for i in range(nx + 1)]
    faces: list[tuple[int, ...]] = []
    owner: list[int] = []
    neighbour: list[int] = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                if i + 1 < nx:
                    faces.append((p(i + 1, j, k), p(i + 1, j + 1, k), p(i + 1, j + 1, k + 1),
                                  p(i + 1, j, k + 1)))
                    owner.append(c(i, j, k))
                    neighbour.append(c(i + 1, j, k))
                if j + 1 < ny:
                    faces.append((p(i, j + 1, k), p(i, j + 1, k + 1), p(i + 1, j + 1, k + 1),
                                  p(i + 1, j + 1, k)))
                    owner.append(c(i, j, k))
                    neighbour.append(c(i, j + 1, k))
                if k + 1 < nz:
                    faces.append((p(i, j, k + 1), p(i + 1, j, k + 1), p(i + 1, j + 1, k + 1),
                                  p(i, j + 1, k + 1)))
                    owner.append(c(i, j, k))
                    neighbour.append(c(i, j, k + 1))

    def box_face(face: str) -> list[tuple[tuple[int, ...], int]]:
        out = []
        if face.startswith("x"):
            i = 0 if face == "x_min" else nx
            for k in range(nz):
                for j in range(ny):
                    quad = (p(i, j, k), p(i, j, k + 1), p(i, j + 1, k + 1), p(i, j + 1, k))
                    out.append((quad if i == 0 else quad[::-1], c(min(i, nx - 1), j, k)))
        elif face.startswith("y"):
            j = 0 if face == "y_min" else ny
            for k in range(nz):
                for i in range(nx):
                    quad = (p(i, j, k), p(i + 1, j, k), p(i + 1, j, k + 1), p(i, j, k + 1))
                    out.append((quad if j == 0 else quad[::-1], c(i, min(j, ny - 1), k)))
        else:
            k = 0 if face == "z_min" else nz
            for j in range(ny):
                for i in range(nx):
                    quad = (p(i, j, k), p(i, j + 1, k), p(i + 1, j + 1, k), p(i + 1, j, k))
                    out.append((quad if k == 0 else quad[::-1], c(i, j, min(k, nz - 1))))
        return out

    boundary: list[tuple[str, str, int, int]] = []
    for name, kind in dict.fromkeys(patches.values()):
        start = len(faces)
        for face in FACES:
            if patches.get(face) == (name, kind):
                for quad, cell in box_face(face):
                    faces.append(quad)
                    owner.append(cell)
        boundary.append((name, kind, len(faces) - start, start))

    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    (poly / "points").write_text(
        _header("vectorField", "points") + f"{len(points)}\n(\n"
        + "".join(f"({x:g} {y:g} {z:g})\n" for x, y, z in points) + ")\n")
    (poly / "faces").write_text(
        _header("faceList", "faces") + f"{len(faces)}\n(\n"
        + "".join(f"{len(f)}({' '.join(map(str, f))})\n" for f in faces) + ")\n")
    (poly / "owner").write_text(_header("labelList", "owner") + f"{len(owner)}\n(\n"
                                + "\n".join(map(str, owner)) + "\n)\n")
    (poly / "neighbour").write_text(_header("labelList", "neighbour")
                                    + f"{len(neighbour)}\n(\n"
                                    + "\n".join(map(str, neighbour)) + "\n)\n")
    entries = "".join(f"    {name}\n    {{\n        type {kind};\n        nFaces {count};\n"
                      f"        startFace {start};\n    }}\n"
                      for name, kind, count, start in boundary)
    (poly / "boundary").write_text(_header("polyBoundaryMesh", "boundary")
                                   + f"{len(boundary)}\n(\n{entries})\n")
    return poly


def write_set(poly: Path, name: str, set_class: str, labels: Sequence[int]) -> Path:
    sets = poly / "sets"
    sets.mkdir(exist_ok=True)
    path = sets / name
    path.write_text(_header(set_class, name) + f"{len(labels)}\n(\n"
                    + "\n".join(map(str, labels)) + "\n)\n")
    return path
