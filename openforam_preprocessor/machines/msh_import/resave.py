"""Re-save a Gmsh file as MSH 2.2 ASCII, which gmshToFoam reads (E0).

    python resave.py <in.msh> <out.msh>

Run as its own process by the import (a crash in the gmsh library cannot take
the application down, and its output is the step's log). Needs the optional
gmsh package (requirements.txt). Physical groups and their names are kept;
the mesh is not changed.
"""

from __future__ import annotations

import sys


def main(source: str, target: str) -> int:
    try:
        import gmsh  # optional dependency
    except ImportError as error:
        print(f"The gmsh package is not available: {error}", file=sys.stderr)
        return 2
    gmsh.initialize(readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.open(source)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.write(target)
        print(f"Re-saved {source} as MSH 2.2 ASCII: {target}")
        return 0
    except Exception as error:  # gmsh raises plain Exception with its message
        print(f"gmsh could not re-save {source}: {error}", file=sys.stderr)
        return 1
    finally:
        gmsh.finalize()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
