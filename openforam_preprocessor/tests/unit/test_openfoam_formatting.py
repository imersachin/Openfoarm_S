from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config.models import OpenFOAMProfile, Vector3
from openfoam.commands import feature_extraction_command, supports_feature_extraction
from openfoam.dictionary import foam_scalar, foam_vector


def test_foam_scalar_round_trips_precision() -> None:
    assert foam_scalar(0.1) == "0.1"
    assert foam_scalar(1e-13) == "1e-13"
    assert foam_scalar(1234567.891) == "1234567.891"
    assert float(foam_scalar(1 / 3)) == 1 / 3
    assert foam_vector(1, -2.5, 3) == "(1.0 -2.5 3.0)"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_foam_scalar_rejects_non_finite(value: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        foam_scalar(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_config_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValidationError):
        Vector3(x=value, y=0, z=0)


def test_feature_extraction_command_by_profile() -> None:
    case = Path("case")
    assert supports_feature_extraction(OpenFOAMProfile.OPENCFD)
    assert feature_extraction_command(OpenFOAMProfile.OPENCFD, case) == (
        "surfaceFeatureExtract", "-case", str(case),
    )
    assert not supports_feature_extraction(OpenFOAMProfile.FOUNDATION)
    assert feature_extraction_command(OpenFOAMProfile.FOUNDATION, case) is None
