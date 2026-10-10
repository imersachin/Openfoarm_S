"""Domain, zone and merged cases for a whole machine (G3; decisions F1-F7).

Method A of the VAWT workflow (two meshes joined by AMI, V0 E1), generalised
to several bodies and zones, as proven in G0 (R3 HAWT, R5 pole):

- domain: the domain (G2) with every cylinder zone cut out and the
  stationary bodies, and the stationary parts of split bodies, cut in.
- zone_<name>: one per cylinder zone: the zone's background block cut to the
  zone surface, with its rotating bodies and the inner parts of its split
  bodies; topoSet makes the cell zone <name>.
- zone_<name> of an imported zone (G5; K3): the zone's own surface cut out
  of its background block, as an imported part, with its rotating bodies;
  topoSet puts every cell in the cell zone <name> (G0 R4).
- merged: the domain mesh with every zone added (mergeMeshes); createPatch
  turns each interface and each joint into a cyclicAMI pair; postProcess
  measures the AMI weights on the static mesh (section 18, decision 4).

A joint's regions are meshed as <region>_src in their own surfaces and
refined to the joint's level, the coarser side to the finer side's cell size
(K1). The VAWT workflow keeps its own generator. Identical input gives
identical files.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from geometry.metrics import contains_points
from machines.case_generator import (
    DOMAIN_CASE,
    FEATURE_INCLUDED_ANGLE,
    SURFACE_DIR,
    DomainCase,
    DomainCaseGenerator,
    _background_boundary,
    _common,
    _stl,
)
from machines.config import (
    BodyConfig,
    BoxDomain,
    CylinderDomain,
    CylinderZone,
    ImportedDomain,
    ImportedSurface,
    ImportedZone,
    LayersConfig,
    MachineProjectConfig,
    Motion,
    PatchType,
    RotatingZone,
    SourceKind,
)
from machines.domains import SurfaceInfo, surface_grid
from machines.patches import openfoam_type
from machines.zones import (
    INNER,
    SOURCE_SUFFIX,
    AmiPair,
    Interface,
    interfaces,
    joint_pairs,
    split_patch,
    zone_grid,
    zone_surface,
)
from openfoam.dictionary import foam_header, foam_scalar, foam_vector
from vawt.case_generator import Grid, _block_mesh_dict
from vawt.config import LayerSizing, Vec3, plane_axes

MERGED_CASE = "merged"
AMI_WEIGHTS_DICT = "system/amiWeightsDict"
CYCLIC_AMI = "cyclicAMI"


def zone_case_name(zone: RotatingZone) -> str:
    return f"zone_{zone.name}"


@dataclass(frozen=True)
class Surface:
    """One geometry entry of a snappyHexMeshDict."""

    stem: str  # constant/triSurface/<stem>.stl, and its snappy name
    regions: dict[str, str]  # region (= patch) name -> OpenFOAM type
    level: tuple[int, int]
    features: int | None  # feature-edge level, or None for no feature edges
    mesh: bytes  # the STL
    region_levels: dict[str, tuple[int, int]] = field(default_factory=dict)  # overrides


@dataclass(frozen=True)
class MachineCases:
    domain: tuple[DomainCase, ...]
    zones: tuple[DomainCase, ...]
    merged: DomainCase
    interfaces: tuple[Interface, ...]
    # Patch -> OpenFOAM type to set on the merged mesh (untyped meshes, H3).
    retype: dict[str, str] = field(default_factory=dict)
    joints: tuple[AmiPair, ...] = ()  # between imported surfaces (G5)

    @property
    def all(self) -> tuple[DomainCase, ...]:
        return (*self.domain, *self.zones, self.merged)

    @property
    def pairs(self) -> tuple[AmiPair, ...]:
        """Every cyclicAMI pair: the zone interfaces, then the joints."""
        return (*(face.pair for face in self.interfaces), *self.joints)


# --- dictionaries -------------------------------------------------------------------------

def _feature_extract_dict(stems: Sequence[str]) -> str:
    entries = "".join(f"""
{stem}.stl
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
""" for stem in stems)
    return foam_header("surfaceFeatureExtractDict") + entries


_NO_LAYERS = {"expansion_ratio": 1.2, "final": 0.3, "min": 0.1}  # as vawt.case_generator


def _layer_controls(layered: Sequence[tuple[str, LayersConfig]]) -> str:
    """addLayersControls: sizing from the case's layered bodies (one sizing per
    case, checked before meshing); per-patch values for each body (F7)."""
    if not layered:
        sizing = f"""    relativeSizes       true;
    expansionRatio      {foam_scalar(_NO_LAYERS["expansion_ratio"])};
    finalLayerThickness {foam_scalar(_NO_LAYERS["final"])};
    minThickness        {foam_scalar(_NO_LAYERS["min"])};"""
        per_patch = ""
    else:
        absolute = layered[0][1].sizing is LayerSizing.ABSOLUTE
        rows = []
        for patch, layers in layered:
            if absolute:
                assert layers.first_layer_thickness is not None
                assert layers.min_thickness_m is not None
                values = (f"firstLayerThickness {foam_scalar(layers.first_layer_thickness)}; "
                          f"minThickness {foam_scalar(layers.min_thickness_m)};")
            else:
                values = (f"finalLayerThickness {foam_scalar(layers.final_layer_thickness)}; "
                          f"minThickness {foam_scalar(layers.min_thickness)};")
            rows.append(f"        {patch} {{ nSurfaceLayers {layers.count}; expansionRatio "
                        f"{foam_scalar(layers.expansion_ratio)}; {values} }}")
        first = layered[0][1]
        if absolute:
            assert first.first_layer_thickness is not None and first.min_thickness_m is not None
            # V0 E3: firstLayerThickness is required with relativeSizes false.
            sizing = f"""    relativeSizes       false;
    thicknessModel      firstAndExpansion;
    firstLayerThickness {foam_scalar(first.first_layer_thickness)};
    expansionRatio      {foam_scalar(first.expansion_ratio)};
    minThickness        {foam_scalar(first.min_thickness_m)};"""
        else:
            sizing = f"""    relativeSizes       true;
    expansionRatio      {foam_scalar(first.expansion_ratio)};
    finalLayerThickness {foam_scalar(first.final_layer_thickness)};
    minThickness        {foam_scalar(first.min_thickness)};"""
        per_patch = "\n" + "\n".join(rows) + "\n    "
    return f"""addLayersControls
{{
{sizing}
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
    {{{per_patch}}}
}}"""


def _snappy_dict(config: MachineProjectConfig, surfaces: Sequence[Surface], location: Vec3,
                 layered: Sequence[tuple[str, LayersConfig]]) -> str:
    geometry, features, refinement = [], [], []
    for s in surfaces:
        renames = "\n".join(f"            {r} {{ name {r}; }}" for r in s.regions)
        geometry.append(f"""    {s.stem}.stl
    {{
        type triSurfaceMesh;
        name {s.stem};
        regions
        {{
{renames}
        }}
    }}""")
        if s.features is not None:
            features.append(f"""
        {{
            file "{s.stem}.eMesh";
            level {s.features};
        }}""")
        lo, hi = s.level
        types = "\n".join(
            f"                {r} {{ level ({s.region_levels.get(r, s.level)[0]} "
            f"{s.region_levels.get(r, s.level)[1]}); patchInfo {{ type {t}; }} }}"
            for r, t in s.regions.items())
        refinement.append(f"""        {s.stem}
        {{
            level ({lo} {hi});
            patchInfo {{ type patch; }}
            regions
            {{
{types}
            }}
        }}""")
    cells = config.max_global_cells
    return foam_header("snappyHexMeshDict") + f"""
