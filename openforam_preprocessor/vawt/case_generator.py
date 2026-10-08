"""OpenFOAM dictionaries for every VAWT sub-case (spec sections 5, 9 and 10).

Method A (docs/vawt_method_notes.md section 4), as run in V0 on OpenFOAM v2512:

- AMI: two meshes. cases/outer (domain, cylinder removed) and cases/rotor
  (blades inside the cylinder, cell zone from topoSet) are merged into
  cases/merged, where createPatch turns the two cylinder patches into a
  cyclicAMI pair. Evidence: tests/fixtures/vawt/v0/E1_ami_two_mesh.
- CELL_ZONE with a domain: one snappyHexMesh pass in cases/merged with the
  cylinder as a zoned surface. Evidence: tests/fixtures/vawt/v0/E2_single_mesh.
- CELL_ZONE without a domain: cases/rotor only (the AMI rotor sub-case).

The final mesh is cases/merged when there is a domain, cases/rotor otherwise.
Each dictionary depends only on the settings its sub-case meshes with, so the
outer and rotor meshes can be cached independently (spec section 9.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from core.config.models import OpenFOAMProfile, Vector3
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from openfoam.dictionary import OpenFOAMFileWriter, foam_header, foam_scalar, foam_vector
from vawt.config import Axis, InterfaceType, LayerSizing, VawtProjectConfig, plane_axes

CASES_DIR = "cases"
OUTER, ROTOR, MERGED = "outer", "rotor", "merged"
SUB_CASES = (OUTER, ROTOR, MERGED)

INTERFACE_OUTER = "AMI_outer"  # cylinder patch of the outer mesh
INTERFACE_ROTOR = "AMI_rotor"  # cylinder patch of the rotor mesh
AMI_PATCHES = ("AMI1", "AMI2")  # the cyclicAMI pair after createPatch
ZONE_SURFACE = "rotatingZone"  # cylinder surface; face zone in the single mesh
CELL_ZONE_NAME = "rotating"
ROTOR_BACKGROUND = "rotorBackground"  # rotor block patch; snappyHexMesh removes it
WAKE_REGION = "wake"
# Names the generated cases use; user patch names must not collide with them.
RESERVED_NAMES = frozenset({
    INTERFACE_OUTER, INTERFACE_ROTOR, *AMI_PATCHES, ZONE_SURFACE, CELL_ZONE_NAME,
    ROTOR_BACKGROUND, WAKE_REGION,
})

ROTOR_BLOCK_MARGIN_CELLS = 2  # rotor background extends this far past the cylinder
_CEIL_TOLERANCE = 1e-9  # 10.4 / (1.04 / 9) is 90.00000000000001, not 91 cells

# Fixed addLayersControls for meshes without layers (the outer mesh): the
# values are never used, and they must not depend on the layer settings.
_NO_LAYER_CONTROLS = {"expansion_ratio": 1.2, "final": 0.3, "min": 0.1}


def cells_along(length: float, cell_size: float) -> int:
    """Number of cells so that each is no larger than cell_size."""
    return max(1, math.ceil(length / cell_size * (1.0 - _CEIL_TOLERANCE)))


@dataclass(frozen=True)
class Grid:
    """A blockMesh block: global min/max corners and cells per x, y, z."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    cells: tuple[int, int, int]

    def spacing(self, index: int) -> float:
        return (self.maximum[index] - self.minimum[index]) / self.cells[index]


@dataclass(frozen=True)
class CaseLayout:
    """Which sub-cases a configuration uses, in run order, and where the final mesh is."""

    sub_cases: tuple[str, ...]
    final: str

    @property
    def single_mesh(self) -> bool:
        return self.sub_cases == (MERGED,)


@dataclass(frozen=True)
class GeneratedCases:
    layout: CaseLayout | None
    written: tuple[str, ...]  # project-relative paths whose content changed
    removed: tuple[str, ...]  # stale generated files that were deleted
    issues: tuple[Issue, ...]


def case_layout(config: VawtProjectConfig) -> CaseLayout:
    if config.domain is None:
        return CaseLayout((ROTOR,), ROTOR)
    if config.rotating_zone.interface is InterfaceType.AMI:
        return CaseLayout((OUTER, ROTOR, MERGED), MERGED)
    return CaseLayout((MERGED,), MERGED)


