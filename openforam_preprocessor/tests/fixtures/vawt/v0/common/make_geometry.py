"""Write the V0 experiment surfaces (metres, rotor axis z, centred at the origin).

    python -m tests.fixtures.vawt.v0.common.make_geometry <directory>

rotor.stl           the V1 fixture rotor (closed, outward-wound bodies)
rotor_open.stl      the same with its last triangle removed (open surface)
rotor_inverted.stl  the same with blade body 2 inside-out
"""

from __future__ import annotations

import sys
from pathlib import Path

import trimesh

from tests.fixtures.vawt.rotor import rotor_mesh, write_rotor


def main() -> None:
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    write_rotor(directory / "rotor.stl")
    write_rotor(directory / "rotor_inverted.stl", invert_body=2)
    mesh = rotor_mesh()
    trimesh.Trimesh(mesh.vertices, mesh.faces[:-1], process=False).export(
        directory / "rotor_open.stl"
    )
    for path in sorted(directory.glob("rotor*.stl")):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
