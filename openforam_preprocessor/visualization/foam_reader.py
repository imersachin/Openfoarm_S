"""Minimal reader for ASCII OpenFOAM polyMesh files and sets (display only).

Supports the uncompressed ASCII format this application configures in
controlDict (writeFormat ascii; writeCompression off). Anything else raises
FoamReadError with an explanation instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_HEADER = re.compile(r"FoamFile\s*\{(.*?)\}", re.DOTALL)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


class FoamReadError(ValueError):
    """The file is missing, binary/compressed, or not in the expected format."""


@dataclass(frozen=True)
class Patch:
    name: str
    patch_type: str
    n_faces: int
    start_face: int


@dataclass(frozen=True)
class PolyMeshSurface:
    points: np.ndarray  # (n, 3) float
    faces: list[np.ndarray]  # vertex labels per face
    owner: np.ndarray  # (n_faces,) int
    patches: tuple[Patch, ...]


def _read(path: Path) -> tuple[dict[str, str], str]:
    if not path.is_file():
        if path.with_name(path.name + ".gz").is_file():
            raise FoamReadError(f"{path.name} is compressed (.gz); only uncompressed ASCII "
                                "meshes can be displayed.")
        raise FoamReadError(f"{path} does not exist.")
    text = path.read_text(encoding="utf-8", errors="replace")
    header: dict[str, str] = {}
    match = _HEADER.search(text)
    if match:
        for entry in match.group(1).split(";"):
            parts = entry.split(None, 1)
            if len(parts) == 2:
                header[parts[0]] = parts[1].strip().strip('"')
        text = text[match.end():]
    if header.get("format", "ascii") != "ascii":
        raise FoamReadError(f"{path.name} is in {header['format']} format; only ASCII meshes "
                            "can be displayed.")
    return header, _COMMENTS.sub(" ", text)


def _list_body(path: Path, text: str) -> tuple[int, str]:
    """The declared size and the text inside the outermost list parentheses."""
    match = re.search(r"(\d+)\s*\(", text)
    if match is None:
        raise FoamReadError(f"{path.name}: no OpenFOAM list found.")
    end = text.rfind(")")
    if end < match.end():
        raise FoamReadError(f"{path.name}: unterminated list.")
    return int(match.group(1)), text[match.end():end]


def read_points(path: Path) -> np.ndarray:
    _, text = _read(path)
    size, body = _list_body(path, text)
    try:
        values = np.array(body.replace("(", " ").replace(")", " ").split(), dtype=float)
    except ValueError as exc:
        raise FoamReadError(f"{path.name}: non-numeric point data.") from exc
    if values.size != size * 3:
        raise FoamReadError(f"{path.name}: expected {size} points, found {values.size / 3:g}.")
    return values.reshape(size, 3)


def read_labels(path: Path) -> np.ndarray:
    """A labelList (owner, neighbour, sets)."""
    _, text = _read(path)
    size, body = _list_body(path, text)
    try:
        labels = np.array(body.split(), dtype=np.int64)
    except ValueError as exc:
        raise FoamReadError(f"{path.name}: non-integer labels.") from exc
    if labels.size != size:
        raise FoamReadError(f"{path.name}: expected {size} labels, found {labels.size}.")
    return labels


def read_faces(path: Path) -> list[np.ndarray]:
    _, text = _read(path)
    size, body = _list_body(path, text)
    try:
        tokens = np.array(body.replace("(", " ").replace(")", " ").split(), dtype=np.int64)
    except ValueError as exc:
        raise FoamReadError(f"{path.name}: non-integer face data.") from exc
    faces: list[np.ndarray] = []
    position = 0
    while position < tokens.size:
        count = int(tokens[position])
        faces.append(tokens[position + 1:position + 1 + count])
        position += 1 + count
    if len(faces) != size or position != tokens.size:
        raise FoamReadError(f"{path.name}: expected {size} faces, found {len(faces)}.")
    return faces


def read_boundary(path: Path) -> tuple[Patch, ...]:
    _, text = _read(path)
    patches: list[Patch] = []
    for name, body in re.findall(r"(\S+)\s*\{([^{}]*)\}", text):
        values = dict(re.findall(r"(\w+)\s+([^;]+);", body))
        try:
            patches.append(Patch(name, values.get("type", "patch"),
                                 int(values["nFaces"]), int(values["startFace"])))
        except (KeyError, ValueError) as exc:
            raise FoamReadError(f"boundary: patch {name} lacks nFaces/startFace.") from exc
    return tuple(patches)


def read_set(path: Path) -> tuple[str, np.ndarray]:
    """(set class, labels) for a faceSet/cellSet/pointSet file."""
    header, _ = _read(path)
    return header.get("class", "unknown"), read_labels(path)


def read_poly_mesh(poly_mesh: Path) -> PolyMeshSurface:
    return PolyMeshSurface(
        points=read_points(poly_mesh / "points"),
        faces=read_faces(poly_mesh / "faces"),
        owner=read_labels(poly_mesh / "owner"),
        patches=read_boundary(poly_mesh / "boundary"),
    )
