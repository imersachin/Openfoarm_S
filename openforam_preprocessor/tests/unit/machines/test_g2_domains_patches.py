"""G2 without OpenFOAM: imported-surface reader, generated cylinder, background
grids, patch typing, pre-meshing checks, domain cases and result checks."""

from __future__ import annotations

import copy
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from core.issues import Issue
from machines.case_generator import DomainCase, DomainCaseGenerator, part_case_name
from machines.config import (
    CylinderDomain,
    MachineProjectConfig,
    PatchType,
    StlFormat,
    StlSource,
)
from machines.domains import (
    BACKGROUND_MARGIN_CELLS,
    background_grid,
    box_grid,
    cylinder_surface,
    open_edge_count,
    read_imported,
    read_surface,
    read_surface_file,
)
from machines.mesh_checks import check_domain_case, check_domain_mesh
from machines.patches import (
    draft_box_patches,
    draft_cylinder_patches,
    draft_domain_patches,
    openfoam_type,
    suggest_region_types,
)
from machines.validation import check_config, check_imported, validate_machine
from tests.fixtures.machines import configs
from tests.fixtures.machines.configs import vec
from tests.fixtures.machines.g0.common import make_geometry
from vawt.config import Axis
from visualization.foam_reader import Patch

GEN = DomainCaseGenerator()


def source(path: Path, units: str = "m", **kwargs: Any) -> StlSource:
    return StlSource.model_validate({"source_path": str(path), "source_units": units,
                                     **kwargs})


def codes(issues: tuple[Issue, ...] | list[Issue]) -> list[str]:
    return [i.code for i in issues]


def stopping(data: dict[str, Any]) -> dict[str, str]:
    return {i.code: i.severity.value for i in validate_machine(data).issues
            if i.severity.value in ("ERROR", "BLOCKING")}


@pytest.fixture
def duct_dir(tmp_path: Path) -> Path:
    make_geometry.duct(tmp_path)
    return tmp_path


def duct_part(directory: Path, fmt: str, files: list[str]) -> dict[str, Any]:
    data = configs.duct(directory)
    part = data["domain"]["parts"][0]
    part["format"] = fmt
    part["files"] = [configs.stl(directory / f) for f in files]
    return data


# --- reader --------------------------------------------------------------------------------

def test_named_regions_are_read_per_solid(duct_dir: Path) -> None:
    read = read_surface_file(source(duct_dir / "duct_named.stl"), StlFormat.NAMED_REGIONS)

    assert not read.binary and read.error is None
    assert [(r.name, r.faces) for r in read.regions] == [("inlet", 2), ("outlet", 2),
                                                        ("wall", 8)]  # as G0 R2
    assert read.regions[0].area == pytest.approx(0.25)


def test_one_file_per_patch_names_regions_after_files(duct_dir: Path) -> None:
    for name in ("inlet", "outlet", "wall"):
        read = read_surface_file(source(duct_dir / f"{name}.stl"),
                                 StlFormat.ONE_FILE_PER_PATCH)
        assert [r.name for r in read.regions] == [name]


def test_binary_file_is_one_region_named_after_the_file(duct_dir: Path) -> None:
    read = read_surface_file(source(duct_dir / "duct_binary.stl"), StlFormat.NAMED_REGIONS)

    assert read.binary
    assert [(r.name, r.faces) for r in read.regions] == [("duct_binary", 12)]


def test_regions_are_transformed_to_metres(duct_dir: Path) -> None:
    plain = read_surface_file(source(duct_dir / "inlet.stl"), StlFormat.ONE_FILE_PER_PATCH)
    moved = read_surface_file(
        source(duct_dir / "inlet.stl", "mm", scale=2.0, translation=vec(1.0, 0.0, 0.0)),
        StlFormat.ONE_FILE_PER_PATCH)

    np.testing.assert_allclose(moved.regions[0].mesh.vertices,
                               plain.regions[0].mesh.vertices * 2e-3 + [1.0, 0.0, 0.0])


def test_unreadable_and_empty_files(tmp_path: Path) -> None:
    (tmp_path / "empty.stl").write_text("solid empty\nendsolid empty\n")

    missing = read_surface_file(source(tmp_path / "missing.stl"), StlFormat.NAMED_REGIONS)
    empty = read_surface_file(source(tmp_path / "empty.stl"), StlFormat.NAMED_REGIONS)

    assert missing.error and not missing.regions
    assert empty.error == "no triangles found"


