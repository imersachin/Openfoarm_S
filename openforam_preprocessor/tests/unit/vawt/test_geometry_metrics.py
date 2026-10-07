from __future__ import annotations

import numpy as np
import pytest
import trimesh

from geometry.metrics import body_orientations, contains_points, winding_numbers
from tests.fixtures.vawt.rotor import rotor_mesh


def test_winding_number_inside_outside_and_inverted() -> None:
    box = trimesh.creation.box(extents=(2, 2, 2))
    inverted = box.copy()
    inverted.invert()
    points = np.array([[0, 0, 0], [5, 0, 0]])

    assert winding_numbers(points, box.vertices, box.faces) == pytest.approx([1.0, 0.0],
                                                                             abs=1e-9)
    assert winding_numbers(points, inverted.vertices, inverted.faces) == pytest.approx(
        [-1.0, 0.0], abs=1e-9
    )
    assert contains_points(points, inverted.vertices, inverted.faces).tolist() == [True, False]


def test_contains_points_on_fixture_rotor() -> None:
    mesh = rotor_mesh()
    points = [[0.5, 0, 0], [0, 0, 0], [0.7, 0, 0], [0.5, 0, 0.55]]  # blade, shaft, gap, above

    assert contains_points(np.array(points), mesh.vertices, mesh.faces).tolist() == [
        True, True, False, False,
    ]


def test_body_orientations_finds_the_single_inside_out_body() -> None:
    bodies = body_orientations(rotor_mesh(invert_body=2))  # blade at -u (x = -0.5)

    assert len(bodies) == 5
    assert all(body.closed for body in bodies)
    flipped = [body for body in bodies if body.inside_out]
    assert len(flipped) == 1
    assert flipped[0].bounds_max[0] == pytest.approx(-0.48)


def test_body_order_is_deterministic() -> None:
    first = body_orientations(rotor_mesh())
    second = body_orientations(rotor_mesh())

    assert first == second
    assert [b.bounds_min for b in first] == sorted(b.bounds_min for b in first)


def test_open_body_has_no_volume() -> None:
    box = trimesh.creation.box()
    open_box = trimesh.Trimesh(box.vertices, box.faces[:-1], process=False)

    (body,) = body_orientations(open_box)

    assert body.closed is False
    assert body.signed_volume is None
    assert body.inside_out is False
