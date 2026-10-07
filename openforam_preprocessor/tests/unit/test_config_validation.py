from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config.models import BackgroundMeshConfig, Bounds, MeshConfig, Vector3
from core.config.validation import validate_project_config
from core.issues import IssueCategory, IssueSeverity
from tests.helpers import build_config

DOMAIN = BackgroundMeshConfig(
    domain=Bounds(minimum=Vector3(x=0, y=0, z=0), maximum=Vector3(x=1, y=1, z=1)),
)


def test_location_inside_domain_is_accepted() -> None:
    MeshConfig(background=DOMAIN, location_in_mesh=Vector3(x=0.5, y=0.5, z=0.5))


@pytest.mark.parametrize(
    "point",
    [
        Vector3(x=2, y=0.5, z=0.5),   # outside
        Vector3(x=0.5, y=-1, z=0.5),  # outside
        Vector3(x=0, y=0.5, z=0.5),   # on boundary face
        Vector3(x=0.5, y=0.5, z=1),   # on boundary face
    ],
)
def test_location_outside_or_on_domain_boundary_is_rejected(point: Vector3) -> None:
    with pytest.raises(ValidationError, match="location_in_mesh"):
        MeshConfig(background=DOMAIN, location_in_mesh=point)


def test_valid_raw_config_returns_model_and_no_issues() -> None:
    raw = build_config(Path("part.stl")).model_dump(mode="json")

    result = validate_project_config(raw)

    assert result.is_valid
    assert result.issues == ()


def test_invalid_raw_config_returns_structured_issues() -> None:
    raw = build_config(Path("part.stl")).model_dump(mode="json")
    raw["mesh"]["location_in_mesh"] = {"x": 99, "y": 0, "z": 0}
    raw["geometry"]["source_path"] = "part.obj"

    result = validate_project_config(raw)

    assert not result.is_valid
    assert len(result.issues) == 2
    assert all(i.category is IssueCategory.CONFIGURATION for i in result.issues)
    assert all(i.severity is IssueSeverity.BLOCKING for i in result.issues)
    fields = {i.details["field"] for i in result.issues}
    assert fields == {"geometry", "mesh"}
    assert all(i.suggested_action for i in result.issues)


def test_feature_extraction_is_disabled_by_default() -> None:
    assert build_config(Path("part.stl")).mesh.surface.extract_features is False
