"""U1-1: the rotor axis point and D (owner, after E1).

The VAWT preset centred the zone, the domain and the rotation on the rotor's
bounding-box centre and took D as its largest extent normal to the axis. For
an odd blade count the box centre is off the axis: on the E1 rotor (3
NACA0018 blades, R 0.5 m) by 0.098 m, and D was 0.918 m instead of the swept
1.018 m. Now the axis passes through the area-weighted centroid of the rotor
surface (or a point the user enters), and D is swept about it. These tests
fail on the old behaviour: they put the true axis at (0.3, -0.2), away from
the box centre and the origin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from case_setup.setup import _zone_origin
from machines.config import MachineProjectConfig
from machines.previews import AXIS_POINT, BOX_CENTRE
from machines.service import MachineService
from tests.fixtures.vawt.three_blade import (
    BOX_EXTENT,
    BOX_OFFSET,
    SWEPT_DIAMETER,
    three_blade_rotor,
    write_three_blade,
)
from vawt.config import Axis
from vawt.presets import ZONE_DIAMETER_D
from vawt.rotor_metrics import compute_rotor_metrics
from vawt.service import VawtService

AXIS_UV = (0.3, -0.2)  # the true axis of the test rotor, in (x, y)


def source(path: Path) -> dict[str, Any]:
    return {"source_path": str(path), "source_units": "m"}


@pytest.fixture
def rotor(tmp_path: Path) -> Path:
    return write_three_blade(tmp_path / "inputs" / "three_blade.stl", AXIS_UV)


def vawt_draft(root: Path, rotor: Path, **extra: Any) -> dict[str, Any]:
    service = MachineService(root)
    result = service.draft_from_preset(service.choose_profile("vawt"), {
        "project_name": "U1", "source": source(rotor), "axis": "z", "flow_axis": "x", **extra})
    assert result.draft is not None, result.issues
    return result.draft


def test_d_is_the_swept_diameter_on_the_e1_rotor() -> None:
    mesh = three_blade_rotor()
    metrics = compute_rotor_metrics(mesh.vertices, Axis.Z, faces=mesh.faces)
    assert metrics.diameter == pytest.approx(SWEPT_DIAMETER, abs=1e-5)  # 1.018 m
    assert metrics.candidates[2].diameter == pytest.approx(BOX_EXTENT, abs=1e-5)  # not 0.918
    assert metrics.sweep_radius == pytest.approx(SWEPT_DIAMETER / 2, abs=1e-5)
    assert metrics.axis_point == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
    # The box centre stays as reported, off the axis.
    assert metrics.centre[:2] == pytest.approx(BOX_OFFSET, abs=1e-6)


def test_the_vawt_preset_centres_everything_on_the_true_axis(tmp_path: Path,
                                                             rotor: Path) -> None:
    draft = vawt_draft(tmp_path / "project", rotor)
    zone = draft["rotating_zones"][0]
    shape = zone["shape"]
    assert (shape["centre_u"], shape["centre_v"]) == pytest.approx(AXIS_UV, abs=1e-6)
    assert shape["diameter"] == pytest.approx(ZONE_DIAMETER_D * SWEPT_DIAMETER, abs=1e-4)
    # The rotation (case setup) turns about the axis, not the box centre.
    config = MachineProjectConfig.model_validate(draft)
    assert _zone_origin(config.rotating_zones[0])[:2] == pytest.approx(AXIS_UV, abs=1e-6)
    # The domain: inlet 3 D upstream of the axis, lateral faces 1.5 D each side of it.
    bounds = draft["domain"]["bounds"]
    assert bounds["minimum"]["x"] == pytest.approx(AXIS_UV[0] - 3 * SWEPT_DIAMETER, abs=1e-4)
    assert (bounds["minimum"]["y"] + bounds["maximum"]["y"]) / 2 == pytest.approx(
        AXIS_UV[1], abs=1e-6)


def test_the_old_app_preset_uses_the_same_axis(tmp_path: Path, rotor: Path) -> None:
    draft, issues = VawtService(tmp_path / "old").draft_from_preset(
        {"project_name": "U1", "geometry": {**source(rotor), "patch_name": "rotor"}},
        Axis.Z, Axis.X)
    assert draft is not None, issues
    zone = draft["rotating_zone"]
    assert (zone["centre_u"], zone["centre_v"]) == pytest.approx(AXIS_UV, abs=1e-6)
    assert zone["diameter"] == pytest.approx(ZONE_DIAMETER_D * SWEPT_DIAMETER, abs=1e-4)


def test_an_entered_axis_point_replaces_the_centroid(tmp_path: Path, rotor: Path) -> None:
    point = {"u": 0.31, "v": -0.19}
    draft = vawt_draft(tmp_path / "vawt", rotor, axis_point=point)
    shape = draft["rotating_zones"][0]["shape"]
    assert (shape["centre_u"], shape["centre_v"]) == pytest.approx((0.31, -0.19))
    service = MachineService(tmp_path / "info")
    info, _ = service.rotor_info(source(rotor), Axis.Z, point)
    assert info is not None and info.centroid == pytest.approx(AXIS_UV, abs=1e-6)
    vertices = np.asarray(three_blade_rotor(AXIS_UV).vertices)
    swept = 2 * np.hypot(vertices[:, 0] - 0.31, vertices[:, 1] + 0.19).max()
    assert info.diameter == pytest.approx(swept, abs=1e-5)  # about the entered point
    hawt = service.draft_from_preset(service.choose_profile("hawt"), {
        "project_name": "H", "source": source(rotor), "axis": "z", "axis_point": point})
    assert hawt.draft is not None
    hshape = hawt.draft["rotating_zones"][0]["shape"]
    assert (hshape["centre_u"], hshape["centre_v"]) == pytest.approx((0.31, -0.19))


def test_the_proposed_axis_is_drawn_before_the_preset(tmp_path: Path, rotor: Path) -> None:
    scene, arrows, issues = MachineService(tmp_path / "p").axis_preview(source(rotor), Axis.Z)
    assert scene is not None, issues
    point = next(m for m in scene.markers if m.kind == AXIS_POINT)
    assert point.points[0][:2] == pytest.approx(AXIS_UV, abs=1e-5)
    box = next(m for m in scene.markers if m.kind == BOX_CENTRE)
    offset = float(np.hypot(*BOX_OFFSET))
    assert f"{offset:.4g} m off the axis" in box.name  # 0.09769 m
    circle = next(line for line in scene.lines if line.kind == AXIS_POINT)
    ring = circle.points[~np.isnan(circle.points[:, 0])]
    radii = np.hypot(ring[:, 0] - AXIS_UV[0], ring[:, 1] - AXIS_UV[1])
    assert radii == pytest.approx(SWEPT_DIAMETER / 2, abs=1e-5)
    assert [a.kind for a in arrows] == ["axis"] and "(not applied)" in arrows[0].label
    assert arrows[0].path[:, :2] == pytest.approx(np.array([AXIS_UV, AXIS_UV]), abs=1e-5)
