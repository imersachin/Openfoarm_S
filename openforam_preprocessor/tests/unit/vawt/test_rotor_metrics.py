from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from geometry.importer import import_stl
from geometry.transformer import GeometryTransform
from tests.fixtures.vawt.rotor import RotorSpec, rotor_mesh, write_rotor
from vawt.config import Axis, RotorGeometryConfig
from vawt.rotor_metrics import compute_rotor_metrics

FLOAT32 = 1e-6  # binary STL stores float32


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_exact_metrics_and_suggestion_in_each_orientation(axis: str) -> None:
    spec = RotorSpec(axis=axis, centre=(1.0, -2.0, 3.0))

    metrics = compute_rotor_metrics(rotor_mesh(spec).vertices, Axis(axis))

    assert metrics.suggested_axis is Axis(axis)
    assert metrics.axis is Axis(axis)
    assert metrics.diameter == pytest.approx(spec.diameter, rel=1e-12)
    assert metrics.span == pytest.approx(spec.span, rel=1e-12)
    assert metrics.centre == pytest.approx(spec.centre, abs=1e-12)
    # Farthest point in plane: a blade corner at radius r + t/2, chord offset c/2.
    expected_sweep = math.hypot(spec.blade_radius + spec.blade_thickness / 2,
                                spec.blade_chord / 2)
    assert metrics.sweep_radius == pytest.approx(expected_sweep, rel=1e-12)


def test_metrics_from_file_match_within_float32(tmp_path: Path) -> None:
    path = write_rotor(tmp_path / "rotor.stl", binary_solid_header=True)

    mesh = import_stl(path).mesh
    assert mesh is not None
    metrics = compute_rotor_metrics(mesh.vertices, Axis.Z)

    assert metrics.diameter == pytest.approx(RotorSpec().diameter, abs=FLOAT32)
    assert metrics.span == pytest.approx(RotorSpec().span, abs=FLOAT32)


def test_centimetre_copy_gives_same_metrics_after_unit_conversion(tmp_path: Path) -> None:
    path = write_rotor(tmp_path / "rotor_cm.stl", scale=100.0)
    geometry = RotorGeometryConfig(source_path=path, source_units="cm")
    mesh = import_stl(path).mesh
    assert mesh is not None

    converted = GeometryTransform.from_config(geometry).apply_to_mesh(mesh)
    metrics = compute_rotor_metrics(converted.vertices, Axis.Z)

    assert metrics.diameter == pytest.approx(RotorSpec().diameter, abs=FLOAT32)
    assert metrics.span == pytest.approx(RotorSpec().span, abs=FLOAT32)


def test_no_suggestion_when_ambiguous() -> None:
    cube = trimesh.creation.box(extents=(1, 1, 1))

    metrics = compute_rotor_metrics(cube.vertices)

    assert metrics.suggested_axis is None
    assert "Choose the rotor axis explicitly" in metrics.suggestion_note


def test_suggestion_is_only_a_report() -> None:
    metrics = compute_rotor_metrics(rotor_mesh().vertices)  # no axis confirmed

    assert metrics.suggested_axis is Axis.Z
    assert metrics.axis is None
    assert metrics.diameter is None and metrics.span is None
    assert "Confirm before use" in metrics.suggestion_note


def test_candidates_report_every_axis() -> None:
    metrics = compute_rotor_metrics(rotor_mesh().vertices)

    by_axis = {c.axis: c for c in metrics.candidates}
    assert by_axis[Axis.Z].roundness == pytest.approx(1.0)
    assert by_axis[Axis.X].span == pytest.approx(RotorSpec().diameter)
    assert metrics.as_dict()["suggested_axis"] == "z"


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        compute_rotor_metrics(np.empty((0, 3)))
