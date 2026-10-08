"""V2: dictionaries for every VAWT sub-case (no OpenFOAM needed)."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.issues import IssueSeverity
from openfoam.dictionary import OpenFOAMFileWriter
from tests.fixtures.vawt.drafts import preset_draft
from vawt.case_generator import (
    AMI_PATCHES,
    INTERFACE_OUTER,
    INTERFACE_ROTOR,
    VawtCaseGenerator,
    case_layout,
    cells_along,
    domain_grid,
    rotor_grid,
    single_mesh_level_offset,
    zone_cell_size,
)
from vawt.config import VawtProjectConfig

GEN = VawtCaseGenerator()


def full_draft(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    """Preset AMI draft with every optional feature on (features, relative layers)."""
    draft = preset_draft(tmp_path, **kwargs)
    draft["refinement"]["extract_features"] = True
    draft["layers"]["enabled"] = True
    draft["layers"]["min_thickness_m"] = 1e-4  # inactive while sizing is RELATIVE
    return draft


def config_of(draft: dict[str, Any]) -> VawtProjectConfig:
    return VawtProjectConfig.model_validate(draft)


@pytest.fixture
def ami(tmp_path: Path) -> VawtProjectConfig:
    return config_of(full_draft(tmp_path))


def entry(text: str, key: str) -> str:
    match = re.search(rf"^\s*{key}\s+(.+?);\s*$", text, re.MULTILINE)
    assert match, key
    return match.group(1)


# --- layout and paths --------------------------------------------------------------

def test_layout_for_each_mode(tmp_path: Path, ami: VawtProjectConfig) -> None:
    single = config_of({**full_draft(tmp_path), "rotating_zone": {
        **full_draft(tmp_path)["rotating_zone"], "interface": "CELL_ZONE"}})
    rotor_only = config_of(full_draft(tmp_path, include_domain=False))

    assert (case_layout(ami).sub_cases, case_layout(ami).final) == (
        ("outer", "rotor", "merged"), "merged")
    assert (case_layout(single).sub_cases, case_layout(single).final) == (("merged",), "merged")
    assert (case_layout(rotor_only).sub_cases, case_layout(rotor_only).final) == (
        ("rotor",), "rotor")


def test_ami_files(ami: VawtProjectConfig) -> None:
    files = set(GEN.render(ami))
    common = {"controlDict", "fvSchemes", "fvSolution", "meshQualityDict"}

    assert files == (
        {f"cases/outer/system/{n}" for n in common | {"blockMeshDict", "snappyHexMeshDict"}}
        | {f"cases/rotor/system/{n}" for n in common | {
            "blockMeshDict", "snappyHexMeshDict", "topoSetDict", "surfaceFeatureExtractDict"}}
        | {f"cases/merged/system/{n}" for n in common | {"createPatchDict"}}
    )


def test_every_file_is_under_a_sub_case_system_directory(ami: VawtProjectConfig) -> None:
    for path in GEN.render(ami):
        assert re.fullmatch(r"cases/(outer|rotor|merged)/system/[A-Za-z]+", path), path


def test_feature_dictionary_and_emesh_only_when_extracting(tmp_path: Path) -> None:
    draft = full_draft(tmp_path)
    draft["refinement"]["extract_features"] = False
    files = GEN.render(config_of(draft))

    assert "cases/rotor/system/surfaceFeatureExtractDict" not in files
    snappy = files["cases/rotor/system/snappyHexMeshDict"]
    assert ".eMesh" not in snappy
    assert entry(snappy, "explicitFeatureSnap") == "false"


# --- references between files -----------------------------------------------------------

def test_geometry_and_feature_references_agree(ami: VawtProjectConfig) -> None:
    files = GEN.render(ami)
    snappy = files["cases/rotor/system/snappyHexMeshDict"]
    extract = files["cases/rotor/system/surfaceFeatureExtractDict"]

    assert "rotor.stl\n    {\n        type triSurfaceMesh;\n        name rotor;" in snappy
    assert re.search(r"^rotor\.stl$", extract, re.MULTILINE)
    assert 'file "rotor.eMesh";' in snappy
    assert '#include "meshQualityDict"' in snappy


def test_interface_patches_and_ami_pair(ami: VawtProjectConfig) -> None:
    files = GEN.render(ami)
    create = files["cases/merged/system/createPatchDict"]

    assert f"{INTERFACE_OUTER}\n    {{\n        type    searchableCylinder;" in (
        files["cases/outer/system/snappyHexMeshDict"])
    assert f"{INTERFACE_ROTOR}\n    {{\n        type    searchableCylinder;" in (
        files["cases/rotor/system/snappyHexMeshDict"])
    first, second = AMI_PATCHES
    assert f"name {first};" in create and f"patches ({INTERFACE_OUTER});" in create
    assert f"name {second};" in create and f"patches ({INTERFACE_ROTOR});" in create
    assert create.count("type            cyclicAMI;") == 2


def test_rotor_only_case_names_its_cylinder_boundary_for_a_zone(tmp_path: Path) -> None:
    files = GEN.render(config_of(full_draft(tmp_path, include_domain=False)))

    snappy = files["cases/rotor/system/snappyHexMeshDict"]
    assert "rotatingZone\n    {\n        type    searchableCylinder;" in snappy
    assert INTERFACE_ROTOR not in snappy
    assert "cellZoneSet" in files["cases/rotor/system/topoSetDict"]


# --- patch names and geometry ----------------------------------------------------------

@pytest.mark.parametrize("axis,flow,inlet_face,axial_face", [
    ("z", "x", "(0 4 7 3)", "(0 3 2 1)"),
    ("z", "y", "(0 1 5 4)", "(0 3 2 1)"),
    ("x", "y", "(0 1 5 4)", "(0 4 7 3)"),
])
def test_domain_patches_on_the_right_faces(
    tmp_path: Path, axis: str, flow: str, inlet_face: str, axial_face: str
) -> None:
    draft = full_draft(tmp_path, axis=axis, flow_axis=flow)
    draft["domain"]["patches"] = {"inlet": "in_1", "axial_min": "ground"}
    block = GEN.render(config_of(draft))["cases/outer/system/blockMeshDict"]

    assert f"in_1 {{ type patch; faces ({inlet_face}); }}" in block
    assert f"ground {{ type patch; faces ({axial_face}); }}" in block


def test_cell_counts_tolerate_floating_point(ami: VawtProjectConfig) -> None:
    # 10.4 / (1.04 / 9) is 90.00000000000001 in floating point.
    assert cells_along(10.4, 1.04 / 9) == 90
    assert cells_along(4.32, 1.04 / 9) == 38
    assert domain_grid(ami).cells == (90, 27, 38)


def test_rotor_block_has_the_zone_cell_size_and_a_margin(ami: VawtProjectConfig) -> None:
    grid, zone = rotor_grid(ami), ami.rotating_zone

    for index in range(3):
        assert grid.spacing(index) == pytest.approx(zone.cell_size, rel=1e-12)
    assert grid.minimum[0] <= -zone.diameter / 2 - 2 * zone.cell_size + 1e-12
    assert grid.minimum[2] <= zone.axis_min - 2 * zone.cell_size + 1e-12
    assert grid.maximum[2] >= zone.axis_max + 2 * zone.cell_size - 1e-12


def test_cylinder_follows_the_rotor_axis(tmp_path: Path) -> None:
    config = config_of(full_draft(tmp_path, axis="x", flow_axis="y"))
    zone = config.rotating_zone
    snappy = GEN.render(config)["cases/outer/system/snappyHexMeshDict"]

    p1 = entry(snappy, "point1")
    assert p1 == f"({zone.axis_min!r} {zone.centre_u!r} {zone.centre_v!r})"


# --- layers ------------------------------------------------------------------------

def test_relative_layers(ami: VawtProjectConfig) -> None:
    snappy = GEN.render(ami)["cases/rotor/system/snappyHexMeshDict"]

    assert entry(snappy, "addLayers") == "true"
    assert entry(snappy, "relativeSizes") == "true"
    assert entry(snappy, "finalLayerThickness") == "0.3"
    assert "rotor { nSurfaceLayers 3; }" in snappy


def test_absolute_layers_use_the_v0_entries(tmp_path: Path) -> None:
    draft = full_draft(tmp_path)
    draft["layers"].update(sizing="ABSOLUTE", first_layer_thickness=2.46e-3,
                           min_thickness_m=1e-4)
    snappy = GEN.render(config_of(draft))["cases/rotor/system/snappyHexMeshDict"]

    assert entry(snappy, "relativeSizes") == "false"
    assert entry(snappy, "thicknessModel") == "firstAndExpansion"
    assert entry(snappy, "firstLayerThickness") == "0.00246"
    assert entry(snappy, "minThickness") == "0.0001"
    assert "finalLayerThickness" not in snappy


def test_outer_mesh_has_no_layers(ami: VawtProjectConfig) -> None:
    snappy = GEN.render(ami)["cases/outer/system/snappyHexMeshDict"]

    assert entry(snappy, "addLayers") == "false"
    assert "nSurfaceLayers" not in snappy


# --- single mesh (CELL_ZONE with a domain) -------------------------------------------------

def test_single_mesh_zone_and_level_offset(tmp_path: Path) -> None:
    draft = full_draft(tmp_path)
    draft["rotating_zone"]["interface"] = "CELL_ZONE"
    config = config_of(draft)
    files = GEN.render(config)
    snappy = files["cases/merged/system/snappyHexMeshDict"]

    k = single_mesh_level_offset(config)
    assert k == 2  # (D/9) / 2^2 = D/36 is the first size <= D/22
    assert zone_cell_size(config) == pytest.approx(1.04 / 36)
    assert "faceZone        rotatingZone;" in snappy
    assert "cellZone        rotating;" in snappy
    assert "cellZoneInside  inside;" in snappy
    assert f"level ({1 + k} {2 + k});" in snappy  # blade levels from the zone size
    assert f"level {2 + k};" in snappy  # feature level
    assert f"rotatingZone {{ mode inside; levels ((1e15 {k})); }}" in snappy
    assert "createPatchDict" not in " ".join(files)
    issue = next(i for i in GEN.issues(config) if i.code == "ZONE_CELL_SIZE_ADJUSTED")
    assert issue.severity is IssueSeverity.INFO


def test_single_mesh_uses_the_domain_mesh_point(tmp_path: Path) -> None:
    draft = full_draft(tmp_path)
    draft["rotating_zone"]["interface"] = "CELL_ZONE"
    config = config_of(draft)
    snappy = GEN.render(config)["cases/merged/system/snappyHexMeshDict"]

    assert entry(snappy, "locationInMesh") == config.domain.location_in_mesh.as_openfoam()


# --- profile and blocking states ---------------------------------------------------------

def test_foundation_profile_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    config = config_of({**full_draft(tmp_path), "openfoam_profile": "openfoam_foundation"})

    result = GEN.generate(tmp_path / "project", config)

    assert [i.code for i in result.issues] == ["VAWT_PROFILE_UNSUPPORTED"]
    assert result.issues[0].severity is IssueSeverity.BLOCKING
    assert result.written == () and result.layout is None
    assert not (tmp_path / "project" / "cases").exists()


def test_ami_without_domain_is_refused(tmp_path: Path) -> None:
    draft = full_draft(tmp_path, include_domain=False)
    draft["rotating_zone"]["interface"] = "AMI"

    assert GEN.render(config_of(draft)) == {}
    assert [i.code for i in GEN.issues(config_of(draft))] == ["AMI_REQUIRES_DOMAIN"]


# --- determinism and writing ---------------------------------------------------------------

def test_output_is_byte_identical_across_runs_and_directories(
    tmp_path: Path, ami: VawtProjectConfig
) -> None:
    first = GEN.generate(tmp_path / "a", ami)
    GEN.generate(tmp_path / "b", ami)
    again = GEN.generate(tmp_path / "a", ami)

    assert set(first.written) == set(GEN.render(ami))
    assert again.written == () and again.removed == ()
    for relative in GEN.render(ami):
        assert (tmp_path / "a" / relative).read_bytes() == (tmp_path / "b" / relative).read_bytes()
        assert (tmp_path / "a" / relative).read_text("utf-8").startswith(
            OpenFOAMFileWriter.GENERATED_HEADER)


def test_mode_change_removes_stale_generated_files_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    draft = full_draft(tmp_path)
    GEN.generate(project, config_of(draft))
    user_file = project / "cases" / "outer" / "system" / "myNotes"
    user_file.write_text("not generated", "utf-8")

    draft["rotating_zone"]["interface"] = "CELL_ZONE"
    result = GEN.generate(project, config_of(draft))

    assert "cases/outer/system/snappyHexMeshDict" in result.removed
    assert "cases/rotor/system/topoSetDict" in result.removed
    assert "cases/merged/system/createPatchDict" in result.removed
    assert user_file.is_file()
    remaining = {p.relative_to(project).as_posix() for p in (project / "cases").rglob("*")
                 if p.is_file()}
    assert remaining == set(GEN.render(config_of(draft))) | {"cases/outer/system/myNotes"}


# --- every configuration field: used, and only by the sub-cases spec 9.2 allows ------------

# Fields that do not change any V2 dictionary, with the reason.
NOT_USED_IN_V2 = {
    "project_name": "identification only",
    "geometry.source_path": "the STL artifact (V3) is what the dictionaries reference",
    "geometry.source_units": "applied when the STL artifact is written (V3)",
    "geometry.scale": "applied when the STL artifact is written (V3)",
    "geometry.rotation_deg.x": "applied when the STL artifact is written (V3)",
    "geometry.rotation_deg.y": "applied when the STL artifact is written (V3)",
    "geometry.rotation_deg.z": "applied when the STL artifact is written (V3)",
    "geometry.translation.x": "applied when the STL artifact is written (V3)",
    "geometry.translation.y": "applied when the STL artifact is written (V3)",
    "geometry.translation.z": "applied when the STL artifact is written (V3)",
    "quality.max_non_orthogonality": "acceptance limits are applied after checkMesh",
    "quality.max_boundary_skewness": "acceptance limits are applied after checkMesh",
    "quality.max_internal_skewness": "acceptance limits are applied after checkMesh",
    "quality.min_volume": "acceptance limits are applied after checkMesh",
    "quality.min_determinant": "acceptance limits are applied after checkMesh",
    "export.fluent_msh": "export (V7)",
    "layers.first_layer_thickness": "inactive: sizing is RELATIVE in this configuration",
    "layers.min_thickness_m": "inactive: sizing is RELATIVE in this configuration",
}
# Fields with no valid one-field change.
NOT_CHANGEABLE = {"schema_version": "only the current version is valid"}

# Sub-cases whose files a field may change (AMI layout); everything else must
# stay byte-identical, so the outer and rotor meshes are cached independently.
SCOPE = [
    ("domain.", {"outer"}),
    ("refinement.wake.", {"outer"}),
    ("refinement.interface_level", {"outer"}),
    ("rotating_zone.cell_size", {"rotor"}),
    ("rotating_zone.location_in_mesh.", {"rotor"}),
    ("refinement.", {"rotor"}),
    ("layers.", {"rotor"}),
    ("geometry.patch_name", {"rotor"}),
    ("rotating_zone.", {"outer", "rotor"}),  # the cylinder is in both meshes
    ("snappy_quality.", {"outer", "rotor", "merged"}),
    ("max_global_cells", {"outer", "rotor"}),
]


def leaves(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(data, dict):
        return [leaf for key, value in data.items()
                for leaf in leaves(value, f"{prefix}{key}.")]
    return [(prefix[:-1], data)]


def set_leaf(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    result = copy.deepcopy(data)
    *parents, leaf = path.split(".")
    target = result
    for key in parents:
        target = target[key]
    target[leaf] = value
    return result


def candidates(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [value + 1, value - 1]
    if isinstance(value, float):
        step = 0.013 * max(abs(value), 1e-3)
        return [value + step, value - step]
    if isinstance(value, str) and value.endswith(".stl"):
        return [value[:-4] + "_b.stl"]
    if isinstance(value, str):
        options = {"x", "y", "z", "AMI", "CELL_ZONE", "RELATIVE", "ABSOLUTE",
                   "openfoam_com", "openfoam_foundation", "m", "mm"}
        return [o for o in sorted(options) if o != value] + [value + "_b"]
    return []


def changed_sub_cases(base: dict[str, str], other: dict[str, str]) -> set[str]:
    paths = set(base) | set(other)
    return {p.split("/")[1] for p in paths if base.get(p) != other.get(p)}


def test_every_field_is_used_or_listed_and_stays_in_scope(tmp_path: Path) -> None:
    draft = config_of(full_draft(tmp_path)).model_dump(mode="json")
    base = GEN.render(config_of(draft))
    problems = []

    for path, value in leaves(draft):
        if path in NOT_CHANGEABLE:
            continue
        changes = []
        for candidate in candidates(value):
            try:
                other = config_of(set_leaf(draft, path, candidate))
            except ValidationError:
                continue
            changes.append(changed_sub_cases(base, GEN.render(other)))
        if not changes:
            problems.append(f"{path}: no valid change found")
        elif path in NOT_USED_IN_V2:
            if any(changes):
                problems.append(f"{path}: listed as not used but changes {changes}")
        elif not any(changes):
            problems.append(f"{path}: changes no dictionary and is not listed")
        elif not path.startswith(("rotor.", "rotating_zone.interface", "openfoam_profile")):
            allowed = next(scope for prefix, scope in SCOPE if path.startswith(prefix))
            for subs in changes:
                if subs - allowed:
                    problems.append(f"{path}: changes {sorted(subs)}, allowed {sorted(allowed)}")

    assert problems == []
