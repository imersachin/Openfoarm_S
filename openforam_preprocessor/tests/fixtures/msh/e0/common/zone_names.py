"""Print the names and sizes of the zones in an OpenFOAM zone file (cellZones,
faceZones, pointZones), ASCII or binary. Evidence helper for the M1-MSH E0 logs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# <name> { type <type>; <kind>Labels List<label> <n>( ... ) ... }
ZONE = re.compile(rb"\n(\w[\w.:-]*)\s*\{\s*type\s+(\w+);\s*(\w+)Labels\s+List<label>\s*(\d+)")


def main(path: Path) -> None:
    zones = [tuple(m.decode() for m in z.groups()) for z in ZONE.finditer(path.read_bytes())]
    for name, kind, labels, count in zones:
        print(f"{name} {kind} {labels}Labels {count}")
    if not zones:
        print("(no zones)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
