"""The cases and commands of a .msh import (docs/msh_import_notes.md).

    cases/imported/   the converted mesh, scaled to metres: the verified
                      artifact. Nothing writes into it after the import
                      operation (invariant 14).
    cases/merged/     a copy of it with the renamed patches (createPatch), the
                      AMI pairs and the patch types (foamDictionary); checked
                      by checkMesh. Case setup copies this mesh, as for an STL
                      project.
    cases/imported_scratch/  a conversion at scale 1 made only to show the
                      bounding box of a file whose box cannot be read raw
                      (owner answer 4). Never verified: the import promotes it
                      (moves its polyMesh) only when its record matches the file
                      and the tool, and then checks it like any conversion.

Converter: fluent3DMeshToFoam for Fluent (owner decision 2: fluentMeshToFoam
drops wall baffles silently), gmshToFoam for Gmsh. A binary Gmsh file, or a
version gmshToFoam was not proven on, is first re-saved as MSH 2.2 ASCII with
the gmsh package (resave.py). The units are applied after conversion with
transformPoints, which gave the same points as the converters' own -scale
(E0). An AMI pair keeps the patches' own names: foamDictionary sets type
cyclicAMI, neighbourPatch, transform and matchTolerance in the boundary file
(v2512, M1c; checkMesh "Mesh OK", AMI weights and pimpleFoam as before).
createPatch with a patch's own name would leave its type unchanged (exit 0;
v2512, M1b), so it is used only for renamed patches.

Every case writes binary (M2a): a large mesh is never read whole in Python;
its geometry comes from the surface the tools extract (surface.py). The
boundary file stays text, and so do the zone names and the headers.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from machines.assembly import AMI_WEIGHTS_DICT, CYCLIC_AMI, Step, _ami_weights_dict
from machines.case_generator import _common, _control_dict
from machines.config import (
    ImportedMesh,
    InterfaceJoint,
    MachineParts,
    MachineProjectConfig,
    PatchType,
    SourceKind,
)
from machines.msh_import import interfaces, resave
from machines.msh_import.detect import DIRECT_GMSH_VERSIONS, MeshFileInfo, MeshFormat
from machines.msh_import.quality import WRITE_FIELDS
from machines.patches import openfoam_type
from openfoam.dictionary import foam_header

IMPORTED_CASE = "imported"
MERGED_CASE = "merged"
SCRATCH_CASE = "imported_scratch"
RESAVED = "resaved.msh"  # in cases/imported/: the MSH 2.2 copy gmshToFoam reads
AMI_MATCH_TOLERANCE = "0.0001"
EMPTY_PATCH_DICT = "system/createPatchDict.empty"  # removes the patches left empty
PART_ZONES_DICT = "system/topoSetDict.parts"  # each part's zone <P> (M2b)
BAFFLES_LOG = "02_baffles.log"  # mergeOrSplitBaffles -detectOnly, in logs/imported/


def binary_control_dict() -> str:
    """The meshing controlDict with writeFormat binary: every imported mesh is
    written binary (owner, M2a), so no file is converted to text on the way.
    The STL path keeps its ASCII controlDict."""
    text = _control_dict()
    ascii_format = "writeFormat     ascii;"
    assert ascii_format in text
    return text.replace(ascii_format, "writeFormat     binary;")


def binary_common(config: MachineProjectConfig) -> dict[str, str]:
    return {**_common(config), "system/controlDict": binary_control_dict()}


def imported_ami_weights_dict() -> str:
    """The AMIWeights function object with writeFields true: each side's
    per-face sum(weights), for where a pair falls short (M2a evidence 3).
    STL projects keep writeFields false."""
    text = _ami_weights_dict()
    off = "writeFields     false;"
    assert off in text
    return text.replace(off, "writeFields     true;")


def converter(info: MeshFileInfo) -> str:
    return "fluent3DMeshToFoam" if info.format is MeshFormat.FLUENT else "gmshToFoam"


def needs_resave(info: MeshFileInfo) -> bool:
    return info.format is MeshFormat.GMSH and (
        info.binary or info.version not in DIRECT_GMSH_VERSIONS)


def convert_steps(info: MeshFileInfo, source: Path, case: str) -> tuple[Step, ...]:
    """Re-save (when needed) and convert, at scale 1, in cases/<case>/."""
    root = f"cases/{case}"
    steps: list[Step] = []
    read = str(source)
    if needs_resave(info):
        steps.append(Step(f"{case}_resave", "run", root,
                          (sys.executable, str(Path(resave.__file__)), read, RESAVED)))
        read = RESAVED
    steps.append(Step(f"{case}_convert", "run", root, (converter(info), read, "-case", ".")))
    return tuple(steps)


def scale_step(factor: float, case: str = IMPORTED_CASE) -> Step | None:
    """transformPoints to metres, or None at scale 1."""
    if factor == 1.0:
        return None
    value = f"{factor:.17g}"
    return Step(f"{case}_scale", "run", f"cases/{case}", (
        "transformPoints", "-case", ".", "-scale", f"({value} {value} {value})"))


def _mesh_types(config: MachineProjectConfig) -> dict[str, tuple[str, PatchType]]:
    """Imported patch -> (final name, type) of every MESH_PATCH patch."""
    return {p.source.ref: (p.name, p.type) for p in config.patches
            if p.source.kind is SourceKind.MESH_PATCH}


def ami_sides(config: MachineProjectConfig) -> tuple[tuple[str, str], ...]:
    """(patch, neighbourPatch) of each side of each AMI pair, original names."""
    mesh = config.imported_mesh
    assert mesh is not None
    return tuple(side for p in mesh.ami_pairs
                 for side in ((p.first, p.second), (p.second, p.first)))


def used_names(config: MachineProjectConfig) -> dict[str, str]:
    """Patch of the file -> its name in the mesh: a renamed patch's new name,
    every other patch (an AMI patch too) its own."""
    mesh = config.imported_mesh
    assert mesh is not None
    names = {ref: name for ref, (name, _) in _mesh_types(config).items()}
    names.update({n: n for p in mesh.ami_pairs for n in (p.first, p.second)})
    if isinstance(mesh, MachineParts):  # an interface's patches become its side (M2b-2)
        for interface in mesh.interfaces:
            for side, patches in zip(interfaces.sides(interface),
                                     (interface.first, interface.second), strict=True):
                names.update(dict.fromkeys(patches, side))
    return dict(sorted(names.items()))


def create_patch_dict(config: MachineProjectConfig) -> str | None:
    """The renamed patches and a machine's interfaces (M2b-2), or None when
    there is none."""
    entries: list[str] = []
    for ref, (name, kind) in sorted(_mesh_types(config).items()):
        if name != ref:
            entries.append(f"""    {{
        name {name};
        patchInfo
        {{
            type            {openfoam_type(kind)};
        }}
        constructFrom patches;
        patches ({ref});
    }}""")
    mesh = config.imported_mesh
    if isinstance(mesh, MachineParts):
        entries += interfaces.create_patch_entries(mesh)
    if not entries:
        return None
    return foam_header("createPatchDict") + f"""
