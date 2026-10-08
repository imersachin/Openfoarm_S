"""V2 follow-ups to the V1 checks, from V0 evidence (docs/vawt_method_notes.md section 5)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from core.issues import IssueSeverity
from tests.fixtures.vawt.drafts import preset_draft
from vawt.case_generator import RESERVED_NAMES, domain_grid, rotor_grid
from vawt.config import VawtProjectConfig
from vawt.validation import ValidationThresholds, validate_vawt


@pytest.fixture
def draft(tmp_path: Path) -> dict[str, Any]:
    return preset_draft(tmp_path)


def issues(raw: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {i.code: i for i in validate_vawt(raw, **kwargs).issues}


def with_point(draft: dict[str, Any], field: str, point: tuple[float, ...]) -> dict[str, Any]:
    data = copy.deepcopy(draft)
    section = "rotating_zone" if field == "rotating_zone" else "domain"
    data[section]["location_in_mesh"] = dict(zip("xyz", point, strict=True))
    return data


# --- R1: mesh points on background-cell faces or edges ------------------------------------

@pytest.mark.parametrize("include_domain", [True, False])
def test_preset_points_are_off_cell_faces(tmp_path: Path, include_domain: bool) -> None:
    raw = preset_draft(tmp_path, include_domain=include_domain)

    assert "MESH_POINT_ON_CELL_FACE" not in issues(raw)


def test_v0_preset_inner_point_on_a_cell_edge_is_an_error(draft: dict[str, Any]) -> None:
    # The point snappyHexMesh rejected in V0 E4 (location_on_cell_edge).
    found = issues(with_point(draft, "rotating_zone", (0.65, 0.0, 0.0)))

    issue = found["MESH_POINT_ON_CELL_FACE"]
    assert issue.severity is IssueSeverity.ERROR
    assert issue.details["field"] == "rotating_zone.location_in_mesh"
    assert issue.details["axes"] == ["z"]  # y = 0 is mid-cell with 37 rotor cells
    assert "edge" not in issue.message


def test_point_on_two_face_planes_is_an_edge(draft: dict[str, Any]) -> None:
    grid = rotor_grid(VawtProjectConfig.model_validate(draft))
    on_edge = (0.65, grid.minimum[1] + 3 * grid.spacing(1), grid.minimum[2] + 5 * grid.spacing(2))

    issue = issues(with_point(draft, "rotating_zone", on_edge))["MESH_POINT_ON_CELL_FACE"]

    assert issue.details["axes"] == ["y", "z"]
    assert "edge" in issue.message


def test_outer_point_is_checked_against_the_domain_grid(draft: dict[str, Any]) -> None:
    grid = domain_grid(VawtProjectConfig.model_validate(draft))
    on_face = (grid.minimum[0] + 10 * grid.spacing(0), 0.0131, 0.0173)

    issue = issues(with_point(draft, "domain", on_face))["MESH_POINT_ON_CELL_FACE"]

    assert issue.details["field"] == "domain.location_in_mesh"
    assert issue.details["axes"] == ["x"]


def test_single_mesh_does_not_check_the_unused_inner_point(draft: dict[str, Any]) -> None:
    data = with_point(draft, "rotating_zone", (0.65, 0.0, 0.0))
    data["rotating_zone"]["interface"] = "CELL_ZONE"

    assert "MESH_POINT_ON_CELL_FACE" not in issues(data)


def test_face_tolerance_is_configurable(draft: dict[str, Any]) -> None:
    grid = rotor_grid(VawtProjectConfig.model_validate(draft))
    near = (0.65, 0.0131, grid.minimum[2] + 5 * grid.spacing(2) + 1e-3 * grid.spacing(2))

    assert "MESH_POINT_ON_CELL_FACE" not in issues(with_point(draft, "rotating_zone", near))
    assert "MESH_POINT_ON_CELL_FACE" in issues(
        with_point(draft, "rotating_zone", near),
        thresholds=ValidationThresholds(point_face_tolerance_cells=1e-2),
    )


# --- R2: absolute layers too thin for the cells next to them --------------------------------

def absolute(draft: dict[str, Any], first: float) -> dict[str, Any]:
    data = copy.deepcopy(draft)
    data["layers"].update(enabled=True, sizing="ABSOLUTE", first_layer_thickness=first,
                          min_thickness_m=1e-5)
    return data


def test_v0_preset_first_layer_warns(draft: dict[str, Any]) -> None:
    # E3a: D/5000 with 3 layers added 0 % of layers.
    issue = issues(absolute(draft, 1.04 / 5000))["LAYERS_TOO_THIN_FOR_CELLS"]

    assert issue.severity is IssueSeverity.WARNING
    assert issue.details["final_layer_thickness"] == pytest.approx(1.04 / 5000 * 1.44)
    assert issue.details["finest_blade_cell"] == pytest.approx(1.04 / 22 / 4)


def test_v0_working_first_layer_does_not_warn(draft: dict[str, Any]) -> None:
    # E3d: 2.46e-3 m added layers.
    assert "LAYERS_TOO_THIN_FOR_CELLS" not in issues(absolute(draft, 2.46e-3))


def test_relative_and_disabled_layers_are_not_checked(draft: dict[str, Any]) -> None:
    relative = copy.deepcopy(draft)
    relative["layers"]["enabled"] = True
    disabled = absolute(draft, 1e-6)
    disabled["layers"]["enabled"] = False

    assert "LAYERS_TOO_THIN_FOR_CELLS" not in issues(relative)
    assert "LAYERS_TOO_THIN_FOR_CELLS" not in issues(disabled)


def test_single_mesh_uses_the_effective_zone_cell_size(draft: dict[str, Any]) -> None:
    # Finest blade cell: AMI D/22/4 = 11.8 mm; single mesh D/36/4 = 7.2 mm
    # (effective zone cells). A 10 mm first layer fits the first, not the second.
    ami = absolute(draft, 0.010)
    single = copy.deepcopy(ami)
    single["rotating_zone"]["interface"] = "CELL_ZONE"

    assert "ABSOLUTE_LAYER_TOO_THICK" not in issues(ami)
    found = issues(single)["ABSOLUTE_LAYER_TOO_THICK"]
    assert found.details["finest_blade_cell"] == pytest.approx(1.04 / 36 / 4)


def test_layer_fraction_is_configurable(draft: dict[str, Any]) -> None:
    raw = absolute(draft, 2.46e-3)

    assert "LAYERS_TOO_THIN_FOR_CELLS" in issues(
        raw, thresholds=ValidationThresholds(min_final_layer_fraction=0.5))


# --- reserved names -------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
def test_reserved_domain_patch_names_are_blocking(draft: dict[str, Any], name: str) -> None:
    data = copy.deepcopy(draft)
    data["domain"]["patches"] = {"outlet": name}

    issue = issues(data)["RESERVED_PATCH_NAME"]

    assert issue.severity is IssueSeverity.BLOCKING
    assert issue.details == {"field": "domain.patches.outlet", "name": name}


def test_reserved_rotor_patch_name_is_blocking(draft: dict[str, Any]) -> None:
    data = copy.deepcopy(draft)
    data["geometry"]["patch_name"] = "AMI1"

    assert issues(data)["RESERVED_PATCH_NAME"].details["field"] == "geometry.patch_name"
