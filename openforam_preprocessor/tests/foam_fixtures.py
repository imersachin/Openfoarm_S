"""Writes a tiny but valid ASCII polyMesh: two unit hex cells along x."""

from __future__ import annotations

from pathlib import Path


def _header(cls: str, obj: str, fmt: str = "ascii") -> str:
    return (
        "FoamFile\n{\n    version     2.0;\n    format      " + fmt + ";\n"
        f"    class       {cls};\n    object      {obj};\n}}\n"
        "// * * * * * * * * * * * * * * * * //\n\n"
    )


def _p(i: int, j: int, k: int) -> int:
    return i + 3 * j + 6 * k


POINTS = [(float(i), float(j), float(k)) for k in (0, 1) for j in (0, 1) for i in (0, 1, 2)]
FACES = [
    (1, 4, 10, 7),  # 0: internal face x=1 (owner 0, neighbour 1)
    (0, 6, 9, 3),  # 1: inlet x=0
    (2, 5, 11, 8),  # 2: outlet x=2
    (0, 1, 7, 6), (1, 2, 8, 7),  # walls y=0
    (3, 9, 10, 4), (4, 10, 11, 5),  # walls y=1
    (0, 3, 4, 1), (1, 4, 5, 2),  # walls z=0
    (6, 7, 10, 9), (7, 8, 11, 10),  # walls z=1
]
OWNER = [0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
NEIGHBOUR = [1]


def write_poly_mesh(case: Path, *, fmt: str = "ascii") -> Path:
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    points = "\n".join(f"({x:g} {y:g} {z:g})" for x, y, z in POINTS)
    (poly / "points").write_text(
        _header("vectorField", "points", fmt) + f"{len(POINTS)}\n(\n{points}\n)\n", "utf-8"
    )
    faces = "\n".join(f"4({' '.join(map(str, f))})" for f in FACES)
    (poly / "faces").write_text(
        _header("faceList", "faces") + f"{len(FACES)}\n(\n{faces}\n)\n", "utf-8"
    )
    for name, labels in (("owner", OWNER), ("neighbour", NEIGHBOUR)):
        body = "\n".join(map(str, labels))
        (poly / name).write_text(
            _header("labelList", name) + f"{len(labels)}\n(\n{body}\n)\n", "utf-8"
        )
    (poly / "boundary").write_text(_header("polyBoundaryMesh", "boundary") + """3
(
    inlet
    {
        type            patch;
        nFaces          1;
        startFace       1;
    }
    outlet
    {
        type            patch;
        nFaces          1;
        startFace       2;
    }
    walls
    {
        type            wall;
        inGroups        1(wall);
        nFaces          8;
        startFace       3;
    }
)
""", "utf-8")
    return poly


def write_set(poly: Path, name: str, set_class: str, labels: list[int]) -> Path:
    sets = poly / "sets"
    sets.mkdir(exist_ok=True)
    path = sets / name
    body = "\n".join(map(str, labels))
    path.write_text(_header(set_class, name) + f"{len(labels)}\n(\n{body}\n)\n", "utf-8")
    return path
