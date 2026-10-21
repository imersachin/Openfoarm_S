"""OpenFOAM binary list files for tests (M2a): write them as v2512 does, and read
the points back. The application never reads a binary polyMesh; the fakes
write one so that a leftover text read fails, and the real-OpenFOAM tests read
points to compare two meshes."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def header(class_name: str, object_name: str, note: str = "") -> str:
    extra = f'    note        "{note}";\n' if note else ""
    return ("FoamFile\n{\n    version     2.0;\n    format      binary;\n"
            '    arch        "LSB;label=32;scalar=64";\n'
            f"    class       {class_name};\n{extra}    location    \"constant/polyMesh\";\n"
            f"    object      {object_name};\n}}\n\n")


def binary_list(values: np.ndarray) -> bytes:
    """N\\n( raw little-endian bytes )\\n, as OpenFOAM writes a binary List."""
    return f"{len(values)}\n(".encode() + np.ascontiguousarray(values).tobytes() + b")\n"


def write(path: Path, class_name: str, values: np.ndarray, note: str = "") -> None:
    path.write_bytes(header(class_name, path.name, note).encode() + binary_list(values))


def read_points(path: Path) -> np.ndarray:
    """The points of a binary (or text) points file, (n, 3) float64."""
    data = path.read_bytes()
    head = data[:data.index(b"}", data.index(b"FoamFile")) + 1]
    if not re.search(rb"format\s+binary\s*;", head):
        from visualization.foam_reader import read_points as read_text_points

        return read_text_points(path)
    end = data.index(b"}", data.index(b"FoamFile")) + 1
    match = re.compile(rb"\n(\d+)\s*\(").search(data, end)  # after the "// * * *" line
    assert match is not None, f"{path}: no list"
    count = int(match[1])
    start = match.end()
    return np.frombuffer(data[start:start + 24 * count], dtype="<f8").reshape(-1, 3).copy()
