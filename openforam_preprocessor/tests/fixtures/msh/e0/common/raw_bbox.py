"""Bounding box straight from a .msh file, without converting (prototype for
plan E0.6; evidence only, the application version comes in M1b).

    python raw_bbox.py <file.msh>

ASCII only: Fluent node sections "(10 (zone first last type nd)(...))" with a
zone id other than 0, and Gmsh $Nodes in MSH 2.2 or 4.1. Prints the box,
the node count and the time taken, or "not read: <reason>"."""

from __future__ import annotations

import io
import re
import sys
import time
from pathlib import Path

import numpy as np

FLUENT_NODES = re.compile(r"^\(10 \(([0-9a-fA-F]+) ([0-9a-fA-F]+) ([0-9a-fA-F]+) "
                          r"([0-9a-fA-F]+)(?: ([0-9a-fA-F]+))?\)")
BINARY_NODES = re.compile(r"^\((2010|3010) ")


def _box(chunks: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, int]:
    points = np.concatenate(chunks)
    return points.min(axis=0), points.max(axis=0), len(points)


def _rows(lines: io.TextIOBase, count: int, columns: int) -> np.ndarray:
    text = "".join(next(lines) for _ in range(count))
    return np.loadtxt(io.StringIO(text), usecols=range(columns), ndmin=2)


def fluent(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    chunks: list[np.ndarray] = []
    with path.open("r", encoding="latin-1") as lines:
        for line in lines:
            if BINARY_NODES.match(line):
                raise ValueError("binary node section")
            match = FLUENT_NODES.match(line)
            if not match or int(match[1], 16) == 0:
                continue
            first, last = int(match[2], 16), int(match[3], 16)
            dimension = int(match[5] or "3", 16)
            if not line.rstrip().endswith("("):
                next(lines)  # the "(" line
            chunks.append(_rows(lines, last - first + 1, dimension))
    if not chunks:
        raise ValueError("no node section")
    return _box(chunks)


def gmsh(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    with path.open("r", encoding="latin-1") as lines:
        next(lines)
        version, binary = next(lines).split()[:2]
        if binary != "0":
            raise ValueError("binary MSH")
        for line in lines:
            if line.startswith("$Nodes"):
                break
        else:
            raise ValueError("no $Nodes")
        if version.startswith("2"):
            count = int(next(lines))
            points = _rows(lines, count, 4)[:, 1:]
            return points.min(axis=0), points.max(axis=0), len(points)
        if not version.startswith("4"):
            raise ValueError(f"MSH {version}")
        blocks = int(next(lines).split()[0])
        chunks: list[np.ndarray] = []
        for _ in range(blocks):
            _, _, parametric, count = (int(v) for v in next(lines).split())
            for _ in range(count):
                next(lines)  # node tags
            if count:
                chunks.append(_rows(lines, count, 3 + (parametric and 0)))
        return _box(chunks)


def main(path: Path) -> None:
    start = time.perf_counter()
    try:
        head = path.open("rb").read(64)
        reader = gmsh if head.startswith(b"$MeshFormat") else fluent
        low, high, count = reader(path)
    except (ValueError, StopIteration, OSError) as error:
        print(f"{path.name}: not read: {error or type(error).__name__} "
              f"({time.perf_counter() - start:.2f} s)")
        return
    print(f"{path.name}: {count} nodes, box {low.tolist()} {high.tolist()}, "
          f"{path.stat().st_size / 1e6:.1f} MB in {time.perf_counter() - start:.2f} s")


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        main(Path(argument))
