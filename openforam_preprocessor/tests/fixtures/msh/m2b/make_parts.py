"""blockMeshDicts of the M2b test parts (make_fixtures.sh): annulus sectors about
z through the origin, r0..r1, z 0..0.4, span S degrees in blocks of at most 90.
A whole ring (S 360) shares its first and last vertices: no periodic patch.

    casing  360 deg  r 1.0-1.5  patches inlet (r 1.5), casing_out (r 1.0), walls
    vanes    45 deg  r 0.6-1.0  per1 / per2 (8 passages), vane_in, vane_out, walls
    runner   60 deg  r 0.2-0.6  per_a / per_b (6 passages), run_in, outlet, walls

Usage: python make_parts.py <folder>   (writes <folder>/<part>/system/blockMeshDict)
"""
import math
import sys
from pathlib import Path

PARTS = {
    "casing": (1.0, 1.5, 360.0, ("casing_out", "inlet"), None, (3, 4)),
    "vanes": (0.6, 1.0, 45.0, ("vane_out", "vane_in"), ("per1", "per2"), (3, 3)),
    "runner": (0.2, 0.6, 60.0, ("outlet", "run_in"), ("per_a", "per_b"), (3, 4)),
}
H, LAYERS = 0.4, 2


def point(r: float, t: float, z: float) -> str:
    a = math.radians(t)
    return f"({r * math.cos(a):.15g} {r * math.sin(a):.15g} {z:.15g})"


def write(folder: Path, name: str) -> None:
    r0, r1, span, (inner, outer), periodic, (nr, nt) = PARTS[name]
    blocks = max(1, math.ceil(span / 90.0 - 1e-9))
    step = span / blocks
    whole = span == 360.0
    columns = blocks if whole else blocks + 1
    vertices = []
    for z in (0.0, H):
        for k in range(columns):
            vertices += [point(r0, k * step, z), point(r1, k * step, z)]
    per_layer = 2 * columns

    def v(k: int, outer_side: int, top: int) -> int:
        return top * per_layer + 2 * (k % columns) + outer_side

    hexes, edges = [], []
    faces = {inner: [], outer: [], "bottom": [], "top": []}
    for k in range(blocks):
        a0, a1, b1, b0 = v(k, 0, 0), v(k, 1, 0), v(k + 1, 1, 0), v(k + 1, 0, 0)
        c0, c1, d1, d0 = v(k, 0, 1), v(k, 1, 1), v(k + 1, 1, 1), v(k + 1, 0, 1)
        hexes.append(f"    hex ({a0} {a1} {b1} {b0} {c0} {c1} {d1} {d0}) ({nr} {nt} {LAYERS}) "
                     "simpleGrading (1 1 1)")
        mid = (k + 0.5) * step
        for (p, q), r, z in (((a0, b0), r0, 0.0), ((a1, b1), r1, 0.0), ((c0, d0), r0, H),
                             ((c1, d1), r1, H)):
            edges.append(f"    arc {p} {q} {point(r, mid, z)}")
        faces[inner].append(f"({a0} {c0} {d0} {b0})")
        faces[outer].append(f"({a1} {b1} {d1} {c1})")
        faces["bottom"].append(f"({a0} {b0} {b1} {a1})")
        faces["top"].append(f"({c0} {c1} {d1} {d0})")
    if periodic:
        faces[periodic[0]] = [f"({v(0, 0, 0)} {v(0, 1, 0)} {v(0, 1, 1)} {v(0, 0, 1)})"]
        faces[periodic[1]] = [f"({v(blocks, 0, 0)} {v(blocks, 0, 1)} {v(blocks, 1, 1)} "
                              f"{v(blocks, 1, 0)})"]
    walls = {"bottom", "top"}
    boundary = "\n".join(
        f"    {n} {{ type {'wall' if n in walls else 'patch'}; faces ({' '.join(f)}); }}"
        for n, f in faces.items())
    text = ("FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }\n"
            "scale 1;\nvertices\n(\n" + "\n".join(f"    {p}" for p in vertices)
            + "\n);\nblocks\n(\n" + "\n".join(hexes) + "\n);\nedges\n(\n" + "\n".join(edges)
            + f"\n);\nboundary\n(\n{boundary}\n);\n")
    target = folder / name / "system"
    target.mkdir(parents=True, exist_ok=True)
    (target / "blockMeshDict").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    for part in PARTS:
        write(Path(sys.argv[1]), part)