def test_open_edges_of_the_union(duct_dir: Path) -> None:
    def meshes(names: list[str]) -> list[trimesh.Trimesh]:
        return [r.mesh for n in names for r in read_surface_file(
            source(duct_dir / f"{n}.stl"), StlFormat.ONE_FILE_PER_PATCH).regions]

    assert open_edge_count(meshes(["inlet", "outlet", "wall"])) == 0
    assert open_edge_count(meshes(["inlet", "wall"])) == 4  # G0 R2 python_checks


def test_read_imported_owners(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.francis(tmp_path))

    surfaces = read_imported(config)

    assert [(s.owner, s.name, s.closed) for s in surfaces] == [
        ("rotating_zones.0", "runner", True), ("domain.parts.0", "casing", True),
        ("domain.parts.1", "guide", True), ("domain.parts.2", "draft", True)]
    assert [r.name for r in surfaces[2].regions] == ["guide_in", "guide_wall", "guide_out",
                                                     "vanes"]


# --- generated cylinder and grids -------------------------------------------------------------

@pytest.mark.parametrize("axis", list(Axis))
def test_cylinder_surface_is_closed_and_faces_out(axis: Axis) -> None:
    domain = CylinderDomain(kind="CYLINDER", axis=axis, centre_u=0.5, centre_v=-1.0,
                            axis_min=-2.0, axis_max=5.0, diameter=4.0, cell_size=0.1,
                            location_in_mesh=vec(0, 0, 0), segments=48)
    regions = cylinder_surface(domain)
    union = trimesh.util.concatenate([m for _, m in regions])
    union.merge_vertices()

    assert [(name, len(m.faces)) for name, m in regions] == [
        ("axis_min", 48), ("axis_max", 48), ("side", 96)]
    assert union.is_watertight
    polygon = 0.5 * 48 * 2.0 ** 2 * math.sin(2 * math.pi / 48)
    assert union.volume == pytest.approx(polygon * 7.0)  # positive: normals point out
    low = dict(regions)["axis_min"]
    assert np.allclose(low.face_normals[:, axis.position], -1.0)


def test_background_grid_margin() -> None:
    grid = background_grid(np.array([0.0, 0.0, 0.0]), np.array([1.0, 2.0, 0.5]), 0.1)

    assert grid.minimum == pytest.approx((-0.25, -0.25, -0.25))
    assert grid.maximum == pytest.approx((1.25, 2.25, 0.75))
    assert grid.cells == (15, 25, 10)
    assert BACKGROUND_MARGIN_CELLS == 2.5
    # The surface lies half-way between background planes.
    assert (0.0 - grid.minimum[0]) / grid.spacing(0) == pytest.approx(2.5)


def test_box_grid_is_the_box(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.pole(tmp_path))
    assert config.domain is not None and config.domain.kind == "BOX"

    grid = box_grid(config.domain)  # type: ignore[arg-type]

    assert grid.cells == (90, 27, 38)


# --- patch typing ---------------------------------------------------------------------------

def test_openfoam_types() -> None:
    assert {t: openfoam_type(t) for t in PatchType} == {
        PatchType.INLET: "patch", PatchType.OUTLET: "patch", PatchType.WALL: "wall",
        PatchType.ROTATING_WALL: "wall", PatchType.SLIP: "patch"}


def test_draft_patches() -> None:
    box = draft_box_patches(Axis.Y)
    assert [(p.name, p.type, p.source.ref) for p in box] == [
        ("inlet", PatchType.INLET, "y_min"), ("outlet", PatchType.OUTLET, "y_max"),
        ("x_min", PatchType.SLIP, "x_min"), ("x_max", PatchType.SLIP, "x_max"),
        ("z_min", PatchType.SLIP, "z_min"), ("z_max", PatchType.SLIP, "z_max")]
    assert [(p.name, p.source.ref) for p in draft_cylinder_patches()] == [
        ("inlet", "axis_min"), ("outlet", "axis_max"), ("side", "side")]


def test_box_draft_needs_the_flow_axis(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.pole(tmp_path))
    assert config.domain is not None and config.domain.kind == "BOX"

    with pytest.raises(ValueError):
        draft_domain_patches(config.domain, None)  # type: ignore[arg-type]
    assert draft_domain_patches(config.domain, Axis.X) == draft_box_patches(Axis.X)  # type: ignore[arg-type]


def test_region_names_are_only_suggested() -> None:
    assert suggest_region_types(["Inlet", "outlet", "casing_wall", "walls"]) == {
        "Inlet": PatchType.INLET, "outlet": PatchType.OUTLET, "walls": PatchType.WALL}