pointSync false;

patches
(
{chr(10).join(entries)}
);
"""


def retype(config: MachineProjectConfig) -> dict[str, str]:
    """Patch -> OpenFOAM type set with foamDictionary: every typed patch that
    keeps its name (a renamed one gets its type from createPatch). A converter
    writes Fluent walls as wall and the rest as patch, gmshToFoam everything as
    patch (E0), so every type is set, never assumed."""
    return {name: openfoam_type(kind) for ref, (name, kind) in sorted(_mesh_types(config).items())
            if name == ref}


def expected_patches(config: MachineProjectConfig) -> dict[str, str]:
    """The merged mesh's patches and their OpenFOAM types."""
    expected = {name: openfoam_type(kind) for name, kind in _mesh_types(config).values()}
    for patch, _ in ami_sides(config):
        expected[patch] = CYCLIC_AMI
    mesh = config.imported_mesh
    if isinstance(mesh, MachineParts):
        expected.update(interfaces.expected(mesh))
    return expected


@dataclass(frozen=True)
class ImportCases:
    """Dictionaries (relative path -> text) of the two cases, and the steps
    after conversion."""

    imported: dict[str, str]
    merged: dict[str, str]
    retype: dict[str, str]
    expected: dict[str, str]
    ami: tuple[tuple[str, str], ...] = ()  # ami_sides
    # checkMesh -allTopology (M2a). A machine of several files (M2b) checks
    # without it (23 M: 23.5 GB / 90 s against 31.9 GB / 170 s) and counts the
    # baffles with mergeOrSplitBaffles -detectOnly instead (9.2 GB / 7.8 s).
    all_topology: bool = True
    stitch: tuple[tuple[str, str], ...] = ()  # interfaces stitched (M2b-2): their sides

    def merged_steps(self) -> tuple[Step, ...]:
        root = f"cases/{MERGED_CASE}"
        steps = [Step("merged_copy", "copy_mesh", root, source=f"cases/{IMPORTED_CASE}")]
        if "system/createPatchDict" in self.merged:
            steps.append(Step("merged_createPatch", "run", root,
                              ("createPatch", "-overwrite", "-case", ".")))
        for patch, kind in self.retype.items():
            steps.append(_set(root, f"merged_type_{patch}", patch, "type", kind))
        for first, second in self.stitch:  # then the emptied sides removed
            steps.append(Step(f"merged_stitch_{first}", "run", root, (
                "stitchMesh", "-case", ".", "-perfect", "-overwrite", first, second)))
        if self.stitch:
            steps.append(Step("merged_drop_empty", "run", root, (
                "createPatch", "-overwrite", "-case", ".", "-dict", EMPTY_PATCH_DICT)))
            # The stitch rebuilds the mesh; the part zones are made again.
            steps.append(Step("merged_part_zones", "run", root, (
                "topoSet", "-case", ".", "-dict", PART_ZONES_DICT)))
        for patch, neighbour in self.ami:
            for key, value in (("type", CYCLIC_AMI), ("neighbourPatch", neighbour),
                               ("transform", "noOrdering"),
                               ("matchTolerance", AMI_MATCH_TOLERANCE)):
                steps.append(_set(root, f"merged_ami_{patch}_{key}", patch, key, value))
            # A converter's group (Fluent "wall": inGroups 1(wall)) would put the
            # interface in that group; -remove exits 0 without one (v2512, M1c).
            steps.append(Step(f"merged_ami_{patch}_inGroups", "run", root, (
                "foamDictionary", "constant/polyMesh/boundary", "-entry",
                f"entry0/{patch}/inGroups", "-remove")))
        return tuple(steps)

    def check_steps(self, ami: bool) -> tuple[Step, ...]:
        root = f"cases/{MERGED_CASE}"
        # One pass (M2a evidence 5): the checks, the bad faces and cells as VTK
        # for ParaView, and the cell fields for the per-zone counts (quality.py).
        topology = ("-allTopology",) if self.all_topology else ()
        steps = [Step("merged_checkMesh", "run", root,
                      ("checkMesh", "-case", ".", *topology, "-meshQuality",
                       "-writeSets", "vtk", "-writeFields", WRITE_FIELDS))]
        if not self.all_topology:  # second, so its log is BAFFLES_LOG
            # On the parts merged, before any interface: an ACMI couple and its
            # blockage share their faces, and mergeOrSplitBaffles aborts on
            # them as duplicates (v2512, M2b-2).
            steps.append(Step("imported_baffles", "run", f"cases/{IMPORTED_CASE}",
                              ("mergeOrSplitBaffles", "-case", ".", "-detectOnly")))
        if ami:
            steps.append(Step("merged_amiWeights", "run", root, (
                "postProcess", "-case", ".", "-dict", AMI_WEIGHTS_DICT, "-constant")))
        return tuple(steps)

    def check_kind(self, step: Step) -> str:
        """The resource model of a check step (resources.py)."""
        if step.argv[0] == "checkMesh":
            return "checkMesh" if self.all_topology else "checkMeshTopology"
        return "baffles" if step.argv[0] == "mergeOrSplitBaffles" else "amiWeights"

    def check_executables(self) -> tuple[str, ...]:
        return ("checkMesh", "postProcess",
                *(() if self.all_topology else ("mergeOrSplitBaffles",)))