def single_mesh_level_offset(config: VawtProjectConfig) -> int:
    """Refinement level that brings domain cells to the rotating-zone cell size.

    A single mesh can only have domain cell size / 2^n; the zone gets the
    smallest n whose cells are no larger than rotating_zone.cell_size.
    """
    assert config.domain is not None
    ratio = config.domain.cell_size / config.rotating_zone.cell_size
    return max(0, math.ceil(math.log2(ratio) * (1.0 - _CEIL_TOLERANCE)))


def zone_cell_size(config: VawtProjectConfig) -> float:
    """Cell size the rotating zone actually gets (before blade refinement)."""
    if case_layout(config).single_mesh:
        assert config.domain is not None
        return config.domain.cell_size / 2 ** single_mesh_level_offset(config)
    return config.rotating_zone.cell_size


def _point(axis: Axis, u: float, v: float, along: float) -> tuple[float, float, float]:
    p = [0.0, 0.0, 0.0]
    pu, pv = plane_axes(axis)
    p[pu.position], p[pv.position], p[axis.position] = u, v, along
    return (p[0], p[1], p[2])


def domain_grid(config: VawtProjectConfig) -> Grid:
    assert config.domain is not None
    lo, hi = config.domain.bounds.minimum, config.domain.bounds.maximum
    size = config.domain.cell_size
    return Grid(
        (lo.x, lo.y, lo.z), (hi.x, hi.y, hi.z),
        (cells_along(hi.x - lo.x, size), cells_along(hi.y - lo.y, size),
         cells_along(hi.z - lo.z, size)),
    )


def rotor_grid(config: VawtProjectConfig) -> Grid:
    """Rotor background: the cylinder plus a margin, with exactly the zone cell size."""
    zone, axis = config.rotating_zone, config.rotor.axis
    size = zone.cell_size
    across = cells_along(zone.diameter, size) + 2 * ROTOR_BLOCK_MARGIN_CELLS
    along = cells_along(zone.axis_max - zone.axis_min, size) + 2 * ROTOR_BLOCK_MARGIN_CELLS
    half_across, half_along = across * size / 2.0, along * size / 2.0
    middle = (zone.axis_min + zone.axis_max) / 2.0
    lo = _point(axis, zone.centre_u - half_across, zone.centre_v - half_across,
                middle - half_along)
    hi = _point(axis, zone.centre_u + half_across, zone.centre_v + half_across,
                middle + half_along)
    cells = [0, 0, 0]
    pu, pv = plane_axes(axis)
    cells[pu.position] = cells[pv.position] = across
    cells[axis.position] = along
    return Grid(lo, hi, (cells[0], cells[1], cells[2]))


def background_grids(config: VawtProjectConfig) -> dict[str, Grid]:
    """The blockMesh grid of each sub-case that runs blockMesh."""
    layout = case_layout(config)
    grids: dict[str, Grid] = {}
    if OUTER in layout.sub_cases or layout.single_mesh:
        grids[OUTER if OUTER in layout.sub_cases else MERGED] = domain_grid(config)
    if ROTOR in layout.sub_cases:
        grids[ROTOR] = rotor_grid(config)
    return grids


# --- dictionary text ---------------------------------------------------------------

def _vec(p: tuple[float, float, float]) -> str:
    return foam_vector(*p)


def _control_dict() -> str:
    # Meshing utilities construct a Time object from it; no solver is configured.
    # writePrecision 8 as in the V0 runs.
    return foam_header("controlDict") + """
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
purgeWrite      0;
writeFormat     ascii;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable false;
"""


def _fv_schemes() -> str:
    # snappyHexMesh (v2512) requires the file; meshing reads no schemes from it.
    return foam_header("fvSchemes") + """
ddtSchemes {}
gradSchemes {}
divSchemes {}
laplacianSchemes {}
interpolationSchemes {}
snGradSchemes {}
"""


def _fv_solution() -> str:
    return foam_header("fvSolution")  # required alongside fvSchemes; meshing only


