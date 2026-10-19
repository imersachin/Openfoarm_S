"""The cases and commands of a .msh import (docs/msh_import_notes.md).

    cases/imported/   the converted mesh, scaled to metres: the verified
                      artifact. Nothing writes into it after the import
                      operation (invariant 14).
    cases/merged/     a copy of it with the AMI pairs (createPatch), renamed
                      patches and the patch types (foamDictionary); checked
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
(E0). createPatch with a patch's own name leaves its type unchanged (exit 0;
seen on v2512 during M1b), so each AMI patch gets a new name, <patch>_ami.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from machines.assembly import AMI_WEIGHTS_DICT, CYCLIC_AMI, Step, _ami_weights_dict
from machines.case_generator import _common
from machines.config import MachineProjectConfig, PatchType, SourceKind
from machines.msh_import import resave
from machines.msh_import.detect import DIRECT_GMSH_VERSIONS, MeshFileInfo, MeshFormat
from machines.patches import openfoam_type
from openfoam.dictionary import foam_header

IMPORTED_CASE = "imported"
MERGED_CASE = "merged"
SCRATCH_CASE = "imported_scratch"
RESAVED = "resaved.msh"  # in cases/imported/: the MSH 2.2 copy gmshToFoam reads
AMI_SUFFIX = "_ami"


def ami_name(patch: str) -> str:
    """The cyclicAMI patch made from an imported patch."""
    return f"{patch}{AMI_SUFFIX}"


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


def create_patch_dict(config: MachineProjectConfig) -> str | None:
    """The AMI pairs and the renamed patches, or None when there is neither."""
    mesh = config.imported_mesh
    assert mesh is not None
    entries: list[str] = []
    for pair in mesh.ami_pairs:
        for patch, neighbour in ((pair.first, pair.second), (pair.second, pair.first)):
            entries.append(f"""    {{
        name {ami_name(patch)};
        patchInfo
        {{
            type            {CYCLIC_AMI};
            matchTolerance  0.0001;
            neighbourPatch  {ami_name(neighbour)};
            transform       noOrdering;
        }}
        constructFrom patches;
        patches ({patch});
    }}""")
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
    mesh = config.imported_mesh
    assert mesh is not None
    for pair in mesh.ami_pairs:
        expected[ami_name(pair.first)] = CYCLIC_AMI
        expected[ami_name(pair.second)] = CYCLIC_AMI
    return expected


@dataclass(frozen=True)
class ImportCases:
    """Dictionaries (relative path -> text) of the two cases, and the steps
    after conversion."""

    imported: dict[str, str]
    merged: dict[str, str]
    retype: dict[str, str]
    expected: dict[str, str]

    def merged_steps(self) -> tuple[Step, ...]:
        root = f"cases/{MERGED_CASE}"
        steps = [Step("merged_copy", "copy_mesh", root, source=f"cases/{IMPORTED_CASE}")]
        if "system/createPatchDict" in self.merged:
            steps.append(Step("merged_createPatch", "run", root,
                              ("createPatch", "-overwrite", "-case", ".")))
        for patch, kind in self.retype.items():
            steps.append(Step(f"merged_type_{patch}", "run", root, (
                "foamDictionary", "constant/polyMesh/boundary", "-entry",
                f"entry0/{patch}/type", "-set", kind)))
        return tuple(steps)

    def check_steps(self, ami: bool) -> tuple[Step, ...]:
        root = f"cases/{MERGED_CASE}"
        steps = [Step("merged_checkMesh", "run", root,
                      ("checkMesh", "-case", ".", "-allTopology", "-meshQuality"))]
        if ami:
            steps.append(Step("merged_amiWeights", "run", root, (
                "postProcess", "-case", ".", "-dict", AMI_WEIGHTS_DICT, "-constant")))
        return tuple(steps)


def import_cases(config: MachineProjectConfig) -> ImportCases:
    common = _common(config)
    merged = dict(common)
    patch_dict = create_patch_dict(config)
    if patch_dict is not None:
        merged["system/createPatchDict"] = patch_dict
    mesh = config.imported_mesh
    assert mesh is not None
    if mesh.ami_pairs:
        merged[AMI_WEIGHTS_DICT] = _ami_weights_dict()
    return ImportCases(dict(common), merged, retype(config), expected_patches(config))
