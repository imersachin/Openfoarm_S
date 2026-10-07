from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.config.models import OpenFOAMProfile
from core.issues import IssueStage

CHECK_MESH_LOG = "04_checkMesh.log"


@dataclass(frozen=True)
class MeshingStep:
    argv: tuple[str, ...]
    log_name: str
    stage: IssueStage


def block_mesh_step(case_root: Path) -> MeshingStep:
    return MeshingStep(("blockMesh", "-case", str(case_root)), "01_blockMesh.log",
                       IssueStage.BLOCK_MESH)


def feature_extraction_step(argv: Sequence[str]) -> MeshingStep:
    return MeshingStep(tuple(argv), "02_surfaceFeatureExtract.log",
                       IssueStage.FEATURE_EXTRACTION)


def snappy_step(case_root: Path) -> MeshingStep:
    return MeshingStep(("snappyHexMesh", "-case", str(case_root), "-overwrite"),
                       "03_snappyHexMesh.log", IssueStage.SNAPPY_HEX_MESH)


def check_mesh_step(case_root: Path) -> MeshingStep:
    return MeshingStep(
        ("checkMesh", "-case", str(case_root), "-allGeometry", "-allTopology", "-meshQuality"),
        CHECK_MESH_LOG,
        IssueStage.CHECK_MESH,
    )


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
