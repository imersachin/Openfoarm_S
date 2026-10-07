from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from core.config.models import GeometryConfig

TRANSFORMATION_ORDER = ("unit_conversion", "scale", "rotation", "translation")
ROTATION_CONVENTION = "extrinsic fixed global axes, X then Y then Z (R = Rz @ Ry @ Rx)"
PIVOT = (0.0, 0.0, 0.0)


def _cos_sin(degrees: float) -> tuple[float, float]:
    # Exact values for quarter turns, so 90-degree rotations do not leave
    # 6e-17 noise in coordinates.
    quarter, remainder = divmod(degrees, 90.0)
    if remainder == 0.0:
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(quarter) % 4]
    radians = math.radians(degrees)
    return math.cos(radians), math.sin(radians)


def rotation_matrix_xyz(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """Rotation about fixed global axes: first X, then Y, then Z."""
    cx, sx = _cos_sin(rx_deg)
    cy, sy = _cos_sin(ry_deg)
    cz, sz = _cos_sin(rz_deg)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return rz @ ry @ rx


@dataclass(frozen=True)
class GeometryTransform:
    """Explicit source-to-metres transformation; see GeometryConfig for the contract."""

    source_units: str
    unit_factor: float
    scale: float
    rotation_deg: tuple[float, float, float]
    translation_m: tuple[float, float, float]

    @classmethod
    def from_config(cls, geometry: GeometryConfig) -> GeometryTransform:
        r, t = geometry.rotation_deg, geometry.translation
        return cls(
            source_units=geometry.source_units.value,
            unit_factor=geometry.source_units.to_metres,
            scale=geometry.scale,
            rotation_deg=(r.x, r.y, r.z),
            translation_m=(t.x, t.y, t.z),
        )

    def apply(self, points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64)
        converted = points * self.unit_factor          # 1. unit conversion -> metres
        scaled = converted * self.scale                # 2. scale about origin
        rotation = rotation_matrix_xyz(*self.rotation_deg)
        rotated = scaled @ rotation.T                  # 3. rotate about origin
        return rotated + np.asarray(self.translation_m)  # 4. translate (metres)

    def apply_to_mesh(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        # Proper rotation and positive scale: determinant > 0, so triangle
        # winding (and therefore normal orientation) is preserved.
        return trimesh.Trimesh(
            vertices=self.apply(mesh.vertices),
            faces=np.array(mesh.faces, copy=True),
            process=False,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "order": list(TRANSFORMATION_ORDER),
            "source_units": self.source_units,
            "target_units": "m",
            "unit_factor_to_metres": self.unit_factor,
            "scale": self.scale,
            "rotation_deg": list(self.rotation_deg),
            "rotation_convention": ROTATION_CONVENTION,
            "pivot": list(PIVOT),
            "translation_m": list(self.translation_m),
        }


def stl_ascii_bytes(mesh: trimesh.Trimesh, solid_name: str) -> bytes:
    """Deterministic ASCII STL with round-trip float precision."""
    triangles = np.asarray(mesh.vertices, dtype=np.float64)[np.asarray(mesh.faces)]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(
        normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 0
    )

    def fmt(values: np.ndarray) -> str:
        return " ".join(repr(float(v)) for v in values)

    lines = [f"solid {solid_name}"]
    for normal, triangle in zip(normals, triangles, strict=True):
        lines.append(f"  facet normal {fmt(normal)}")
        lines.append("    outer loop")
        lines.extend(f"      vertex {fmt(vertex)}" for vertex in triangle)
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {solid_name}")
    return ("\n".join(lines) + "\n").encode("ascii")


def write_stl_artifact(mesh: trimesh.Trimesh, path: Path, solid_name: str) -> bool:
    """Atomically write the STL if its content changed. Returns True when written."""
    content = stl_ascii_bytes(mesh, solid_name)
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return True
