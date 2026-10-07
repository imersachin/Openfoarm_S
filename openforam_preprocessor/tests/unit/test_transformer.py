from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from core.config.models import GeometryConfig, LengthUnit
from geometry.transformer import GeometryTransform, stl_ascii_bytes, write_stl_artifact


def make_transform(
    units: str = "m",
    scale: float = 1.0,
    rotation: tuple[float, float, float] = (0, 0, 0),
    translation: tuple[float, float, float] = (0, 0, 0),
) -> GeometryTransform:
    return GeometryTransform.from_config(GeometryConfig(
        source_path=Path("part.stl"),
        source_units=units,
        scale=scale,
        rotation_deg={"x": rotation[0], "y": rotation[1], "z": rotation[2]},
        translation={"x": translation[0], "y": translation[1], "z": translation[2]},
    ))


def apply(transform: GeometryTransform, point: tuple[float, float, float]) -> np.ndarray:
    return transform.apply(np.array([point]))[0]


@pytest.mark.parametrize(
    ("unit", "metres"),
    [("m", 1.0), ("cm", 0.01), ("mm", 0.001), ("um", 1e-6), ("in", 0.0254), ("ft", 0.3048)],
)
def test_unit_conversion_to_metres(unit: str, metres: float) -> None:
    assert apply(make_transform(unit), (1, 2, 3)) == pytest.approx((metres, 2 * metres, 3 * metres))


def test_source_units_are_required() -> None:
    with pytest.raises(ValueError, match="source_units"):
        GeometryConfig(source_path=Path("part.stl"))  # type: ignore[call-arg]


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(ValueError):
        GeometryConfig(source_path=Path("part.stl"), source_units="furlong")


def test_length_unit_vocabulary() -> None:
    assert {u.value for u in LengthUnit} == {"m", "cm", "mm", "um", "in", "ft"}


def test_scale_is_about_origin() -> None:
    assert apply(make_transform(scale=2), (1, 2, 3)).tolist() == [2, 4, 6]


@pytest.mark.parametrize(
    ("rotation", "point", "expected"),
    [
        ((90, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 90, 0), (0, 0, 1), (1, 0, 0)),
        ((0, 0, 90), (1, 0, 0), (0, 1, 0)),
        ((0, 0, 180), (1, 2, 3), (-1, -2, 3)),
        ((0, 0, -90), (1, 0, 0), (0, -1, 0)),
    ],
)
def test_quarter_turn_rotations_are_exact(rotation, point, expected) -> None:
    assert apply(make_transform(rotation=rotation), point).tolist() == list(expected)


def test_rotation_order_is_x_then_y_then_z_about_fixed_axes() -> None:
    # X first: (0,1,0) -> (0,0,1); then Z leaves it at (0,0,1).
    # (Z first would give (-1,0,0).)
    result = apply(make_transform(rotation=(90, 0, 90)), (0, 1, 0))
    assert result.tolist() == [0, 0, 1]


def test_general_angle_rotation() -> None:
    result = apply(make_transform(rotation=(0, 0, 45)), (1, 0, 0))
    assert result == pytest.approx((math.sqrt(0.5), math.sqrt(0.5), 0.0))


def test_translation_is_applied_after_rotation() -> None:
    # Rotate then translate: (1,0,0) -> (0,1,0) -> (1,1,0).
    # (Translate first would give (0,2,0).)
    result = apply(make_transform(rotation=(0, 0, 90), translation=(1, 0, 0)), (1, 0, 0))
    assert result.tolist() == [1, 1, 0]


def test_translation_is_in_metres_after_unit_conversion() -> None:
    result = apply(make_transform("mm", translation=(1, 0, 0)), (1000, 0, 0))
    assert result == pytest.approx((2.0, 0.0, 0.0))


def test_combined_transformation_full_order() -> None:
    # 10 mm -> 0.01 m -> x2 = 0.02 -> Rz90 -> (0, 0.02, 0) -> +(0, 0, 5) m
    transform = make_transform("mm", scale=2, rotation=(0, 0, 90), translation=(0, 0, 5))
    assert apply(transform, (10, 0, 0)) == pytest.approx((0.0, 0.02, 5.0))


def test_transform_preserves_winding_and_scales_volume() -> None:
    box = trimesh.creation.box(extents=(1000, 1000, 1000))  # mm
    transformed = make_transform("mm", scale=2, rotation=(30, 45, 60)).apply_to_mesh(box)

    assert transformed.volume == pytest.approx(8.0)  # (1 m * 2)^3, positive => outward
    assert np.array_equal(transformed.faces, box.faces)


def test_written_stl_round_trips_exact_coordinates(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=(1, 2, 3))
    transform = make_transform(rotation=(0, 0, 33), translation=(0.1, 0.2, 0.3))
    transformed = transform.apply_to_mesh(box)
    path = tmp_path / "part.stl"

    write_stl_artifact(transformed, path, "part")
    loaded = trimesh.load_mesh(path, process=False)

    expected = np.asarray(transformed.vertices)[np.asarray(transformed.faces)]
    actual = np.asarray(loaded.vertices)[np.asarray(loaded.faces)]
    assert np.array_equal(actual, expected)


def test_stl_output_is_deterministic_and_write_if_changed(tmp_path: Path) -> None:
    box = trimesh.creation.box()
    path = tmp_path / "part.stl"

    assert stl_ascii_bytes(box, "part") == stl_ascii_bytes(box.copy(), "part")
    assert write_stl_artifact(box, path, "part") is True
    assert write_stl_artifact(box, path, "part") is False
    assert path.read_bytes().startswith(b"solid part\n")
