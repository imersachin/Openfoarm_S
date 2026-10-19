"""Small Gmsh meshes for the M1-MSH tests (coarse versions of the E0 geometry).

    python make_gmsh_small.py <out_dir>

Writes box_22.msh, box_41.msh, box_22_bin.msh, box_22_nogroups.msh,
box_22_nowalls.msh and rotating_41.msh. Needs the gmsh package; the files
are committed, so the tests do not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gmsh

E0 = Path(__file__).parents[1] / "e0" / "common"
sys.path.insert(0, str(E0))
import make_gmsh  # noqa: E402  (the E0 builder; only its mesh sizes differ)


def coarse(size_max: float, size_min: float) -> None:
    gmsh.option.setNumber("Mesh.MeshSizeMax", size_max)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size_min)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 8)
    gmsh.model.mesh.generate(3)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sizes = {"box": (0.2, 0.1), "rotating": (0.3, 0.04)}
    for kind, (size_max, size_min) in sizes.items():
        make_gmsh._mesh = lambda _a, _b, m=size_max, n=size_min: coarse(m, n)  # type: ignore
        if kind == "box":
            make_gmsh.box(out / "box_22.msh", "2.2", False, True)
            make_gmsh.box(out / "box_41.msh", "4.1", False, True)
            make_gmsh.box(out / "box_22_bin.msh", "2.2", True, True)
            make_gmsh.box(out / "box_22_nogroups.msh", "2.2", False, False)
            make_gmsh.box(out / "box_22_nowalls.msh", "2.2", False, True, walls=False)
        else:
            make_gmsh.rotating(out / "rotating_41.msh", "4.1", False)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
