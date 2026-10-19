"""Split the one fluid cell section of a foamMeshToFluent .msh into two cell zones.

    python split_fluent_cells.py <in.msh> <out.msh> <n_first> <first_name> <second_name>

foamMeshToFluent writes every cell in one section, "(12 (1 1 <last> 1 0)", with
one "(39 (1 fluid fluid-1)())" name. This hand edit stands in for a real Fluent
rotor/stator file: cells 1..n_first stay in zone 1, the rest go to zone 20
(hex 14, unused by the face zones). Only the cell section and the zone names
change; nodes and faces are untouched. E6 evidence only.
"""

import re
import sys
from pathlib import Path

src, dst, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
first, second = sys.argv[4], sys.argv[5]
text = Path(src).read_text(encoding="ascii")
match = re.search(r"\(12 \(1 1 ([0-9a-f]+) 1 0\)\n\(\n(.*?)\n\)\)", text, re.S)
assert match is not None, "no single fluid cell section"
types = match[2].split()
total = int(match[1], 16)
assert len(types) == total and 0 < n < total


def rows(ts: list[str]) -> str:
    return "\n".join(" ".join(ts[i:i + 25]) for i in range(0, len(ts), 25))


sections = (f"(12 (1 1 {n:x} 1 0)\n(\n{rows(types[:n])}\n))\n"
            f"(12 (14 {n + 1:x} {total:x} 1 0)\n(\n{rows(types[n:])}\n))")
text = text[:match.start()] + sections + text[match.end():]
old = "(39 (1 fluid fluid-1)())"
assert old in text
text = text.replace(old, f"(39 (1 fluid {first})())\n(39 (20 fluid {second})())")
Path(dst).write_text(text, encoding="ascii")
print(f"{total} cells: {first} 1..{n}, {second} {n + 1}..{total}")