def _mesh_quality_dict(config: VawtProjectConfig) -> str:
    # FoamFile header: checkMesh -meshQuality reads this file on its own.
    q = config.snappy_quality
    return foam_header("meshQualityDict") + f"""
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


_FACES = {  # blockMesh face of each global axis: (minimum side, maximum side)
    Axis.X: ("(0 4 7 3)", "(1 2 6 5)"),
    Axis.Y: ("(0 1 5 4)", "(3 7 6 2)"),
    Axis.Z: ("(0 3 2 1)", "(4 5 6 7)"),
}


def _block_mesh_dict(grid: Grid, boundary: str) -> str:
    (x0, y0, z0), (x1, y1, z1) = grid.minimum, grid.maximum
    vertices = "\n".join(f"    {foam_vector(x, y, z)}" for x, y, z in (
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ))
    nx, ny, nz = grid.cells
    return foam_header("blockMeshDict") + f"""
convertToMeters 1;

vertices
(
{vertices}
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)
);

edges ();

boundary
(
{boundary}
);

mergePatchPairs ();
"""


def _domain_boundary(config: VawtProjectConfig) -> str:
    assert config.domain is not None
    names = config.domain.patches
    axis, flow = config.rotor.axis, config.rotor.flow_axis
    lateral = config.rotor.lateral_axis
    rows = (
        (names.inlet, _FACES[flow][0]), (names.outlet, _FACES[flow][1]),
        (names.lateral_min, _FACES[lateral][0]), (names.lateral_max, _FACES[lateral][1]),
        (names.axial_min, _FACES[axis][0]), (names.axial_max, _FACES[axis][1]),
    )
    return "\n".join(f"    {name} {{ type patch; faces ({faces}); }}" for name, faces in rows)


def _rotor_boundary() -> str:
    faces = " ".join(pair for side in _FACES.values() for pair in side)
    return f"    {ROTOR_BACKGROUND} {{ type patch; faces ({faces}); }}"


def _cylinder(config: VawtProjectConfig, name: str) -> str:
    zone, axis = config.rotating_zone, config.rotor.axis
    p1 = _point(axis, zone.centre_u, zone.centre_v, zone.axis_min)
    p2 = _point(axis, zone.centre_u, zone.centre_v, zone.axis_max)
    return f"""    {name}
    {{
        type    searchableCylinder;
        point1  {_vec(p1)};
        point2  {_vec(p2)};
        radius  {foam_scalar(zone.diameter / 2.0)};
    }}"""


def _wake_box(config: VawtProjectConfig) -> str | None:
    wake = config.refinement.wake
    if wake is None:
        return None
    lo, hi = wake.box.minimum, wake.box.maximum
    return f"""    {WAKE_REGION}
    {{
        type    searchableBox;
        min     {foam_vector(lo.x, lo.y, lo.z)};
        max     {foam_vector(hi.x, hi.y, hi.z)};
    }}"""


def _rotor_surface(config: VawtProjectConfig) -> str:
    patch = config.geometry.patch_name
    return f"""    {patch}.stl
    {{
        type triSurfaceMesh;
        name {patch};
    }}"""


def _feature_extract_dict(config: VawtProjectConfig) -> str:
    # Same form as mesh/generator.py; includedAngle = 180 - feature angle.
    included = foam_scalar(180.0 - config.refinement.feature_angle_deg)
    return foam_header("surfaceFeatureExtractDict") + f"""
{config.geometry.patch_name}.stl
{{
    extractionMethod    extractFromSurface;

    extractFromSurfaceCoeffs
    {{
        includedAngle   {included};
    }}

    subsetFeatures
    {{
        nonManifoldEdges    no;
        openEdges           yes;
    }}

    writeObj            no;
}}
"""


def _layer_controls(config: VawtProjectConfig, with_layers: bool, feature_angle: float) -> str:
    layers = config.layers
    if with_layers and layers.sizing is LayerSizing.ABSOLUTE:
        assert layers.first_layer_thickness is not None
        assert layers.min_thickness_m is not None
        # V0 E3: firstLayerThickness is required with relativeSizes false.
        sizing = f"""    relativeSizes       false;
    thicknessModel      firstAndExpansion;
    firstLayerThickness {foam_scalar(layers.first_layer_thickness)};
    expansionRatio      {foam_scalar(layers.expansion_ratio)};
    minThickness        {foam_scalar(layers.min_thickness_m)};"""
    elif with_layers:
        sizing = f"""    relativeSizes       true;
    expansionRatio      {foam_scalar(layers.expansion_ratio)};
    finalLayerThickness {foam_scalar(layers.final_layer_thickness)};
    minThickness        {foam_scalar(layers.min_thickness)};"""
    else:
        fixed = _NO_LAYER_CONTROLS
        sizing = f"""    relativeSizes       true;
    expansionRatio      {foam_scalar(fixed["expansion_ratio"])};
    finalLayerThickness {foam_scalar(fixed["final"])};
    minThickness        {foam_scalar(fixed["min"])};"""
    per_patch = (
        f"\n        {config.geometry.patch_name} {{ nSurfaceLayers {layers.count}; }}\n    "
        if with_layers else ""
    )
    return f"""addLayersControls
{{
{sizing}
    nGrow               0;
    featureAngle        {foam_scalar(feature_angle)};
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
    {{{per_patch}}}
}}"""


def _snappy_dict(config: VawtProjectConfig, *, geometry: list[str], features: str,
                 surfaces: str, regions: str, location: tuple[float, float, float],
                 explicit_features: bool, with_layers: bool,
                 feature_angle: float) -> str:
    cells = config.max_global_cells
    return foam_header("snappyHexMeshDict") + f"""
