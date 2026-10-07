from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from core.config.models import ProjectConfig
from openfoam.dictionary import OpenFOAMFileWriter


@dataclass(frozen=True)
class GeneratedFiles:
    control_dict_changed: bool
    block_mesh_changed: bool
    snappy_changed: bool
    mesh_quality_changed: bool


class OpenFOAMMeshCaseGenerator:
    def generate(self, project_root: Path, config: ProjectConfig) -> GeneratedFiles:
        geometry_name = f"{config.geometry.patch_name}.stl"

        control_changed = OpenFOAMFileWriter.write_if_changed(
            project_root / "system/controlDict",
            self._control_dict(),
        )
        block_changed = OpenFOAMFileWriter.write_if_changed(
            project_root / "system/blockMeshDict",
            self._block_mesh_dict(config),
        )
        snappy_changed = OpenFOAMFileWriter.write_if_changed(
            project_root / "system/snappyHexMeshDict",
            self._snappy_dict(config, geometry_name),
        )
        quality_changed = OpenFOAMFileWriter.write_if_changed(
            project_root / "system/meshQualityDict",
            self._mesh_quality_dict(config),
        )

        return GeneratedFiles(
            control_dict_changed=control_changed,
            block_mesh_changed=block_changed,
            snappy_changed=snappy_changed,
            mesh_quality_changed=quality_changed,
        )

    @staticmethod
    def _control_dict() -> str:
        # Meshing utilities construct a Time object from system/controlDict.
        # These are standard time/write controls; no solver is configured.
        return """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      controlDict;
}

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
    def _cells(length: float, cell_size: float, maximum: int) -> int:
        return min(maximum, max(1, math.ceil(length / cell_size)))

    def _block_mesh_dict(self, config: ProjectConfig) -> str:
        bounds = config.mesh.background.domain
        lengths = bounds.lengths
        cell_size = config.mesh.background.base_cell_size
        maximum = config.mesh.background.max_cells_per_axis

        nx = self._cells(lengths.x, cell_size, maximum)
        ny = self._cells(lengths.y, cell_size, maximum)
        nz = self._cells(lengths.z, cell_size, maximum)

        lo, hi = bounds.minimum, bounds.maximum
        grading = config.mesh.background.expansion_ratio

        return f"""\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}

convertToMeters 1;

vertices
(
    {lo.as_openfoam()}
    ({hi.x:g} {lo.y:g} {lo.z:g})
    ({hi.x:g} {hi.y:g} {lo.z:g})
    ({lo.x:g} {hi.y:g} {lo.z:g})
    ({lo.x:g} {lo.y:g} {hi.z:g})
    ({hi.x:g} {lo.y:g} {hi.z:g})
    {hi.as_openfoam()}
    ({lo.x:g} {hi.y:g} {hi.z:g})
);

blocks
(
    hex (0 1 2 3 4 5 6 7)
    ({nx} {ny} {nz})
    simpleGrading ({grading:g} {grading:g} {grading:g})
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

    def _snappy_dict(self, config: ProjectConfig, geometry_file: str) -> str:
        mesh = config.mesh
        surface = mesh.surface
        layers = mesh.layers
        feature_file = f"{config.geometry.patch_name}.eMesh"
        patch = config.geometry.patch_name

        layers_section = (
            f"""
    {patch}
    {{
        nSurfaceLayers {layers.number_of_layers};
    }}"""
            if layers.enabled
            else ""
        )

        # The .eMesh only exists when surfaceFeatureExtract is part of the plan;
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

        return f"""\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      snappyHexMeshDict;
}}

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

    resolveFeatureAngle {surface.feature_angle_deg:g};
    locationInMesh     {mesh.location_in_mesh.as_openfoam()};
    allowFreeStandingZoneFaces false;
}}

snapControls
{{
    nSmoothPatch       3;
    tolerance          2.0;
    nSolveIter         30;
    nRelaxIter         5;
    nFeatureSnapIter   10;
    implicitFeatureSnap false;
    explicitFeatureSnap {explicit_feature_snap};
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes true;
    expansionRatio {layers.expansion_ratio:g};
    finalLayerThickness {layers.final_layer_thickness:g};
    minThickness {layers.min_thickness:g};
    featureAngle {surface.feature_angle_deg:g};
    nGrow 0;
    nRelaxIter 5;
    nLayerIter 50;

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
maxNonOrtho             {q.max_non_orthogonality:g};
maxBoundarySkewness     {q.max_boundary_skewness:g};
maxInternalSkewness     {q.max_internal_skewness:g};
maxConcave              80;
minVol                  {q.min_volume:g};
minTetQuality           1e-15;
minArea                 -1;
minTwist                0.02;
minDeterminant          {q.min_determinant:g};
minFaceWeight           0.05;
minVolRatio             0.01;
minTriangleTwist        -1;
nSmoothScale            4;
errorReduction          0.75;
"""
