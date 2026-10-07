from __future__ import annotations

from pathlib import Path

from core.config.models import OpenFOAMProfile


def supports_feature_extraction(profile: OpenFOAMProfile) -> bool:
    """Feature extraction is implemented for openfoam.com (ESI) only.

    openfoam.org uses a different utility (surfaceFeatures) and dictionary
    (surfaceFeaturesDict) whose output location varies by version; it is not
    generated until that profile is explicitly modelled.
    """
    return profile is OpenFOAMProfile.OPENCFD


def feature_extraction_command(
    profile: OpenFOAMProfile, case_root: Path
) -> tuple[str, ...] | None:
    """Reads system/surfaceFeatureExtractDict; writes constant/triSurface/<name>.eMesh."""
    if not supports_feature_extraction(profile):
        return None
    return ("surfaceFeatureExtract", "-case", str(case_root))