def test_g2_draft_patches_pass_validation(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path)
    data["patches"] = [data["patches"][0], *(p.model_dump(mode="json")
                                             for p in draft_cylinder_patches())]

    assert stopping(data) == {}


# --- checks on the imported files (before meshing) ---------------------------------------------

def test_g0_sets_pass_the_imported_checks(tmp_path: Path) -> None:
    assert stopping(configs.duct(tmp_path / "duct")) == {}
    assert stopping(configs.francis(tmp_path / "francis")) == {}
    assert stopping(duct_part(tmp_path / "named", "NAMED_REGIONS", ["duct_named.stl"])) == {}


def test_binary_stl_as_named_regions_is_an_error(duct_dir: Path) -> None:
    found = stopping(duct_part(duct_dir, "NAMED_REGIONS", ["duct_binary.stl"]))

    assert found["BINARY_STL_AS_NAMED_REGIONS"] == "ERROR"


def test_binary_stl_given_one_file_per_patch_is_accepted(duct_dir: Path) -> None:
    # A binary file named after its patch loses nothing.
    data = duct_part(duct_dir, "ONE_FILE_PER_PATCH", ["duct_binary.stl"])
    data["patches"] = [configs.patch("duct", "WALL", "REGION", "duct_binary")]

    assert "BINARY_STL_AS_NAMED_REGIONS" not in stopping(data)


def test_open_union_is_blocking(duct_dir: Path) -> None:
    data = duct_part(duct_dir, "ONE_FILE_PER_PATCH", ["inlet.stl", "wall.stl"])
    issues = [i for i in validate_machine(data).issues if i.code == "IMPORTED_SURFACE_OPEN"]

    assert [(i.severity.value, i.details["open_edges"]) for i in issues] == [("BLOCKING", 4)]


def test_unreadable_file_is_blocking(duct_dir: Path) -> None:
    data = duct_part(duct_dir, "ONE_FILE_PER_PATCH", ["inlet.stl", "outlet.stl", "nope.stl"])

    assert stopping(data)["IMPORTED_FILE_UNREADABLE"] == "BLOCKING"


def test_region_names_invalid_reserved_or_duplicate(duct_dir: Path) -> None:
    shutil.copy(duct_dir / "wall.stl", duct_dir / "my-wall.stl")
    shutil.copy(duct_dir / "wall.stl", duct_dir / "background.stl")
    invalid = duct_part(duct_dir, "ONE_FILE_PER_PATCH", ["inlet.stl", "outlet.stl",
                                                         "my-wall.stl"])
    reserved = duct_part(duct_dir, "ONE_FILE_PER_PATCH", ["inlet.stl", "outlet.stl",
                                                          "background.stl"])
    (duct_dir / "copy").mkdir()
    shutil.copy(duct_dir / "wall.stl", duct_dir / "copy" / "wall.stl")
    twice = duct_part(duct_dir, "ONE_FILE_PER_PATCH",
                      ["inlet.stl", "outlet.stl", "wall.stl", "copy/wall.stl"])

    assert stopping(invalid)["REGION_NAME_INVALID"] == "BLOCKING"
    assert stopping(reserved)["REGION_NAME_RESERVED"] == "BLOCKING"
    assert stopping(twice)["REGION_NAME_DUPLICATE"] == "BLOCKING"


def test_region_names_are_unique_across_parts(duct_dir: Path) -> None:
    data = configs.duct(duct_dir)
    second = copy.deepcopy(data["domain"]["parts"][0])
    second["name"] = "duct2"
    data["domain"]["parts"].append(second)

    issues = [i for i in validate_machine(data).issues if i.code == "REGION_NAME_DUPLICATE"]

    assert sorted(i.details["region"] for i in issues) == ["inlet", "outlet", "wall"]
    assert issues[0].details["owners"] == ["domain.parts.0", "domain.parts.1"]


def test_named_regions_are_now_checked(tmp_path: Path) -> None:
    francis = configs.francis(tmp_path)
    francis["patches"][2]["source"]["ref"] = "walls"  # casing_wall misspelt

    found = stopping(francis)

    assert found["PATCH_SOURCE_UNKNOWN"] == "BLOCKING"
    assert found["REGION_WITHOUT_PATCH"] == "BLOCKING"  # casing_wall


def test_joint_between_regions_of_one_part(tmp_path: Path) -> None:
    francis = configs.francis(tmp_path)
    francis["joints"][0] = {"first": "casing_out", "second": "inlet"}
    francis["patches"] = [p for p in francis["patches"] if p["name"] != "inlet"]
    francis["patches"].append(configs.patch("inlet", "INLET", "REGION", "guide_in"))

    assert stopping(francis)["JOINT_SAME_PART"] == "BLOCKING"


