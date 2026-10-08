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
    # No -allGeometry: its extra checks (e.g. concave cells) fail on ordinary
    # snappyHexMesh meshes, and every failed check makes the mesh INVALID
    # (OpenFOAM v2512; see docs/architecture.md section 16).
    return MeshingStep(
        ("checkMesh", "-case", str(case_root), "-allTopology", "-meshQuality"),
        CHECK_MESH_LOG,
        IssueStage.CHECK_MESH,
    )


def topo_set_step(case_root: Path, log_name: str = "topoSet.log") -> MeshingStep:
    """Reads system/topoSetDict; writes sets and zones into constant/polyMesh."""
    return MeshingStep(("topoSet", "-case", str(case_root)), log_name, IssueStage.ZONE_CREATION)


def merge_meshes_step(master_case: Path, add_case: Path,
                      log_name: str = "mergeMeshes.log") -> MeshingStep:
    """Adds add_case's mesh into master_case's mesh, written into master_case.

    Run it from master_case: OpenFOAM v2512 appends "/processor" to the master
    path when the working directory's name ends in "processor".
    """
    return MeshingStep(("mergeMeshes", "-overwrite", str(master_case), str(add_case)),
                       log_name, IssueStage.MERGE_MESHES)


def create_patch_step(case_root: Path, log_name: str = "createPatch.log") -> MeshingStep:
    """Reads system/createPatchDict. Exits 0 even when a source patch is missing."""
    return MeshingStep(("createPatch", "-case", str(case_root), "-overwrite"), log_name,
                       IssueStage.PATCH_CREATION)


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
