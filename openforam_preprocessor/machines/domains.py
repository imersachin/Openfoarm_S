"""Imported surfaces: region names (docs/rotating_machinery.md section 5.3).

G1 needs only which regions each imported file holds. The full reader
(closedness, areas, the binary-STL and open-union checks) comes in G2.
"""

from __future__ import annotations

from pathlib import Path

_BINARY_HEADER = 80
_BINARY_TRIANGLE = 50  # normal, three vertices (12 float32) and a uint16


def is_binary_stl(path: Path) -> bool:
    """Binary STL by size, not by a leading "solid" (spec section 10): a binary
    file is exactly 84 + 50 * n bytes, where n is stored after the header."""
    size = path.stat().st_size
    if size < _BINARY_HEADER + 4:
        return False
    with path.open("rb") as stream:
        stream.seek(_BINARY_HEADER)
        count = int.from_bytes(stream.read(4), "little")
    return size == _BINARY_HEADER + 4 + _BINARY_TRIANGLE * count


def stl_region_names(path: Path) -> tuple[str, ...] | None:
    """The `solid <name>` names of an ASCII STL, in file order (repeats once).

    None when the file cannot be read or is binary (binary STL stores no
    region names).
    """
    try:
        if is_binary_stl(path):
            return None
        names: list[str] = []
        with path.open("rb") as stream:
            for line in stream:
                words = line.split()
                if len(words) >= 2 and words[0] == b"solid":
                    name = words[1].decode("ascii", errors="replace")
                    if name not in names:
                        names.append(name)
        return tuple(names)
    except OSError:
        return None