castellatedMesh true;
snap            true;
addLayers       {"true" if layered else "false"};

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
    ({"".join(features)}
    );

    refinementSurfaces
    {{
{chr(10).join(refinement)}
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

{_layer_controls(layered)}

meshQualityControls
{{
    #include "meshQualityDict"
}}

mergeTolerance 1e-6;
"""


def _topo_set_dict(zone: str, grid: Grid) -> str:
    # Every cell of the zone mesh is in the cell zone: the box is the zone's
    # background block itself, not a geometric test (as the VAWT rotor).
    lo, hi = grid.minimum, grid.maximum
    return foam_header("topoSetDict") + f"""
actions
(
    {{
        name    {zone}Cells;
        type    cellSet;
        action  new;
        source  boxToCell;
        box     {foam_vector(*lo)} {foam_vector(*hi)};
    }}
    {{
        name    {zone};
        type    cellZoneSet;
        action  new;
        source  setToCellZone;
        set     {zone}Cells;
    }}
);
"""


def _create_patch_dict(pairs: Sequence[AmiPair]) -> str:
    # Entries as the propeller tutorial's createPatchDict and V0 E1.
    def entry(name: str, neighbour: str, source: str) -> str:
        return f"""    {{
        name {name};
        patchInfo
        {{
            type            {CYCLIC_AMI};
            matchTolerance  0.0001;
            neighbourPatch  {neighbour};
            transform       noOrdering;
        }}
        constructFrom patches;
        patches ({source});
    }}"""
    entries = []
    for pair in pairs:
        entries.append(entry(pair.first, pair.second, pair.first + SOURCE_SUFFIX))
        entries.append(entry(pair.second, pair.first, pair.second + SOURCE_SUFFIX))
    return foam_header("createPatchDict") + f"""
pointSync false;

patches
(
{chr(10).join(entries)}
);
"""


def _ami_weights_dict() -> str:
    # The AMIWeights function object (tutorials .../propeller/system/AMIWeights),
    # run by postProcess -constant on the finished mesh (section 18, decision 4).
    return foam_header("amiWeightsDict") + """
functions
{
    AMIWeights1
    {
        type            AMIWeights;
        libs            (fieldFunctionObjects);
        writeFields     false;
        writeToFile     true;
        log             true;
    }
}
"""


# --- surfaces ----------------------------------------------------------------------------------

def _level_offset(coarse: float, fine: float) -> int:
    """Refinement levels that bring cells of `coarse` size near `fine` (F3)."""
    return max(0, round(math.log2(coarse / fine)))


def _body_surface(body: BodyConfig, mesh: trimesh.Trimesh, patch: str, offset: int,
                  kind: str = "wall") -> Surface:
    r = body.refinement
    return Surface(
        stem=patch, regions={patch: kind},
        level=(r.min_level + offset, r.max_level + offset),
        features=r.feature_level + offset if r.extract_features else None,
        mesh=_stl([(patch, mesh)]))


def _zone_surface(zone: RotatingZone, side: str, level: int, hole_level: int) -> Surface:
    names = {face.region: (face.stationary if side == "stat" else face.rotating) + SOURCE_SUFFIX
             for face in interfaces(zone)}
    regions = [(names[region], mesh) for region, mesh in zone_surface(zone)]
    hole = {names[INNER]: (hole_level, hole_level)} if INNER in names else {}
    return Surface(stem=f"{zone.name}_{side}", regions={n: "patch" for n, _ in regions},
                   level=(level, level), features=max(level, hole_level) if hole else level,
                   mesh=_stl(regions), region_levels=hole)


def _patch_of(config: MachineProjectConfig, body: BodyConfig) -> str:
    patch = config.patch_for(SourceKind.BODY, body.name)
    return patch.name if patch else body.name


# --- cases ------------------------------------------------------------------------------------

@dataclass
class _Content:
    """What one domain mesh gets besides its own boundary."""

    surfaces: list[Surface] = field(default_factory=list)
    layered: list[tuple[str, LayersConfig]] = field(default_factory=list)
    expected: dict[str, str] = field(default_factory=dict)


def _cylinder_zones(config: MachineProjectConfig) -> list[RotatingZone]:
    return [z for z in config.rotating_zones if isinstance(z.shape, CylinderZone)]


def _check_imported_zones(config: MachineProjectConfig) -> None:
    imported = any(isinstance(z.shape, ImportedZone) for z in config.rotating_zones)
    if imported and not isinstance(config.domain, ImportedDomain):
        raise ValueError("An imported rotating zone needs an imported domain: it meets the "
                         "domain parts at joints.")


def _domain_content(config: MachineProjectConfig, zones: Sequence[RotatingZone],
                    bodies: Sequence[BodyConfig], meshes: Mapping[str, trimesh.Trimesh],
                    cell: float, wall: str = "wall") -> _Content:
    content = _Content()
    for zone in zones:
        shape = zone.shape
        assert isinstance(shape, CylinderZone)
        # The hole's wall: the same cell size as on the zone side (G0 R5).
        hole = shape.hole_level + _level_offset(cell, zone.cell_size)
        content.surfaces.append(_zone_surface(zone, "stat", zone.interface_level, hole))
        content.expected.update({f.stationary + SOURCE_SUFFIX: "patch" for f in interfaces(zone)})
    for body in bodies:
        patch = _patch_of(config, body)
        offset = 0
        if body.motion is Motion.SPLIT:  # the same cell size on both sides of the zone (F3)
            own = config.zone(body.zone or "")
            offset = _level_offset(cell, own.cell_size) if own else 0
        content.surfaces.append(_body_surface(body, meshes[body.name], patch, offset, wall))
        content.expected[patch] = wall
        if body.layers.enabled:
            content.layered.append((patch, body.layers))
    return content


def _with_content(case: DomainCase, config: MachineProjectConfig, content: _Content,
                  location: Vec3, own: Surface | None) -> DomainCase:
    surfaces = [*([own] if own else []), *content.surfaces]
    stems = [s.stem for s in surfaces if s.features is not None]
    dictionaries = dict(case.dictionaries)
    dictionaries["system/snappyHexMeshDict"] = _snappy_dict(config, surfaces, location,
                                                            content.layered)
    dictionaries["system/surfaceFeatureExtractDict"] = _feature_extract_dict(stems)
    return DomainCase(
        case.name, case.owner, dictionaries,
        {f"{SURFACE_DIR}/{s.stem}.stl": s.mesh for s in surfaces},
        {**case.expected, **content.expected}, case.other, case.cut or bool(content.surfaces),
        case.unplaced)


def _stationary(config: MachineProjectConfig) -> list[BodyConfig]:
    return [b for b in config.bodies if b.motion is not Motion.ROTATING]


def _own_surface(case: DomainCase, stem: str) -> Surface:
    """The domain's own cut surface (G2), its regions in file order."""
    mesh = case.surfaces[f"{SURFACE_DIR}/{stem}.stl"]
    types = {**{o: "patch" for o in case.other}, **case.expected}
    names = [line.split()[1].decode() for line in mesh.splitlines()
             if line.startswith(b"solid ")]
    return Surface(stem, {n: types[n] for n in names}, (0, 0), 0, mesh)


def _owner_cell(config: MachineProjectConfig, owner: str) -> float:
    """The cell size of an imported part or zone, by its configuration path."""
    section, index = owner.rsplit(".", 1)
    if section == "domain.parts":
        assert isinstance(config.domain, ImportedDomain)
        return config.domain.parts[int(index)].cell_size
    return config.rotating_zones[int(index)].cell_size


def joint_levels(config: MachineProjectConfig,
                 surfaces: Sequence[SurfaceInfo]) -> dict[str, int]:
    """Region -> refinement level of every joint region: the joint's level, plus
    the levels that bring the coarser side to the finer side's cell size."""
    cells = {r.name: _owner_cell(config, s.owner) for s in surfaces for r in s.regions}
    levels: dict[str, int] = {}
    for joint in config.joints:
        if joint.first not in cells or joint.second not in cells:
            continue  # an unknown region is BLOCKING before meshing
        finer = min(cells[joint.first], cells[joint.second])
        for region in (joint.first, joint.second):
            levels[region] = joint.level + _level_offset(cells[region], finer)
    return levels


@dataclass(frozen=True)
class _Imported:
    """An imported surface ready for its case: the surface, with its regions
    named after their patches (<region>_src for a joint region), and the
    patches its mesh must have (name -> type) and may have."""

    surface: Surface
    expected: dict[str, str]
    other: tuple[str, ...]


def _owner_surface(config: MachineProjectConfig, owner: str) -> ImportedSurface:
    section, index = owner.rsplit(".", 1)
    if section == "domain.parts":
        assert isinstance(config.domain, ImportedDomain)
        return config.domain.parts[int(index)]
    shape = config.rotating_zones[int(index)].shape
    assert isinstance(shape, ImportedZone)
    return shape


def _imported_surface(config: MachineProjectConfig, info: SurfaceInfo, stem: str,
                      levels: Mapping[str, int], typed: bool) -> _Imported:
    refinement = _owner_surface(config, info.owner).refinement
    named: list[tuple[str, trimesh.Trimesh]] = []
    types: dict[str, str] = {}
    region_levels: dict[str, tuple[int, int]] = {}
    expected: dict[str, str] = {}
    other: list[str] = []
    for region in info.regions:
        if region.name in levels:
            name = region.name + SOURCE_SUFFIX
            types[name] = expected[name] = "patch"
            region_levels[name] = (levels[region.name], levels[region.name])
        else:
            patch = config.patch_for(SourceKind.REGION, region.name)
            name = patch.name if patch else region.name
            types[name] = openfoam_type(patch.type) if patch and typed else "patch"
            if patch:
                expected[name] = types[name]
            else:
                other.append(name)
            level = refinement.get(region.name, 0)
            if level:
                region_levels[name] = (level, level)
        named.append((name, region.mesh))
    # Feature edges at the finest region level (a joint's rim, a blade's edges).
    features = max((lo for lo, _ in region_levels.values()), default=0)
    return _Imported(Surface(stem, types, (0, 0), features, _stl(named), region_levels),
                     expected, tuple(other))


def _imported_case(config: MachineProjectConfig, name: str, owner: str, own: _Imported,
                   grid: Grid, location: Vec3, content: _Content, zone: str | None = None,
                   unplaced: tuple[str, ...] = ()) -> DomainCase:
    """A case cut from an imported surface (a domain part or an imported zone)."""
    surfaces = [own.surface, *content.surfaces]
    stems = [s.stem for s in surfaces if s.features is not None]
    dictionaries = {
        **_common(config),
        "system/blockMeshDict": _block_mesh_dict(grid, _background_boundary()),
        "system/surfaceFeatureExtractDict": _feature_extract_dict(stems),
        "system/snappyHexMeshDict": _snappy_dict(config, surfaces, location, content.layered),
    }
    if zone is not None:
        dictionaries["system/topoSetDict"] = _topo_set_dict(zone, grid)
    return DomainCase(name, owner, dictionaries,
                      {f"{SURFACE_DIR}/{s.stem}.stl": s.mesh for s in surfaces},
                      {**own.expected, **content.expected}, own.other, cut=True,
                      unplaced=unplaced)


def _part_index(surfaces: Sequence[SurfaceInfo], points: np.ndarray) -> int:
    """The imported part holding most of the points."""
    counts = []
    for surface in surfaces:
        union = surface.union()
        counts.append(int(contains_points(points, union.vertices, union.faces).sum()))
    return int(np.argmax(counts))


def _zone_point(zone: RotatingZone) -> np.ndarray:
    """A point inside the zone (in the annulus for an annular zone)."""
    shape = zone.shape
    assert isinstance(shape, CylinderZone)
    u, v = plane_axes(zone.axis)
    point = np.zeros((1, 3))
    radial = (shape.diameter + (shape.hole_diameter or 0.0)) / 4.0 if shape.hole_diameter else 0.0
    point[0, u.position], point[0, v.position] = shape.centre_u + radial, shape.centre_v
    point[0, zone.axis.position] = (shape.axis_min + shape.axis_max) / 2.0
    return point


def _domain_cases(config: MachineProjectConfig, zones: Sequence[RotatingZone],
                  surfaces: Sequence[SurfaceInfo], meshes: Mapping[str, trimesh.Trimesh],
                  typed: bool) -> tuple[DomainCase, ...]:
    domain = config.domain
    wall = "wall" if typed else "patch"
    # Untyped (H3): every face and region meshed as a plain patch.
    base = DomainCaseGenerator().render(config if typed else _untyped(config), surfaces)
    stationary = _stationary(config)
    if isinstance(domain, BoxDomain):
        content = _domain_content(config, zones, stationary, meshes, domain.cell_size, wall)
        return (_with_content(base[0], config, content, domain.location_in_mesh, None),)
    if isinstance(domain, CylinderDomain):
        content = _domain_content(config, zones, stationary, meshes, domain.cell_size, wall)
        return (_with_content(base[0], config, content, domain.location_in_mesh,
                              _own_surface(base[0], DOMAIN_CASE)),)
    if not isinstance(domain, ImportedDomain):
        raise ValueError("A machine needs a domain; rotor-only projects are VAWT only.")
    # Each zone and stationary body goes into the imported part that holds it.
    parts = [s for s in surfaces if s.owner.startswith("domain.parts.")]
    placed: list[tuple[list[RotatingZone], list[BodyConfig]]] = [([], []) for _ in base]
    for zone in zones:
        placed[_part_index(parts, _zone_point(zone))][0].append(zone)
    for body in stationary:
        placed[_part_index(parts, np.asarray(meshes[body.name].vertices))][1].append(body)
    levels = joint_levels(config, surfaces)
    result = []
    for case, part, info, (part_zones, part_bodies) in zip(base, domain.parts, parts, placed,
                                                            strict=True):
        content = _domain_content(config, part_zones, part_bodies, meshes, part.cell_size,
                                  wall)
        grid = surface_grid(info, part.cell_size)
        assert grid is not None  # rendered by DomainCaseGenerator: the part was read
        result.append(_imported_case(
            config, case.name, case.owner, _imported_surface(config, info, part.name, levels,
                                                             typed),
            grid, part.location_in_mesh, content, unplaced=case.unplaced))
    return tuple(result)


def _zone_case(config: MachineProjectConfig, zone: RotatingZone,
               meshes: Mapping[str, trimesh.Trimesh], wall: str) -> DomainCase:
    grid = zone_grid(zone)
    shape = zone.shape
    assert isinstance(shape, CylinderZone)
    surfaces = [_zone_surface(zone, "rot", 0, shape.hole_level)]
    expected = {f.rotating + SOURCE_SUFFIX: "patch" for f in interfaces(zone)}
    layered: list[tuple[str, LayersConfig]] = []
    for body in config.bodies:
        if body.zone != zone.name:
            continue
        patch = _patch_of(config, body) if body.motion is Motion.ROTATING else split_patch(body)
        surfaces.append(_body_surface(body, meshes[body.name], patch, 0, wall))
        expected[patch] = wall
        if body.layers.enabled:
            layered.append((patch, body.layers))
    stems = [s.stem for s in surfaces if s.features is not None]
    dictionaries = {
        **_common(config),
        "system/blockMeshDict": _block_mesh_dict(grid, _background_boundary()),
        "system/surfaceFeatureExtractDict": _feature_extract_dict(stems),
        "system/snappyHexMeshDict": _snappy_dict(config, surfaces, zone.location_in_mesh,
                                                 layered),
        "system/topoSetDict": _topo_set_dict(zone.name, grid),
    }
    return DomainCase(zone_case_name(zone), f"rotating_zones.{config.rotating_zones.index(zone)}",
                      dictionaries, {f"{SURFACE_DIR}/{s.stem}.stl": s.mesh for s in surfaces},
                      expected, (), cut=True)


def _imported_zone_case(config: MachineProjectConfig, zone: RotatingZone,
                        surfaces: Sequence[SurfaceInfo], meshes: Mapping[str, trimesh.Trimesh],
                        wall: str, typed: bool) -> DomainCase:
    """An imported zone (K3): its surface cut out of its background block, with
    its rotating bodies; every cell in the cell zone (G0 R4)."""
    owner = f"rotating_zones.{config.rotating_zones.index(zone)}"
    info = next((s for s in surfaces if s.owner == owner), None)
    grid = surface_grid(info, zone.cell_size) if info is not None else None
    if info is None or grid is None:
        raise ValueError(f"Imported rotating zone '{zone.name}' has not been read.")
    content = _Content()
    for body in config.bodies:
        if body.zone != zone.name:
            continue
        if body.motion is not Motion.ROTATING:
            raise ValueError(f"Body '{body.name}' is {body.motion.value} in imported zone "
                             f"'{zone.name}'; only a rotating body can be.")
        patch = _patch_of(config, body)
        content.surfaces.append(_body_surface(body, meshes[body.name], patch, 0, wall))
        content.expected[patch] = wall
        if body.layers.enabled:
            content.layered.append((patch, body.layers))
    own = _imported_surface(config, info, zone.name, joint_levels(config, surfaces), typed)
    return _imported_case(config, zone_case_name(zone), owner, own, grid,
                          zone.location_in_mesh, content, zone=zone.name)


def final_types(config: MachineProjectConfig) -> dict[str, str]:
    """The OpenFOAM type of every configured and generated (split) patch."""
    types = {p.name: openfoam_type(p.type) for p in config.patches}
    types.update({split_patch(b): "wall" for b in config.bodies if b.motion is Motion.SPLIT})
    return types


def _untyped(config: MachineProjectConfig) -> MachineProjectConfig:
    return config.model_copy(update={"patches": tuple(
        p.model_copy(update={"type": PatchType.SLIP}) for p in config.patches)})


def _merged_case(config: MachineProjectConfig, domain: Sequence[DomainCase],
                 zones: Sequence[DomainCase], pairs: Sequence[AmiPair]) -> DomainCase:
    types = final_types(config)
    expected: dict[str, str] = {}
    for case in (*domain, *zones):
        expected.update({n: types.get(n, t) for n, t in case.expected.items()
                         if not n.endswith(SOURCE_SUFFIX)})
    for pair in pairs:
        expected[pair.first] = CYCLIC_AMI
        expected[pair.second] = CYCLIC_AMI
    other = tuple(o for case in (*domain, *zones) for o in case.other)
    dictionaries = {**_common(config), "system/createPatchDict": _create_patch_dict(pairs),
                    AMI_WEIGHTS_DICT: _ami_weights_dict()}
    return DomainCase(MERGED_CASE, "merged", dictionaries, {}, expected, other, cut=True,
                      unplaced=tuple(u for case in domain for u in case.unplaced))


class MachineCaseGenerator:
    """Every case of a machine: domain, one per zone, merged (F1)."""

    def render(self, config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
               meshes: Mapping[str, trimesh.Trimesh], *, typed: bool = True) -> MachineCases:
        """`surfaces`: read_imported(config); `meshes`: load_bodies(config).

        typed=False (H3): the domain and zone meshes have plain patches only, and
        the merged case sets the final types (retype), so a change of patch type
        alone re-runs only the assembly.
        """
        _check_imported_zones(config)
        zones = _cylinder_zones(config)
        missing = [b.name for b in config.bodies if b.name not in meshes]
        if missing:
            raise ValueError(f"Bodies not loaded: {', '.join(missing)}.")
        wall = "wall" if typed else "patch"
        domain = _domain_cases(config, zones, surfaces, meshes, typed)
        zone_cases = tuple(
            _zone_case(config, zone, meshes, wall) if isinstance(zone.shape, CylinderZone)
            else _imported_zone_case(config, zone, surfaces, meshes, wall, typed)
            for zone in config.rotating_zones)
        faces = tuple(face for zone in zones for face in interfaces(zone))
        joints = joint_pairs(config)
        merged = _merged_case(config, domain, zone_cases,
                              (*(face.pair for face in faces), *joints))
        retype = {} if typed else {n: t for n, t in merged.expected.items()
                                   if t not in ("patch", CYCLIC_AMI)}
        return MachineCases(domain, zone_cases, merged, faces, retype, joints)

    @staticmethod
    def write(project_root: Path, cases: MachineCases) -> tuple[str, ...]:
        return DomainCaseGenerator.write(project_root, cases.all)


# --- running order --------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One meshing step. kind "run": argv in case; "copy_mesh": copy
    constant/polyMesh of `source` into `case` (the merged mesh's start)."""

    name: str
    kind: str
    case: str
    argv: tuple[str, ...] = ()
    source: str = ""


def meshing_steps(cases: MachineCases) -> tuple[Step, ...]:
    """The commands, in order, with case paths relative to the project root.

    Commands run from their case directory (V0 R9: mergeMeshes appends
    "/processor" to the master path when the working directory's name ends
    in "processor").
    """
    steps: list[Step] = []
    for case in (*cases.domain, *cases.zones):
        root = case.root
        steps.append(Step(f"{case.name}_blockMesh", "run", root, ("blockMesh", "-case", ".")))
        if case.cut:
            steps.append(Step(f"{case.name}_surfaceFeatureExtract", "run", root,
                              ("surfaceFeatureExtract", "-case", ".")))
            steps.append(Step(f"{case.name}_snappyHexMesh", "run", root,
                              ("snappyHexMesh", "-case", ".", "-overwrite")))
        if "system/topoSetDict" in case.dictionaries:
            steps.append(Step(f"{case.name}_topoSet", "run", root, ("topoSet", "-case", ".")))
    merged = cases.merged.root
    first, *others = cases.domain
    steps.append(Step("merged_copy", "copy_mesh", merged, source=first.root))
    for case in (*others, *cases.zones):
        steps.append(Step(f"merged_add_{case.name}", "run", merged,
                          ("mergeMeshes", "-overwrite", ".", f"../{case.name}")))
    steps.append(Step("merged_createPatch", "run", merged, ("createPatch", "-overwrite",
                                                             "-case", ".")))
    for patch, kind in cases.retype.items():  # H3: final types on the merged mesh
        steps.append(Step(f"merged_type_{patch}", "run", merged, (
            "foamDictionary", "constant/polyMesh/boundary", "-entry",
            f"entry0/{patch}/type", "-set", kind)))
    steps.append(Step("merged_checkMesh", "run", merged,
                      ("checkMesh", "-case", ".", "-allTopology", "-meshQuality")))
    steps.append(Step("merged_amiWeights", "run", merged,
                      ("postProcess", "-case", ".", "-dict", AMI_WEIGHTS_DICT, "-constant")))
    return tuple(steps)

