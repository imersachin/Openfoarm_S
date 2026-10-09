"""OpenFOAM dictionaries for the domain meshes (docs/rotating_machinery.md
sections 5 and 11; G0 R1 and R2, as run on v2512).

- BOX: one blockMesh block; each face is its patch.
- CYLINDER and each IMPORTED part: a background block cut by snappyHexMesh
  to a closed surface (section 18, decision 1). The surface is written as one
  ASCII STL whose regions are already named after their patches, so snappy
  renames nothing; feature edges are extracted and snapped explicitly.

Rotating zones are meshed from G3; here every domain mesh stands alone, and
a joint region is meshed as a plain patch named after the region (an AMI pair
from G5). Identical input gives byte-identical files.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import trimesh

from geometry.transformer import stl_ascii_bytes
from machines.config import (
    BOX_FACES,
    BoxDomain,
    CylinderDomain,
    ImportedDomain,
    MachineProjectConfig,
    SourceKind,
)
from machines.domains import (
    BACKGROUND_PATCH,
    SurfaceInfo,
    box_grid,
    cylinder_grid,
    cylinder_surface,
    surface_grid,
)
from machines.patches import openfoam_type
from openfoam.dictionary import OpenFOAMFileWriter, foam_header, foam_scalar, foam_vector
from vawt.case_generator import (
    _FACES,
    Grid,
    _block_mesh_dict,
    _control_dict,
    _fv_schemes,
    _fv_solution,
)
from vawt.config import Axis, Vec3

CASES_DIR = "cases"
DOMAIN_CASE = "domain"  # BOX and CYLINDER; IMPORTED parts use domain_<part>
SURFACE_DIR = "constant/triSurface"
FEATURE_INCLUDED_ANGLE = 150  # as G0 R1 and R2 (feature angle 30 deg)


@dataclass(frozen=True)
class DomainCase:
    """One domain mesh: its files, relative to cases/<name>/."""

    name: str
    owner: str  # "domain" or "domain.parts.<i>"
    dictionaries: dict[str, str]
    surfaces: dict[str, bytes] = field(default_factory=dict)
    # Patches the mesh must have, with faces: name -> OpenFOAM type.
    expected: dict[str, str] = field(default_factory=dict)
    # Patches the mesh may also have (unassigned regions, joint regions).
    other: tuple[str, ...] = ()
    cut: bool = False  # snappyHexMesh runs (background patch must end up empty)
    # Patches of imported regions found in no surface (a binary file, a wrong
    # name): expected in this mesh so that their absence is reported.
    unplaced: tuple[str, ...] = ()

    @property
    def root(self) -> str:
        return f"{CASES_DIR}/{self.name}"


def part_case_name(part: str) -> str:
    return f"{DOMAIN_CASE}_{part}"


# --- dictionaries -------------------------------------------------------------------------

def _mesh_quality_dict(config: MachineProjectConfig) -> str:
    # The VAWT meshQualityDict (vawt.case_generator), from snappy_quality.
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


def _common(config: MachineProjectConfig) -> dict[str, str]:
    return {"system/controlDict": _control_dict(), "system/fvSchemes": _fv_schemes(),
            "system/fvSolution": _fv_solution(),
            "system/meshQualityDict": _mesh_quality_dict(config)}


def _background_boundary() -> str:
    faces = " ".join(pair for side in _FACES.values() for pair in side)
    return f"    {BACKGROUND_PATCH} {{ type patch; faces ({faces}); }}"


def _feature_extract_dict(surface_file: str) -> str:
    return foam_header("surfaceFeatureExtractDict") + f"""
{surface_file}
{{
    extractionMethod    extractFromSurface;

    extractFromSurfaceCoeffs
    {{
        includedAngle   {FEATURE_INCLUDED_ANGLE};
    }}

    subsetFeatures
    {{
        nonManifoldEdges    no;
        openEdges           yes;
    }}

    writeObj            no;
}}
"""


def _cut_snappy_dict(config: MachineProjectConfig, surface: str, regions: dict[str, str],
                     location: Vec3) -> str:
    """snappyHexMeshDict cutting the background to `surface` (G0 R1b, R2a)."""
    stem = surface
    renames = "\n".join(f"            {r} {{ name {r}; }}" for r in regions)
    types = "\n".join(f"                {r} {{ level (0 0); patchInfo {{ type {t}; }} }}"
                      for r, t in regions.items())
    cells = config.max_global_cells
    return foam_header("snappyHexMeshDict") + f"""
castellatedMesh true;
snap            true;
addLayers       false;

geometry
{{
    {stem}.stl
    {{
        type triSurfaceMesh;
        name {stem};
        regions
        {{
{renames}
        }}
    }}
}}

castellatedMeshControls
{{
    maxLocalCells       {cells};
    maxGlobalCells      {cells};
    minRefinementCells  0;
    nCellsBetweenLevels 2;

    features
    (
        {{
            file "{stem}.eMesh";
            level 0;
        }}
    );

    refinementSurfaces
    {{
        {stem}
        {{
            level (0 0);
            patchInfo {{ type patch; }}
            regions
            {{
{types}
            }}
        }}
    }}

    refinementRegions {{}}

    resolveFeatureAngle 30;
    locationInMesh      {foam_vector(location.x, location.y, location.z)};
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
    explicitFeatureSnap true;
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes       true;
    expansionRatio      1.2;
    finalLayerThickness 0.3;
    minThickness        0.1;
    nGrow               0;
    featureAngle        30;
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
    {{}}
}}

