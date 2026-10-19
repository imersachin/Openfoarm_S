"""Compare the points of polyMesh folders (evidence helper, M1-MSH E0).

    python compare_points.py <polyMesh> <polyMesh> [<polyMesh> ...]

Prints each mesh's point count and bounding box, and the largest coordinate
difference of every later mesh from the first (point order as written)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from visualization.foam_reader import read_points


def main(meshes: list[Path]) -> None:
    points = [read_points(m / "points") for m in meshes]
    for mesh, p in zip(meshes, points, strict=True):
        print(f"{mesh.parent.parent.name}: {len(p)} points, "
              f"box {p.min(axis=0).tolist()} {p.max(axis=0).tolist()}")
    for mesh, p in zip(meshes[1:], points[1:], strict=True):
        if p.shape != points[0].shape:
            print(f"{mesh.parent.parent.name}: different point count")
            continue
        print(f"{mesh.parent.parent.name} vs {meshes[0].parent.parent.name}: "
              f"max |difference| {np.abs(p - points[0]).max():.3e} m (same order)")


if __name__ == "__main__":
    main([Path(a) for a in sys.argv[1:]])
