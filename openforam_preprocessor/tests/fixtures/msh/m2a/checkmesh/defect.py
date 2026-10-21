"""M2a evidence 1: put one defect into the ASCII polyMesh of a 4 x 4 x 4 unit cube
(blockMesh), for checkMesh's wording per defect class.

Usage: defect.py <polyMesh dir> <kind>
  negvol        swap the z = 0.25 and z = 0.5 point layers: a layer of inverted cells
  pushpoint     push an interior point through its neighbours (volumes stay positive)
  flipface      reverse the points of one internal face
  openboundary  drop the last boundary face from its patch and the mesh
  nonortho      shear the mesh by 80 deg
  aspect        stretch it 5000 times along x
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path


def read_list(path: Path) -> tuple[str, list[str], str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\n(\d+)\n\(\n", text)
    assert match is not None, path
    end = text.rindex("\n)")
    return text[:match.end()], text[match.end():end].splitlines(), text[end:]


def write_list(path: Path, head: str, items: list[str], tail: str) -> None:
    path.write_text(head + "\n".join(items) + tail, encoding="utf-8")


def recount(head: str, old: int, new: int) -> str:
    return head.replace(f"\n{old}\n(\n", f"\n{new}\n(\n")


def main(poly: Path, kind: str) -> None:
    head, rows, tail = read_list(poly / "points")
    points = [[float(v) for v in row.strip("()").split()] for row in rows]

    def at(x: float, y: float, z: float) -> int:
        return next(i for i, p in enumerate(points)
                    if abs(p[0] - x) < 1e-9 and abs(p[1] - y) < 1e-9 and abs(p[2] - z) < 1e-9)

    if kind == "negvol":
        for p in points:
            if abs(p[2] - 0.25) < 1e-9:
                p[2] = 0.5
            elif abs(p[2] - 0.5) < 1e-9:
                p[2] = 0.25
    elif kind == "pushpoint":
        points[at(0.5, 0.5, 0.5)] = [0.5, 0.5, 1.3]
    elif kind == "nonortho":
        for p in points:
            p[0] += p[2] * math.tan(math.radians(80))
    elif kind == "aspect":
        for p in points:
            p[0] *= 5000
    elif kind == "flipface":
        fhead, faces, ftail = read_list(poly / "faces")
        match = re.match(r"(\d+)\((.*)\)", faces[0])
        assert match is not None
        faces[0] = f"{match[1]}(" + " ".join(reversed(match[2].split())) + ")"
        write_list(poly / "faces", fhead, faces, ftail)
        return
    elif kind == "openboundary":
        fhead, faces, ftail = read_list(poly / "faces")
        ohead, owners, otail = read_list(poly / "owner")
        write_list(poly / "faces", recount(fhead, len(faces), len(faces) - 1), faces[:-1], ftail)
        write_list(poly / "owner", recount(ohead, len(owners), len(owners) - 1), owners[:-1],
                   otail)
        boundary = (poly / "boundary").read_text(encoding="utf-8")
        last = list(re.finditer(r"nFaces\s+(\d+);", boundary))[-1]
        boundary = (boundary[:last.start()] + f"nFaces {int(last[1]) - 1};"
                    + boundary[last.end():])
        (poly / "boundary").write_text(boundary, encoding="utf-8")
        return
    else:
        raise SystemExit(f"unknown defect {kind}")
    rows = [f"({p[0]:.10g} {p[1]:.10g} {p[2]:.10g})" for p in points]
    write_list(poly / "points", head, rows, tail)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
