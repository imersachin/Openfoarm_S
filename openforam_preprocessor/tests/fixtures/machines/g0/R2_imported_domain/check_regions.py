"""What a Python reader sees in each R2 input: format, region names, triangle
counts, and whether the union of the given files is a closed surface.

    python -m tests.fixtures.machines.g0.R2_imported_domain.check_regions <triSurface dir>

Prints JSON. Evidence for spec section 5.3 validation; not application code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import trimesh

INPUTS = {
    "named": ["duct_named.stl"],
    "per_file": ["inlet.stl", "outlet.stl", "wall.stl"],
    "binary": ["duct_binary.stl"],
    "open": ["inlet.stl", "wall.stl"],
}


def is_ascii(path: Path) -> bool:
    data = path.read_bytes()
    if not data.lstrip().startswith(b"solid"):
        return False
    # A binary STL's size is fixed by its triangle count; "solid" alone is not proof.
    if len(data) >= 84:
        count = int.from_bytes(data[80:84], "little")
        if len(data) == 84 + 50 * count:
            return False
    return b"endsolid" in data


def ascii_regions(path: Path) -> dict[str, int]:
    regions: dict[str, int] = {}
    current = None
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        words = line.split()
        if words[:1] == ["solid"]:
            current = words[1] if len(words) > 1 else ""
            regions[current] = 0
        elif words[:1] == ["facet"] and current is not None:
            regions[current] += 1
    return regions


def describe(directory: Path, files: list[str]) -> dict[str, object]:
    meshes, per_file = [], {}
    for name in files:
        path = directory / name
        ascii_ = is_ascii(path)
        mesh = trimesh.load(path, force="mesh")
        meshes.append(mesh)
        per_file[name] = {"format": "ascii" if ascii_ else "binary",
                          "regions": ascii_regions(path) if ascii_ else None,
                          "triangles": len(mesh.faces)}
    union = trimesh.util.concatenate(meshes)
    union.merge_vertices()
    edges = union.edges_sorted
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return {"files": per_file, "union_closed": bool(union.is_watertight),
            "open_edges": int((counts == 1).sum())}


def main() -> None:
    directory = Path(sys.argv[1])
    print(json.dumps({k: describe(directory, v) for k, v in INPUTS.items()}, indent=2))


if __name__ == "__main__":
    main()
