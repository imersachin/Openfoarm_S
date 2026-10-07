"""Every row of spec section 8, failing and passing."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import trimesh

from core.issues import IssueSeverity
from tests.fixtures.vawt.drafts import preset_draft
from tests.fixtures.vawt.rotor import RotorSpec, rotor_mesh
from vawt.validation import ValidationThresholds, validate_vawt

D = RotorSpec().diameter


@pytest.fixture
def draft(tmp_path: Path) -> dict[str, Any]:
    return preset_draft(tmp_path)


def set_(draft: dict[str, Any], **values: Any) -> dict[str, Any]:
    """Return a copy with dotted paths (written with __) replaced."""
    data = copy.deepcopy(draft)
    for dotted, value in values.items():
        *parents, leaf = dotted.split("__")
        target = data
        for key in parents:
            target = target[key]
        target[leaf] = value
    return data


def codes(raw: dict[str, Any], **kwargs: Any) -> dict[str, IssueSeverity]:
    return {i.code: i.severity for i in validate_vawt(raw, **kwargs).issues}


def test_preset_draft_passes_every_check(draft: dict[str, Any]) -> None:
    result = validate_vawt(draft)

    assert result.can_run
    assert set(codes(draft)) == {"MULTIPLE_COMPONENTS"}  # INFO from the shared validator
    assert result.metrics is not None and result.metrics.diameter == pytest.approx(D, abs=1e-6)


def test_units_not_chosen(draft: dict[str, Any]) -> None:
    assert codes(set_(draft, geometry__source_units=None)) == {
        "UNITS_NOT_CHOSEN": IssueSeverity.BLOCKING,
    }


def test_axis_not_confirmed(draft: dict[str, Any]) -> None:
    assert codes(set_(draft, rotor__axis=None)) == {
        "ROTOR_AXIS_NOT_CONFIRMED": IssueSeverity.BLOCKING,
    }


def test_rotor_outside_cylinder_radially_and_axially(draft: dict[str, Any]) -> None:
    assert codes(set_(draft, rotating_zone__diameter=1.0))["ROTOR_OUTSIDE_ZONE"] is (
        IssueSeverity.BLOCKING
    )
    assert "ROTOR_OUTSIDE_ZONE" in codes(set_(draft, rotating_zone__axis_max=0.5))


def test_small_clearance_warns(draft: dict[str, Any]) -> None:
    tight = set_(draft, rotating_zone__diameter=2 * 0.5296 + 0.01)  # sweep r = 0.5295

    found = codes(tight)

    assert found["ZONE_CLEARANCE_SMALL"] is IssueSeverity.WARNING
    assert "ROTOR_OUTSIDE_ZONE" not in found


def test_clearance_threshold_is_configurable(draft: dict[str, Any]) -> None:
    assert "ZONE_CLEARANCE_SMALL" in codes(
        draft, thresholds=ValidationThresholds(min_clearance_cells=10.0)
    )


def test_cylinder_outside_domain(draft: dict[str, Any]) -> None:
    found = codes(set_(draft, domain__bounds__maximum__y=0.5))

    assert found["ZONE_OUTSIDE_DOMAIN"] is IssueSeverity.BLOCKING


def test_wake_outside_domain(draft: dict[str, Any]) -> None:
    found = codes(set_(draft, refinement__wake__box__maximum__x=100.0))

    assert found == {"MULTIPLE_COMPONENTS": IssueSeverity.INFO,
                     "WAKE_OUTSIDE_DOMAIN": IssueSeverity.ERROR}


def test_outer_point_outside_domain_or_inside_cylinder(draft: dict[str, Any]) -> None:
    outside = set_(draft, domain__location_in_mesh={"x": -100.0, "y": 0.0, "z": 0.0})
    in_cylinder = set_(draft, domain__location_in_mesh={"x": 0.7, "y": 0.0, "z": 0.0})

    assert codes(outside)["OUTER_POINT_INVALID"] is IssueSeverity.BLOCKING
    assert codes(in_cylinder)["OUTER_POINT_INVALID"] is IssueSeverity.BLOCKING


def test_inner_point_outside_cylinder(draft: dict[str, Any]) -> None:
    moved = set_(draft, rotating_zone__location_in_mesh={"x": 2.0, "y": 0.0, "z": 0.0})

    assert codes(moved)["INNER_POINT_OUTSIDE_ZONE"] is IssueSeverity.BLOCKING


def test_inner_point_inside_rotor_solid(draft: dict[str, Any]) -> None:
    in_blade = set_(draft, rotating_zone__location_in_mesh={"x": 0.5, "y": 0.0, "z": 0.0})

    found = codes(in_blade)

    assert found["INNER_POINT_IN_ROTOR"] is IssueSeverity.BLOCKING
    assert "INNER_POINT_OUTSIDE_ZONE" not in found


def test_open_surface_cannot_be_checked(draft: dict[str, Any], tmp_path: Path) -> None:
    mesh = rotor_mesh()
    open_mesh = trimesh.Trimesh(mesh.vertices, mesh.faces[:-1], process=False)
    path = tmp_path / "open.stl"
    open_mesh.export(path)

    found = codes(set_(draft, geometry__source_path=str(path)))

    assert found["INNER_POINT_UNCHECKED"] is IssueSeverity.WARNING
    assert "INNER_POINT_IN_ROTOR" not in found


def test_inside_out_body(tmp_path: Path) -> None:
    result = validate_vawt(preset_draft(tmp_path, invert_body=2))

    issues = [i for i in result.issues if i.code == "BODY_INSIDE_OUT"]
    assert len(issues) == 1
    assert issues[0].severity is IssueSeverity.WARNING
    assert issues[0].details["bounds_max"][0] == pytest.approx(-0.48, abs=1e-6)
    assert result.can_run  # a warning does not stop a run


def test_absolute_first_layer_thicker_than_finest_blade_cell(draft: dict[str, Any]) -> None:
    finest = draft["rotating_zone"]["cell_size"] / 2 ** 2
    layered = set_(draft, layers__enabled=True, layers__sizing="ABSOLUTE",
                   layers__min_thickness_m=1e-6)

    assert "ABSOLUTE_LAYER_TOO_THICK" not in codes(
        set_(layered, layers__first_layer_thickness=finest / 2)
    )
    assert codes(set_(layered, layers__first_layer_thickness=finest * 2))[
        "ABSOLUTE_LAYER_TOO_THICK"
    ] is IssueSeverity.ERROR


def test_min_thickness_above_total_layer_thickness(draft: dict[str, Any]) -> None:
    relative = set_(draft, layers__enabled=True, layers__count=1,
                    layers__final_layer_thickness=0.2)
    absolute = set_(draft, layers__enabled=True, layers__sizing="ABSOLUTE", layers__count=2,
                    layers__expansion_ratio=1.5, layers__first_layer_thickness=1e-4)

    assert codes(set_(relative, layers__min_thickness=0.3))[
        "LAYER_MIN_THICKNESS_TOO_LARGE"
    ] is IssueSeverity.ERROR
    assert "LAYER_MIN_THICKNESS_TOO_LARGE" not in codes(set_(relative, layers__min_thickness=0.1))
    # Absolute total = 1e-4 * (1 + 1.5) = 2.5e-4 m.
    assert "LAYER_MIN_THICKNESS_TOO_LARGE" in codes(set_(absolute, layers__min_thickness_m=3e-4))
    assert "LAYER_MIN_THICKNESS_TOO_LARGE" not in codes(
        set_(absolute, layers__min_thickness_m=2e-4)
    )


def test_relative_total_uses_all_layers(draft: dict[str, Any]) -> None:
    # Three layers, final 0.3, ratio 1.2: total = 0.3 * (1 + 1/1.2 + 1/1.44) = 0.758.
    layered = set_(draft, layers__enabled=True)

    assert "LAYER_MIN_THICKNESS_TOO_LARGE" not in codes(set_(layered, layers__min_thickness=0.75))
    assert "LAYER_MIN_THICKNESS_TOO_LARGE" in codes(set_(layered, layers__min_thickness=0.76))


def test_disabled_layers_are_not_checked(draft: dict[str, Any]) -> None:
    inactive = set_(draft, layers__enabled=False, layers__min_thickness=1.0,
                    layers__final_layer_thickness=0.01)

    assert "LAYER_MIN_THICKNESS_TOO_LARGE" not in codes(inactive)


def test_ami_without_domain(tmp_path: Path) -> None:
    no_domain = set_(preset_draft(tmp_path, include_domain=False),
                     rotating_zone__interface="AMI")

    assert codes(no_domain)["AMI_REQUIRES_DOMAIN"] is IssueSeverity.BLOCKING
    assert "AMI_REQUIRES_DOMAIN" not in codes(preset_draft(tmp_path, include_domain=False))


def test_too_few_zone_cells_across_diameter(draft: dict[str, Any]) -> None:
    coarse = set_(draft, rotating_zone__cell_size=D / 5)

    assert codes(coarse)["TOO_FEW_ZONE_CELLS"] is IssueSeverity.WARNING
    assert "TOO_FEW_ZONE_CELLS" not in codes(
        coarse, thresholds=ValidationThresholds(min_cells_across_diameter=4.0)
    )


def test_missing_geometry_file_is_reported(draft: dict[str, Any], tmp_path: Path) -> None:
    result = validate_vawt(set_(draft, geometry__source_path=str(tmp_path / "gone.stl")))

    assert result.metrics is None
    assert [i.code for i in result.issues] == ["GEOMETRY_SOURCE_MISSING"]
    assert not result.can_run


@pytest.mark.parametrize("axis,flow", [("x", "y"), ("y", "z"), ("z", "x")])
def test_every_orientation_passes(tmp_path: Path, axis: str, flow: str) -> None:
    assert validate_vawt(preset_draft(tmp_path, axis=axis, flow_axis=flow)).can_run


def test_validation_is_deterministic(draft: dict[str, Any]) -> None:
    raw = set_(draft, rotating_zone__cell_size=D / 5, refinement__wake__box__maximum__x=100.0)

    assert validate_vawt(raw).issues == validate_vawt(raw).issues