castellatedMesh true;
snap            true;
addLayers       {"true" if with_layers else "false"};

geometry
{{
{chr(10).join(geometry)}
}}

castellatedMeshControls
{{
    maxLocalCells       {cells};
    maxGlobalCells      {cells};
    minRefinementCells  0;
    nCellsBetweenLevels 2;

    features
    ({features}
    );

    refinementSurfaces
    {{
{surfaces}
    }}

    refinementRegions
    {{
{regions}
    }}

    resolveFeatureAngle {foam_scalar(feature_angle)};
    locationInMesh      {_vec(location)};
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
    explicitFeatureSnap {"true" if explicit_features else "false"};
    multiRegionFeatureSnap false;
}}

{_layer_controls(config, with_layers, feature_angle)}

meshQualityControls
{{
    #include "meshQualityDict"
}}

mergeTolerance 1e-6;
"""


def _features(config: VawtProjectConfig, offset: int) -> str:
    if not config.refinement.extract_features:
        return ""
    level = config.refinement.feature_level + offset
    return f"""
        {{
            file "{config.geometry.patch_name}.eMesh";
            level {level};
        }}"""


def _rotor_entry(config: VawtProjectConfig, offset: int) -> str:
    r = config.refinement
    return f"""        {config.geometry.patch_name}
        {{
            level ({r.blade_min_level + offset} {r.blade_max_level + offset});
            patchInfo {{ type wall; }}
        }}"""


def _location(vector: Vector3) -> tuple[float, float, float]:
    return (vector.x, vector.y, vector.z)


def _outer_snappy(config: VawtProjectConfig) -> str:
    """Domain with the cylinder removed: the mesh point is outside the cylinder (Q3)."""
    assert config.domain is not None
    level = config.refinement.interface_level
    geometry = [_cylinder(config, INTERFACE_OUTER)]
    regions = ""
    wake = _wake_box(config)
    if wake is not None:
        assert config.refinement.wake is not None
        geometry.append(wake)
        regions = (f"        {WAKE_REGION} {{ mode inside; levels ((1e15 "
                   f"{config.refinement.wake.level})); }}")
    surfaces = f"""        {INTERFACE_OUTER}
        {{
            level ({level} {level});
            patchInfo {{ type patch; }}
        }}"""
    # The outer mesh has no feature edges or layers; fixed values keep this
    # dictionary independent of the rotor settings (spec 9.2).
    return _snappy_dict(config, geometry=geometry, features="", surfaces=surfaces,
                        regions=regions, location=_location(config.domain.location_in_mesh),
                        explicit_features=False, with_layers=False, feature_angle=30.0)


def _rotor_snappy(config: VawtProjectConfig, boundary_name: str) -> str:
    """Rotating zone: blades and the cylinder; the mesh point is inside the cylinder."""
    surfaces = _rotor_entry(config, 0) + f"""
        {boundary_name}
        {{
            level (0 0);
            patchInfo {{ type patch; }}
        }}"""
    return _snappy_dict(
        config, geometry=[_rotor_surface(config), _cylinder(config, boundary_name)],
        features=_features(config, 0), surfaces=surfaces, regions="",
        location=_location(config.rotating_zone.location_in_mesh),
        explicit_features=config.refinement.extract_features,
        with_layers=config.layers.enabled, feature_angle=config.refinement.feature_angle_deg,
    )


def _single_snappy(config: VawtProjectConfig) -> str:
    """CELL_ZONE with a domain: one pass, cylinder as a zoned surface (V0 E2a)."""
    assert config.domain is not None
    k = single_mesh_level_offset(config)
    geometry = [_rotor_surface(config), _cylinder(config, ZONE_SURFACE)]
    regions = [f"        {ZONE_SURFACE} {{ mode inside; levels ((1e15 {k})); }}"]
    wake = _wake_box(config)
    if wake is not None:
        assert config.refinement.wake is not None
        geometry.append(wake)
        regions.append(f"        {WAKE_REGION} {{ mode inside; levels ((1e15 "
                       f"{config.refinement.wake.level})); }}")
    surfaces = _rotor_entry(config, k) + f"""
        {ZONE_SURFACE}
        {{
            level           ({k} {k});
            faceZone        {ZONE_SURFACE};
            cellZone        {CELL_ZONE_NAME};
            cellZoneInside  inside;
        }}"""
    return _snappy_dict(
        config, geometry=geometry, features=_features(config, k), surfaces=surfaces,
        regions="\n".join(regions), location=_location(config.domain.location_in_mesh),
        explicit_features=config.refinement.extract_features,
        with_layers=config.layers.enabled, feature_angle=config.refinement.feature_angle_deg,
    )


def _topo_set_dict(grid: Grid) -> str:
    # Every cell of the rotor mesh is in the zone: the box is the rotor
    # background block itself (all cells lie inside it), not a geometric test.
    return foam_header("topoSetDict") + f"""