def test_part_mesh_point_outside_its_surface(duct_dir: Path) -> None:
    data = configs.duct(duct_dir)
    data["domain"]["parts"][0]["location_in_mesh"] = vec(-0.5013, 0.0113, 0.0113)

    assert stopping(data)["PART_POINT_OUTSIDE"] == "BLOCKING"


def test_imported_zone_mesh_point_outside_its_surface(tmp_path: Path) -> None:
    francis = configs.francis(tmp_path)
    francis["rotating_zones"][0]["location_in_mesh"] = vec(0.0113, 0.0113, 0.5)

    assert stopping(francis)["INNER_POINT_OUTSIDE_ZONE"] == "BLOCKING"


def test_mesh_points_on_background_cell_faces(tmp_path: Path, duct_dir: Path) -> None:
    hawt = configs.hawt(tmp_path / "hawt")
    hawt["domain"]["cell_size"] = 0.125  # background x from -2.3125: x = -1.3125 is a plane
    hawt["domain"]["location_in_mesh"]["x"] = -1.3125
    duct = configs.duct(duct_dir)
    config = MachineProjectConfig.model_validate(duct)
    (surface,) = read_imported(config)
    union = surface.union()
    grid = background_grid(union.bounds[0], union.bounds[1], 0.05)
    duct["domain"]["parts"][0]["location_in_mesh"]["y"] = grid.minimum[1] + 9 * grid.spacing(1)

    assert stopping(hawt)["MESH_POINT_ON_CELL_FACE"] == "ERROR"
    assert stopping(duct)["MESH_POINT_ON_CELL_FACE"] == "ERROR"
    box = configs.pole(tmp_path / "pole")
    box["domain"]["location_in_mesh"]["x"] = -3.12 + 10 * (10.4 / 90)
    assert "MESH_POINT_ON_CELL_FACE" in codes(check_config(MachineProjectConfig.model_validate(
        box)))


def test_check_imported_alone(duct_dir: Path) -> None:
    config = MachineProjectConfig.model_validate(
        duct_part(duct_dir, "NAMED_REGIONS", ["duct_binary.stl"]))

    assert "BINARY_STL_AS_NAMED_REGIONS" in codes(check_imported(config, read_imported(config)))


# --- domain cases ---------------------------------------------------------------------------

def test_box_case(tmp_path: Path) -> None:
    (case,) = GEN.render(MachineProjectConfig.model_validate(configs.pole(tmp_path)))
    block = case.dictionaries["system/blockMeshDict"]

    assert case.name == "domain" and not case.cut and not case.surfaces
    assert "inlet { type patch; faces ((0 4 7 3)); }" in block
    assert "side_z_max { type patch; faces ((4 5 6 7)); }" in block
    assert "(90 27 38)" in block
    assert case.expected == {"inlet": "patch", "outlet": "patch", "side_y_min": "patch",
                             "side_y_max": "patch", "side_z_min": "patch",
                             "side_z_max": "patch"}
    assert set(case.dictionaries) == {"system/controlDict", "system/fvSchemes",
                                      "system/fvSolution", "system/meshQualityDict",
                                      "system/blockMeshDict"}


def test_box_face_without_patch_keeps_the_face_name(tmp_path: Path) -> None:
    data = configs.pole(tmp_path)
    data["patches"] = [p for p in data["patches"] if p["name"] != "side_y_min"]

    (case,) = GEN.render(MachineProjectConfig.model_validate(data))

    assert "y_min { type patch;" in case.dictionaries["system/blockMeshDict"]
    assert case.other == ("y_min",)


def test_cylinder_case(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path)
    data["patches"][3]["type"] = "WALL"  # side
    (case,) = GEN.render(MachineProjectConfig.model_validate(data))
    stl = case.surfaces["constant/triSurface/domain.stl"].decode()
    snappy = case.dictionaries["system/snappyHexMeshDict"]

    assert case.cut and case.expected == {"inlet": "patch", "outlet": "patch",
                                          "side": "wall"}
    assert [line for line in stl.splitlines() if line.startswith("solid")] == [
        "solid inlet", "solid outlet", "solid side"]
    assert "side { level (0 0); patchInfo { type wall; } }" in snappy
    assert "inlet { name inlet; }" in snappy
    assert "locationInMesh      (-1.4313 0.0113 0.0213);" in snappy
    assert "explicitFeatureSnap true;" in snappy
    assert "background { type patch;" in case.dictionaries["system/blockMeshDict"]
    assert 'file "domain.eMesh";' in snappy
    assert "domain.stl" in case.dictionaries["system/surfaceFeatureExtractDict"]