def _set(root: str, name: str, patch: str, key: str, value: str) -> Step:
    """foamDictionary setting one entry of a patch in the boundary file: -set
    for the type every patch has, -add for an entry a converter does not write."""
    action = "-set" if key == "type" else "-add"
    return Step(name, "run", root, ("foamDictionary", "constant/polyMesh/boundary",
                                    "-entry", f"entry0/{patch}/{key}", action, value))


def import_cases(config: MachineProjectConfig) -> ImportCases:
    common = binary_common(config)
    merged = dict(common)
    patch_dict = create_patch_dict(config)
    if patch_dict is not None:
        merged["system/createPatchDict"] = patch_dict
    mesh = config.imported_mesh
    assert mesh is not None
    stitch: tuple[tuple[str, str], ...] = ()
    if isinstance(mesh, MachineParts):
        stitch = tuple(interfaces.sides(i) for i in mesh.interfaces
                       if i.joint is InterfaceJoint.STITCH)
        if stitch:
            merged[EMPTY_PATCH_DICT] = empty_patch_dict()
            merged[PART_ZONES_DICT] = part_zones_dict([p.prefix for p in mesh.parts])
    if coupled_pairs(mesh):
        merged[AMI_WEIGHTS_DICT] = imported_ami_weights_dict()
    return ImportCases(dict(common), merged, retype(config), expected_patches(config),
                       ami_sides(config), all_topology=not isinstance(mesh, MachineParts),
                       stitch=stitch)


def part_zones_dict(prefixes: Sequence[str]) -> str:
    """topoSet making each part's cell zone <P> from its own zones <P>_*. Made
    after the parts are merged, never before: mergeMeshes keeps one zone per cell
    (the first), so a zone overlapping the file's zones loses the copies' cells
    (v2512, M2b: the runner's zone held one passage of 15, the draft tube's none)."""
    actions = "\n".join(
        f"    {{ name {p}; type cellSet; action new; source zoneToCell; zones (\"{p}_.*\"); }}\n"
        f"    {{ name {p}; type cellZoneSet; action new; source setToCellZone; set {p}; }}\n"
        f"    {{ name {p}; type cellSet; action remove; }}" for p in prefixes)
    return foam_header("topoSetDict") + f"\nactions\n(\n{actions}\n);\n"


def coupled_pairs(mesh: ImportedMesh | MachineParts) -> tuple[tuple[str, str], ...]:
    """The cyclicAMI and cyclicACMI pairs of the merged mesh."""
    if isinstance(mesh, MachineParts):
        return interfaces.coupled(mesh)
    return tuple((p.first, p.second) for p in mesh.ami_pairs)


def empty_patch_dict() -> str:
    """createPatch with no patch to make: it removes the patches left with no
    face (stitched ones), keeping the stitch faceZones (v2512)."""
    return foam_header("createPatchDict") + "\npointSync false;\n\npatches ();\n"
