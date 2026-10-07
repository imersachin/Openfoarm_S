from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from core.config.models import ProjectConfig
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from openfoam.commands import supports_feature_extraction
from openfoam.dictionary import OpenFOAMFileWriter, foam_scalar, foam_vector

FEATURE_DICT = "system/surfaceFeatureExtractDict"


@dataclass(frozen=True)
class GeneratedFiles:
    control_dict_changed: bool
    block_mesh_changed: bool
    feature_dict_changed: bool
    snappy_changed: bool
    mesh_quality_changed: bool
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class BackgroundCells:
    requested: tuple[int, int, int]
    effective: tuple[int, int, int]

    @property
    def capped(self) -> bool:
        return self.requested != self.effective


def _header(object_name: str) -> str:
    return f"""\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      {object_name};
}}
"""


class OpenFOAMMeshCaseGenerator:
    """Deterministic dictionary generation for the openfoam.com (ESI) profile.

    All meshing dictionaries are written under system/. Identical
    configuration produces byte-identical files.
    """

    def generate(self, project_root: Path, config: ProjectConfig) -> GeneratedFiles:
        issues: list[Issue] = []
        geometry_name = f"{config.geometry.patch_name}.stl"

        cells = self.background_cells(config)
        if cells.capped:
            issues.append(self._capped_issue(config, cells))

        feature_dict_path = project_root / FEATURE_DICT
        feature_changed = False
        if config.mesh.surface.extract_features:
            if supports_feature_extraction(config.openfoam_profile):
                feature_changed = OpenFOAMFileWriter.write_if_changed(
                    feature_dict_path, self._feature_extract_dict(config, geometry_name)
                )
            else:
                issues.append(self._unsupported_feature_profile_issue(config))
        else:
            # Never leave a stale feature dictionary behind when not required.
            feature_changed = OpenFOAMFileWriter.remove_if_generated(feature_dict_path)

        return GeneratedFiles(
            control_dict_changed=OpenFOAMFileWriter.write_if_changed(
                project_root / "system/controlDict", self._control_dict()
            ),
            block_mesh_changed=OpenFOAMFileWriter.write_if_changed(
                project_root / "system/blockMeshDict", self._block_mesh_dict(config, cells)
            ),
            feature_dict_changed=feature_changed,
            snappy_changed=OpenFOAMFileWriter.write_if_changed(
                project_root / "system/snappyHexMeshDict",
                self._snappy_dict(config, geometry_name),
            ),
            mesh_quality_changed=OpenFOAMFileWriter.write_if_changed(
                project_root / "system/meshQualityDict", self._mesh_quality_dict(config)
            ),
            issues=tuple(issues),
        )

    @staticmethod
    def background_cells(config: ProjectConfig) -> BackgroundCells:
        background = config.mesh.background
        lengths = background.domain.lengths
        def cells(length: float) -> int:
            return max(1, math.ceil(length / background.base_cell_size))

        def capped(n: int) -> int:
            return min(background.max_cells_per_axis, n)

        requested = (cells(lengths.x), cells(lengths.y), cells(lengths.z))
        effective = (capped(requested[0]), capped(requested[1]), capped(requested[2]))
        return BackgroundCells(requested=requested, effective=effective)

    @staticmethod
    def _capped_issue(config: ProjectConfig, cells: BackgroundCells) -> Issue:
        lengths = config.mesh.background.domain.lengths
        axes = {}
        for axis, length, requested, effective in zip(
            "xyz", (lengths.x, lengths.y, lengths.z), cells.requested, cells.effective,
            strict=True,
        ):
            if requested != effective:
                axes[axis] = {
                    "requested_cells": requested,
                    "effective_cells": effective,
                    "effective_cell_size": length / effective,
                }
        return Issue(
            category=IssueCategory.CONFIGURATION,
            severity=IssueSeverity.WARNING,
            stage=IssueStage.CASE_GENERATION,
            code="BACKGROUND_CELLS_CAPPED",
            message="Background cells per axis were limited by max_cells_per_axis on axes "
            f"{', '.join(axes)}; the effective cell size is larger than base_cell_size.",
            explanation="The requested base_cell_size would exceed the per-axis cell limit.",
            suggested_action="Increase base_cell_size or raise max_cells_per_axis "
            "if the finer background resolution is intended.",
            artifact_reference="system/blockMeshDict",
            details={"base_cell_size": config.mesh.background.base_cell_size, "axes": axes},
        )

    @staticmethod
    def _unsupported_feature_profile_issue(config: ProjectConfig) -> Issue:
        return Issue(
            category=IssueCategory.OPENFOAM_ENVIRONMENT,
            severity=IssueSeverity.BLOCKING,
            stage=IssueStage.CASE_GENERATION,
            code="FEATURE_EXTRACTION_UNSUPPORTED_PROFILE",
            message="Feature extraction is enabled, but feature-extraction dictionaries are "
            f"not supported for the '{config.openfoam_profile.value}' profile.",
            explanation="Only the openfoam.com (ESI) surfaceFeatureExtract workflow is "
            "implemented; openfoam.org uses a different utility and dictionary.",
            suggested_action="Disable mesh.surface.extract_features or use the "
            "openfoam_com profile.",
            details={"openfoam_profile": config.openfoam_profile.value},
        )

    @staticmethod
    def _control_dict() -> str:
        # Meshing utilities construct a Time object from system/controlDict.
        # These are standard time/write controls; no solver is configured.
        return _header("controlDict") + """
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
purgeWrite      0;
writeFormat     ascii;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable false;
"""

    @staticmethod
    def _block_mesh_dict(config: ProjectConfig, cells: BackgroundCells) -> str:
        bounds = config.mesh.background.domain
        lo, hi = bounds.minimum, bounds.maximum
        nx, ny, nz = cells.effective
        grading = foam_scalar(config.mesh.background.expansion_ratio)

        vertices = "\n".join(
            f"    {foam_vector(x, y, z)}"
            for x, y, z in (
                (lo.x, lo.y, lo.z), (hi.x, lo.y, lo.z), (hi.x, hi.y, lo.z), (lo.x, hi.y, lo.z),
                (lo.x, lo.y, hi.z), (hi.x, lo.y, hi.z), (hi.x, hi.y, hi.z), (lo.x, hi.y, hi.z),
            )
        )

        return _header("blockMeshDict") + f"""
convertToMeters 1;

vertices
(
{vertices}
);

blocks
(
    hex (0 1 2 3 4 5 6 7)
    ({nx} {ny} {nz})
    simpleGrading ({grading} {grading} {grading})
);

edges ();

boundary
(
    xmin {{ type patch; faces ((0 4 7 3)); }}
    xmax {{ type patch; faces ((1 2 6 5)); }}
    ymin {{ type patch; faces ((0 1 5 4)); }}
    ymax {{ type patch; faces ((3 7 6 2)); }}
    zmin {{ type patch; faces ((0 3 2 1)); }}
    zmax {{ type patch; faces ((4 5 6 7)); }}
);

mergePatchPairs ();
"""

    @staticmethod
    def _feature_extract_dict(config: ProjectConfig, geometry_file: str) -> str:
        # includedAngle: edges whose adjacent faces meet at less than this angle
        # are features. resolveFeatureAngle is the angle between face normals,
        # so includedAngle = 180 - feature_angle_deg (tutorial pairing 150/30).
        included_angle = foam_scalar(180.0 - config.mesh.surface.feature_angle_deg)
        return _header("surfaceFeatureExtractDict") + f"""
{geometry_file}
{{
    extractionMethod    extractFromSurface;

    extractFromSurfaceCoeffs
    {{
        includedAngle   {included_angle};
    }}

    subsetFeatures
    {{
        nonManifoldEdges    no;
        openEdges           yes;
    }}

    writeObj            no;
}}
"""

    @staticmethod
    def _snappy_dict(config: ProjectConfig, geometry_file: str) -> str:
        mesh = config.mesh
        surface = mesh.surface
        layers = mesh.layers
        patch = config.geometry.patch_name
        feature_file = f"{patch}.eMesh"
        feature_angle = foam_scalar(surface.feature_angle_deg)

        layers_section = (
            f"""
        {patch}
        {{
            nSurfaceLayers {layers.number_of_layers};
        }}"""
            if layers.enabled
            else ""
        )

        # The .eMesh only exists when feature extraction is part of the plan;
        # never reference it otherwise.
        features_section = (
            f"""
        {{
            file "{feature_file}";
            level {surface.feature_refinement_level};
        }}"""
            if surface.extract_features
            else ""
        )
        explicit_feature_snap = "true" if surface.extract_features else "false"

        return _header("snappyHexMeshDict") + f"""
castellatedMesh true;
snap            true;
addLayers       {"true" if layers.enabled else "false"};

geometry
{{
    {geometry_file}
    {{
        type triSurfaceMesh;
        name {patch};
    }}
}}

castellatedMeshControls
{{
    maxLocalCells       {mesh.max_global_cells};
    maxGlobalCells      {mesh.max_global_cells};
    minRefinementCells  0;
    nCellsBetweenLevels 2;

    features
    ({features_section}
    );

    refinementSurfaces
    {{
        {patch}
        {{
            level ({surface.minimum_level} {surface.maximum_level});
            patchInfo {{ type wall; }}
        }}
    }}

    refinementRegions
    {{
    }}

    resolveFeatureAngle {feature_angle};
    locationInMesh      {mesh.location_in_mesh.as_openfoam()};
    allowFreeStandingZoneFaces false;
}}

snapControls
{{
    nSmoothPatch        3;
    tolerance           2.0;
    nSolveIter          30;
    nRelaxIter          5;
    nFeatureSnapIter    10;
    implicitFeatureSnap false;
    explicitFeatureSnap {explicit_feature_snap};
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes       true;
    expansionRatio      {foam_scalar(layers.expansion_ratio)};
    finalLayerThickness {foam_scalar(layers.final_layer_thickness)};
    minThickness        {foam_scalar(layers.min_thickness)};
    nGrow               0;
    featureAngle        {feature_angle};
    nRelaxIter          5;
    nSmoothSurfaceNormals 1;
    nSmoothNormals      3;
    nSmoothThickness    10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedialAxisAngle  90;
    nBufferCellsNoExtrude 0;
    nLayerIter          50;

    layers
    {{{layers_section}
    }}
}}

meshQualityControls
{{
    #include "meshQualityDict"
}}

mergeTolerance 1e-6;
"""

    @staticmethod
    def _mesh_quality_dict(config: ProjectConfig) -> str:
        q = config.mesh.quality
        return f"""\
maxNonOrtho             {foam_scalar(q.max_non_orthogonality)};
maxBoundarySkewness     {foam_scalar(q.max_boundary_skewness)};
maxInternalSkewness     {foam_scalar(q.max_internal_skewness)};
maxConcave              80;
minVol                  {foam_scalar(q.min_volume)};
minTetQuality           1e-15;
minArea                 -1;
minTwist                0.02;
minDeterminant          {foam_scalar(q.min_determinant)};
minFaceWeight           0.05;
minVolRatio             0.01;
minTriangleTwist        -1;
nSmoothScale            4;
errorReduction          0.75;
"""