def test_imported_cases_one_per_part(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.francis(tmp_path))

    cases = GEN.render(config, read_imported(config))

    assert [c.name for c in cases] == [part_case_name(p) for p in ("casing", "guide", "draft")]
    guide = cases[1]
    stl = guide.surfaces["constant/triSurface/guide.stl"].decode()
    assert [line for line in stl.splitlines() if line.startswith("solid")] == [
        "solid guide_in", "solid guide_wall", "solid guide_out", "solid vanes"]
    assert guide.expected == {"guide_wall": "wall", "vanes": "wall"}
    assert guide.other == ("guide_in", "guide_out")  # joint regions: plain patches until G5
    assert cases[0].unplaced == ()


def test_imported_cases_need_the_surfaces(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        GEN.render(MachineProjectConfig.model_validate(configs.francis(tmp_path)))


def test_patch_rename_changes_only_names(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path)
    renamed = copy.deepcopy(data)
    renamed["patches"][1]["name"] = "upstream"
    (a,) = GEN.render(MachineProjectConfig.model_validate(data))
    (b,) = GEN.render(MachineProjectConfig.model_validate(renamed))

    assert (a.surfaces["constant/triSurface/domain.stl"].replace(b"solid inlet",
                                                                  b"solid upstream")
            .replace(b"endsolid inlet", b"endsolid upstream")
            == b.surfaces["constant/triSurface/domain.stl"])


def test_write_is_deterministic_and_incremental(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.hawt(tmp_path / "geometry"))
    cases = GEN.render(config)

    first = GEN.write(tmp_path / "project", cases)

    assert set(first) == {f"cases/domain/{p}" for p in [
        *cases[0].dictionaries, *cases[0].surfaces]}
    assert GEN.write(tmp_path / "project", GEN.render(config)) == ()


# --- result checks ---------------------------------------------------------------------------

def patches(**faces: tuple[str, int]) -> list[Patch]:
    return [Patch(name, kind, n, 0) for name, (kind, n) in faces.items()]


CASE = DomainCase("domain", "domain", {}, expected={"inlet": "patch", "outlet": "patch",
                                                    "wall": "wall"},
                  other=("joint_a",), cut=True)


def test_result_checks_pass_on_the_expected_mesh() -> None:
    assert check_domain_mesh(CASE, patches(inlet=("patch", 5), outlet=("patch", 5),
                                           wall=("wall", 50))) == ()


def test_result_checks_find_each_fault() -> None:
    found = check_domain_mesh(CASE, patches(
        inlet=("patch", 0), wall=("patch", 50), background=("patch", 12),
        joint_a=("patch", 3), duct=("patch", 7)))

    assert [(i.code, i.severity.value, i.details.get("patch")) for i in found] == [
        ("DOMAIN_PATCH_EMPTY", "ERROR", "inlet"),
        ("DOMAIN_PATCH_MISSING", "ERROR", "outlet"),
        ("DOMAIN_PATCH_TYPE_WRONG", "ERROR", "wall"),
        ("WRONG_REGION_KEPT", "ERROR", None),
        ("DOMAIN_PATCH_UNEXPECTED", "WARNING", None)]


def test_empty_background_is_fine_and_unplaced_patches_are_missing() -> None:
    case = DomainCase("domain_duct", "domain.parts.0", {}, cut=True, unplaced=("inlet",))

    found = check_domain_mesh(case, patches(background=("patch", 0)))

    assert [(i.code, i.details["patch"]) for i in found] == [("DOMAIN_PATCH_MISSING", "inlet")]


def test_unreadable_mesh(tmp_path: Path) -> None:
    assert codes(check_domain_case(tmp_path, CASE)) == ["DOMAIN_MESH_UNREADABLE"]


def test_surface_info_union_and_flags(duct_dir: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.duct(duct_dir))
    (surface,) = read_imported(config)
    part = config.domain.parts[0]  # type: ignore[union-attr]

    assert surface.readable and surface.closed and surface.open_edges == 0
    assert surface.union().is_watertight
    again = read_surface("x", "duct", part)
    assert again.owner == "x"
    assert [(r.name, r.faces) for r in again.regions] == [(r.name, r.faces)
                                                          for r in surface.regions]