actions
(
    {{
        name    {CELL_ZONE_NAME}Cells;
        type    cellSet;
        action  new;
        source  boxToCell;
        box     {_vec(grid.minimum)} {_vec(grid.maximum)};
    }}
    {{
        name    {CELL_ZONE_NAME};
        type    cellZoneSet;
        action  new;
        source  setToCellZone;
        set     {CELL_ZONE_NAME}Cells;
    }}
);
"""


def _create_patch_dict() -> str:
    first, second = AMI_PATCHES
    def entry(name: str, neighbour: str, source: str) -> str:
        return f"""    {{
        name {name};
        patchInfo
        {{
            type            cyclicAMI;
            matchTolerance  0.0001;
            neighbourPatch  {neighbour};
            transform       noOrdering;
        }}
        constructFrom patches;
        patches ({source});
    }}"""
    return foam_header("createPatchDict") + f"""
pointSync false;

patches
(
{entry(first, second, INTERFACE_OUTER)}
{entry(second, first, INTERFACE_ROTOR)}
);
"""


def _common(config: VawtProjectConfig, sub_case: str) -> dict[str, str]:
    system = f"{CASES_DIR}/{sub_case}/system"
    return {
        f"{system}/controlDict": _control_dict(),
        f"{system}/fvSchemes": _fv_schemes(),
        f"{system}/fvSolution": _fv_solution(),
        f"{system}/meshQualityDict": _mesh_quality_dict(config),
    }


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           **details: object) -> Issue:
    return Issue(category=IssueCategory.CONFIGURATION, severity=severity,
                 stage=IssueStage.CASE_GENERATION, code=code, message=message,
                 suggested_action=action, details=dict(details))


class VawtCaseGenerator:
    """Deterministic dictionaries for each sub-case. Identical configuration
    produces byte-identical files; nothing outside cases/*/system is written."""

    def issues(self, config: VawtProjectConfig) -> tuple[Issue, ...]:
        if config.openfoam_profile is not OpenFOAMProfile.OPENCFD:
            return (_issue(
                IssueSeverity.BLOCKING, "VAWT_PROFILE_UNSUPPORTED",
                f"VAWT cases are generated for openfoam.com only, not "
                f"'{config.openfoam_profile.value}'.",
                "Select the openfoam.com profile (target version v2512).",
                openfoam_profile=config.openfoam_profile.value,
            ),)
        if config.rotating_zone.interface is InterfaceType.AMI and config.domain is None:
            return (_issue(
                IssueSeverity.BLOCKING, "AMI_REQUIRES_DOMAIN",
                "The AMI interface requires an outer domain.",
                "Enable the outer domain, or use the CELL_ZONE interface.",
            ),)
        if case_layout(config).single_mesh:
            effective = zone_cell_size(config)
            return (_issue(
                IssueSeverity.INFO, "ZONE_CELL_SIZE_ADJUSTED",
                f"In a single mesh the rotating zone gets cells of {effective:.6g} m "
                f"(domain cell size / 2^{single_mesh_level_offset(config)}); "
                f"requested {config.rotating_zone.cell_size:.6g} m.",
                "Choose the domain and zone cell sizes as a power-of-two ratio to get "
                "the requested size exactly.",
                requested=config.rotating_zone.cell_size, effective=effective,
                level_offset=single_mesh_level_offset(config),
            ),)
        return ()

    def render(self, config: VawtProjectConfig) -> dict[str, str]:
        """{project-relative path: dictionary body} for the configuration."""
        if any(i.severity is IssueSeverity.BLOCKING for i in self.issues(config)):
            return {}
        layout = case_layout(config)
        files: dict[str, str] = {}
        features = config.refinement.extract_features
        if OUTER in layout.sub_cases:
            system = f"{CASES_DIR}/{OUTER}/system"
            files.update(_common(config, OUTER))
            files[f"{system}/blockMeshDict"] = _block_mesh_dict(
                domain_grid(config), _domain_boundary(config))
            files[f"{system}/snappyHexMeshDict"] = _outer_snappy(config)
        if ROTOR in layout.sub_cases:
            system = f"{CASES_DIR}/{ROTOR}/system"
            boundary = INTERFACE_ROTOR if layout.final == MERGED else ZONE_SURFACE
            grid = rotor_grid(config)
            files.update(_common(config, ROTOR))
            files[f"{system}/blockMeshDict"] = _block_mesh_dict(grid, _rotor_boundary())
            files[f"{system}/snappyHexMeshDict"] = _rotor_snappy(config, boundary)
            files[f"{system}/topoSetDict"] = _topo_set_dict(grid)
            if features:
                files[f"{system}/surfaceFeatureExtractDict"] = _feature_extract_dict(config)
        if MERGED in layout.sub_cases:
            system = f"{CASES_DIR}/{MERGED}/system"
            files.update(_common(config, MERGED))
            if layout.single_mesh:
                files[f"{system}/blockMeshDict"] = _block_mesh_dict(
                    domain_grid(config), _domain_boundary(config))
                files[f"{system}/snappyHexMeshDict"] = _single_snappy(config)
                if features:
                    files[f"{system}/surfaceFeatureExtractDict"] = (
                        _feature_extract_dict(config))
            else:
                files[f"{system}/createPatchDict"] = _create_patch_dict()
        return dict(sorted(files.items()))

    def generate(self, project_root: Path, config: VawtProjectConfig) -> GeneratedCases:
        issues = self.issues(config)
        files = self.render(config)
        written = tuple(
            relative for relative, body in files.items()
            if OpenFOAMFileWriter.write_if_changed(project_root / relative, body)
        )
        removed = tuple(
            relative for relative in self._generated_files(project_root)
            if relative not in files
            and OpenFOAMFileWriter.remove_if_generated(project_root / relative)
        )
        layout = None if not files else case_layout(config)
        return GeneratedCases(layout, written, removed, issues)

    @staticmethod
    def _generated_files(project_root: Path) -> list[str]:
        """Existing files under cases/*/system (only generated ones are removed)."""
        found: list[str] = []
        for sub_case in SUB_CASES:
            system = project_root / CASES_DIR / sub_case / "system"
            if system.is_dir():
                found.extend(
                    path.relative_to(project_root).as_posix()
                    for path in sorted(system.iterdir()) if path.is_file()
                )
        return found