meshQualityControls
{{
    #include "meshQualityDict"
}}

mergeTolerance 1e-6;
"""


def _stl(regions: Sequence[tuple[str, trimesh.Trimesh]]) -> bytes:
    """One ASCII STL, one `solid` per region, in the given order."""
    return b"".join(stl_ascii_bytes(mesh, name) for name, mesh in regions)


# --- cases --------------------------------------------------------------------------------------

def _box_case(config: MachineProjectConfig, domain: BoxDomain) -> DomainCase:
    rows, expected, other = [], {}, []
    for face in BOX_FACES:
        axis, side = Axis(face[0]), 1 if face.endswith("max") else 0
        patch = config.patch_for(SourceKind.DOMAIN_FACE, face)
        name, kind = (patch.name, openfoam_type(patch.type)) if patch else (face, "patch")
        rows.append(f"    {name} {{ type {kind}; faces ({_FACES[axis][side]}); }}")
        if patch:
            expected[name] = kind
        else:
            other.append(name)
    dictionaries = {**_common(config),
                    "system/blockMeshDict": _block_mesh_dict(box_grid(domain), "\n".join(rows))}
    return DomainCase(DOMAIN_CASE, "domain", dictionaries, expected=expected,
                      other=tuple(other))


def _cut_case(config: MachineProjectConfig, name: str, owner: str, surface: str,
              regions: Sequence[tuple[str, trimesh.Trimesh]], source: SourceKind,
              grid: Grid, location: Vec3) -> DomainCase:
    named: list[tuple[str, trimesh.Trimesh]] = []
    types: dict[str, str] = {}
    expected: dict[str, str] = {}
    other: list[str] = []
    for region, mesh in regions:
        patch = config.patch_for(source, region)
        patch_name = patch.name if patch else region
        named.append((patch_name, mesh))
        types[patch_name] = openfoam_type(patch.type) if patch else "patch"
        if patch:
            expected[patch_name] = types[patch_name]
        else:
            other.append(patch_name)
    file_name = f"{surface}.stl"
    dictionaries = {
        **_common(config),
        "system/blockMeshDict": _block_mesh_dict(grid, _background_boundary()),
        "system/surfaceFeatureExtractDict": _feature_extract_dict(file_name),
        "system/snappyHexMeshDict": _cut_snappy_dict(config, surface, types, location),
    }
    return DomainCase(name, owner, dictionaries, {f"{SURFACE_DIR}/{file_name}": _stl(named)},
                      expected, tuple(other), cut=True)


class DomainCaseGenerator:
    """Deterministic domain-mesh cases. Imported domains need their surfaces as
    read by machines.domains.read_imported."""

    def render(self, config: MachineProjectConfig,
               surfaces: Sequence[SurfaceInfo] = ()) -> tuple[DomainCase, ...]:
        domain = config.domain
        if isinstance(domain, BoxDomain):
            return (_box_case(config, domain),)
        if isinstance(domain, CylinderDomain):
            return (_cut_case(config, DOMAIN_CASE, "domain", DOMAIN_CASE,
                              cylinder_surface(domain), SourceKind.DOMAIN_FACE,
                              cylinder_grid(domain), domain.location_in_mesh),)
        if isinstance(domain, ImportedDomain):
            read = {s.owner: s for s in surfaces}
            regions = {r.name for s in surfaces for r in s.regions}
            unplaced = tuple(p.name for p in config.patches if p.source.kind is SourceKind.REGION
                             and p.source.ref not in regions)
            cases = []
            for i, part in enumerate(domain.parts):
                info = read.get(f"domain.parts.{i}")
                grid = surface_grid(info, part.cell_size) if info else None
                if info is None or grid is None:
                    raise ValueError(f"Imported part '{part.name}' has not been read.")
                cases.append(_cut_case(
                    config, part_case_name(part.name), info.owner, part.name,
                    [(r.name, r.mesh) for r in info.regions], SourceKind.REGION, grid,
                    part.location_in_mesh))
            if cases and unplaced:  # reported once, with the first part
                cases[0] = replace(cases[0], unplaced=unplaced)
            return tuple(cases)
        return ()

    @staticmethod
    def write(project_root: Path, cases: Sequence[DomainCase]) -> tuple[str, ...]:
        """Write every case below project_root; returns the paths that changed."""
        written = []
        for case in cases:
            for relative, body in case.dictionaries.items():
                if OpenFOAMFileWriter.write_if_changed(project_root / case.root / relative,
                                                       body):
                    written.append(f"{case.root}/{relative}")
            for relative, data in case.surfaces.items():
                path = project_root / case.root / relative
                if path.is_file() and path.read_bytes() == data:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(f"{path.suffix}.tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
                written.append(f"{case.root}/{relative}")
        return tuple(written)
