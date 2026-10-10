"""G3 without OpenFOAM: interface names, zone surfaces, the machine cases,
meshing order, placement and layer checks, and the assembly result checks."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import pytest
import trimesh

from core.issues import Issue
from machines.assembly import (
    MachineCaseGenerator,
    MachineCases,
    meshing_steps,
)
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.mesh_checks import (
    cell_zone_names,
    check_assembly,
    parse_ami_weights,
)
from machines.validation import check_layers, load_bodies, validate_machine
from machines.vawt_migration import NotVawtConvertible, from_vawt, to_vawt
from machines.zones import (
    generated_names,
    interfaces,
    split_patch,
    zone_grid,
    zone_surface,
)
from tests.fixtures.machines import configs
from tests.fixtures.machines.configs import patch, vec
from tests.fixtures.machines.g0.common import make_geometry
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import VawtProjectConfig

GEN = MachineCaseGenerator()


def config_of(data: dict[str, Any]) -> MachineProjectConfig:
    return MachineProjectConfig.model_validate(data)


def render(data: dict[str, Any]) -> MachineCases:
    config = config_of(data)
    meshes, _ = load_bodies(config)
    return GEN.render(config, read_imported(config), meshes)


def found(data: dict[str, Any]) -> list[tuple[str, str]]:
    return [(i.code, i.severity.value) for i in validate_machine(data).issues
            if i.code != "MULTIPLE_COMPONENTS"]


# --- names and surfaces -------------------------------------------------------------------

def test_interface_and_split_names(tmp_path: Path) -> None:
    split = config_of(configs.pole(tmp_path / "split", motion="SPLIT"))
    annular = config_of(configs.pole(tmp_path / "hole", motion="STATIONARY",
                                     hole_diameter=0.16))

    (face,) = interfaces(split.rotating_zones[0])
    assert (face.stationary, face.rotating) == ("rotating_outer_stat", "rotating_outer_rot")
    assert [(f.stationary, f.rotating) for f in interfaces(annular.rotating_zones[0])] == [
        ("rotating_outer_stat", "rotating_outer_rot"),
        ("rotating_inner_stat", "rotating_inner_rot")]
    assert split_patch(split.bodies[1]) == "pole_rotating"
    assert set(generated_names(split)) == {
        "rotating_outer_stat", "rotating_outer_rot", "rotating_outer_stat_src",
        "rotating_outer_rot_src", "pole_rotating"}


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("hole", [None, 0.4])
def test_zone_surface_is_closed_and_faces_out(axis: str, hole: float | None) -> None:
    zone = config_of({
        "project_name": "Z", "machine": "CUSTOM",
        "rotating_zones": [{"name": "z", "axis": axis, "cell_size": 0.1,
                            "location_in_mesh": vec(0, 0, 0),
                            "shape": {"kind": "CYLINDER", "centre_u": 0.3, "centre_v": -0.2,
                                      "axis_min": -1.0, "axis_max": 2.0, "diameter": 2.0,
                                      "hole_diameter": hole, "segments": 48}}],
    }).rotating_zones[0]

    regions = zone_surface(zone)
    union = trimesh.util.concatenate([m for _, m in regions])
    union.merge_vertices()

    assert [name for name, _ in regions] == (["outer"] if hole is None else ["outer", "inner"])
    assert union.is_watertight
    polygon = 0.5 * 48 * math.sin(2 * math.pi / 48)  # area of a unit-radius 48-gon
    expected = polygon * (1.0 - (hole / 2) ** 2 if hole else 1.0) * 3.0
    assert union.volume == pytest.approx(expected)  # positive: normals point out


def test_zone_grid_margin(tmp_path: Path) -> None:
    zone = config_of(configs.hawt(tmp_path)).rotating_zones[0]

    grid = zone_grid(zone)

    assert grid.minimum[0] == pytest.approx(-0.12 - 2.5 * 0.025)
    assert 0.024 < grid.spacing(0) <= 0.025  # whole cells, none larger than the zone's


# --- cases --------------------------------------------------------------------------------------

def test_hawt_cases(tmp_path: Path) -> None:
    cases = render(configs.hawt(tmp_path))
    domain, zone = cases.domain[0], cases.zones[0]
    snappy = domain.dictionaries["system/snappyHexMeshDict"]
    zone_snappy = zone.dictionaries["system/snappyHexMeshDict"]

    assert [c.name for c in cases.all] == ["domain", "zone_rotating", "merged"]
    assert set(domain.surfaces) == {"constant/triSurface/domain.stl",
                                    "constant/triSurface/rotating_stat.stl"}
    assert "rotating_outer_stat_src { level (1 1); patchInfo { type patch; } }" in snappy
    assert domain.expected == {"inlet": "patch", "outlet": "patch", "side": "patch",
                               "rotating_outer_stat_src": "patch"}
    assert "rotor { level (1 2); patchInfo { type wall; } }" in zone_snappy
    assert "locationInMesh      (0.0013 0.3013 0.0113);" in zone_snappy
    assert zone.expected == {"rotating_outer_rot_src": "patch", "rotor": "wall"}
    assert "name    rotating;" in zone.dictionaries["system/topoSetDict"]
    merged = cases.merged
    create = merged.dictionaries["system/createPatchDict"]
    assert "name rotating_outer_stat;" in create and "patches (rotating_outer_stat_src);" in create
    assert "neighbourPatch  rotating_outer_rot;" in create
    assert "type            AMIWeights;" in merged.dictionaries["system/amiWeightsDict"]
    assert merged.expected == {"inlet": "patch", "outlet": "patch", "side": "patch",
                               "rotor": "wall", "rotating_outer_stat": "cyclicAMI",
                               "rotating_outer_rot": "cyclicAMI"}


def test_meshing_steps(tmp_path: Path) -> None:
    steps = meshing_steps(render(configs.hawt(tmp_path)))

    assert [s.name for s in steps] == [
        "domain_blockMesh", "domain_surfaceFeatureExtract", "domain_snappyHexMesh",
        "zone_rotating_blockMesh", "zone_rotating_surfaceFeatureExtract",
        "zone_rotating_snappyHexMesh", "zone_rotating_topoSet", "merged_copy",
        "merged_add_zone_rotating", "merged_createPatch", "merged_checkMesh",
        "merged_amiWeights"]
    add = steps[8]
    assert (add.case, add.argv) == ("cases/merged",
                                    ("mergeMeshes", "-overwrite", ".", "../zone_rotating"))
    assert steps[7].source == "cases/domain"
    assert steps[-1].argv == ("postProcess", "-case", ".", "-dict", "system/amiWeightsDict",
                              "-constant")


def test_split_pole_has_the_same_cell_size_on_both_sides(tmp_path: Path) -> None:
    # Domain cells D/9, zone cells D/22: one level apart (log2 2.44 -> 1), F3.
    cases = render(configs.pole(tmp_path, motion="SPLIT"))

    assert "pole { level (2 3); patchInfo { type wall; } }" in (
        cases.domain[0].dictionaries["system/snappyHexMeshDict"])
    assert "pole_rotating { level (1 2); patchInfo { type wall; } }" in (
        cases.zones[0].dictionaries["system/snappyHexMeshDict"])
    assert cases.zones[0].expected["pole_rotating"] == "wall"
    assert cases.merged.expected["pole"] == cases.merged.expected["pole_rotating"] == "wall"


def test_annular_zone_hole_levels(tmp_path: Path) -> None:
    # As G0 R5 hole: level 2 on the zone side, 3 on the domain side.
    cases = render(configs.pole(tmp_path, motion="STATIONARY", hole_diameter=0.16))

    assert "rotating_inner_rot_src { level (2 2);" in (
        cases.zones[0].dictionaries["system/snappyHexMeshDict"])
    assert "rotating_inner_stat_src { level (3 3);" in (
        cases.domain[0].dictionaries["system/snappyHexMeshDict"])
    assert "pole_rotating" not in cases.merged.expected
    assert [f.region for f in cases.interfaces] == ["outer", "inner"]


def test_layers_per_body(tmp_path: Path) -> None:
    data = configs.pole(tmp_path, motion="SPLIT")
    data["bodies"][0]["layers"] = {"enabled": True, "count": 4, "expansion_ratio": 1.1}
    data["bodies"][1]["layers"] = {"enabled": True, "count": 2}

    cases = render(data)
    zone = cases.zones[0].dictionaries["system/snappyHexMeshDict"]
    domain = cases.domain[0].dictionaries["system/snappyHexMeshDict"]

    assert "addLayers       true;" in zone and "addLayers       true;" in domain
    assert "blades { nSurfaceLayers 4; expansionRatio 1.1;" in zone
    assert "pole_rotating { nSurfaceLayers 2;" in zone
    assert "pole { nSurfaceLayers 2;" in domain


def test_cases_are_deterministic_and_written_once(tmp_path: Path) -> None:
    data = configs.pole(tmp_path / "geometry", motion="SPLIT")
    first, second = render(data), render(copy.deepcopy(data))

    assert first == second
    assert GEN.write(tmp_path / "project", first)
    assert GEN.write(tmp_path / "project", second) == ()


def test_stationary_body_and_zone_go_to_their_imported_part(tmp_path: Path) -> None:
    data = configs.duct(tmp_path)
    make_geometry.closed_cylinder(0.02, -0.05, 0.05).export(tmp_path / "rod.stl")
    data["bodies"] = [{"name": "rod", "source": configs.stl(tmp_path / "rod.stl"),
                       "motion": "STATIONARY"}]
    data["bodies"][0]["source"]["translation"] = vec(1.4096, 0.513, 0.0)
    data["patches"].append(patch("rod", "WALL", "BODY", "rod"))

    cases = render(data)

    assert [c.name for c in cases.domain] == ["domain_duct"]
    assert set(cases.domain[0].surfaces) == {"constant/triSurface/duct.stl",
                                             "constant/triSurface/rotating_stat.stl",
                                             "constant/triSurface/rod.stl"}


def test_imported_zones_are_meshed_from_g5(tmp_path: Path) -> None:
    # G5 changed this test: it asserted the G3 refusal ("meshed from G5").
    cases = render(configs.francis(tmp_path))

    assert [c.name for c in cases.zones] == ["zone_runner"]


def test_bodies_must_be_loaded(tmp_path: Path) -> None:
    config = config_of(configs.hawt(tmp_path))
    with pytest.raises(ValueError, match="rotor"):
        GEN.render(config, (), {})


# --- checks before meshing ----------------------------------------------------------------------

def test_generated_names_are_reserved(tmp_path: Path) -> None:
    data = configs.pole(tmp_path, motion="SPLIT")
    data["patches"][4]["name"] = "pole_rotating"
    data["patches"][5]["name"] = "rotating_outer_stat"

    reserved = [i for i in validate_machine(data).issues if i.code == "RESERVED_PATCH_NAME"]

    assert sorted(i.details["name"] for i in reserved) == ["pole_rotating",
                                                           "rotating_outer_stat"]
    assert all(i.severity.value == "BLOCKING" for i in reserved)


def _duct_with_rod(directory: Path, translation: dict[str, float],
                   motion: str = "STATIONARY") -> dict[str, Any]:
    data = configs.duct(directory)
    make_geometry.closed_cylinder(0.02, -0.4, 0.4).export(directory / "rod.stl")  # along z
    body: dict[str, Any] = {"name": "rod", "motion": motion,
                            "source": {**configs.stl(directory / "rod.stl"),
                                       "translation": translation}}
    if motion != "STATIONARY":
        body["zone"] = "rotating"
    data["bodies"] = [body]
    data["patches"].append(patch("rod", "WALL" if motion == "STATIONARY" else "ROTATING_WALL",
                                 "BODY", "rod"))
    return data


def test_body_through_an_imported_wall_warns(tmp_path: Path) -> None:
    # A vertical rod through the duct's top and bottom walls, near the inlet.
    issues = [i for i in validate_machine(_duct_with_rod(tmp_path, vec(0.3, 0.11, 0.0))).issues
              if i.code == "BODY_CROSSES_DOMAIN_BOUNDARY"]

    assert [(i.severity.value, i.details["regions"], i.details["patches"]) for i in issues] == [
        ("WARNING", ["wall"], ["wall"])]


def test_body_through_an_imported_inlet_is_an_error(tmp_path: Path) -> None:
    data = _duct_with_rod(tmp_path, vec(0.3, 0.11, 0.0))
    data["patches"][2]["type"] = "INLET"  # the wall region typed as an inlet

    assert ("BODY_CROSSES_INLET_OUTLET", "ERROR") in found(data)


def test_body_outside_the_imported_domain(tmp_path: Path) -> None:
    assert ("BODY_OUTSIDE_DOMAIN", "BLOCKING") in found(
        _duct_with_rod(tmp_path, vec(5.0, 5.0, 0.0)))


def test_zone_outside_the_imported_domain(tmp_path: Path) -> None:
    data = configs.duct(tmp_path)
    data["rotating_zones"][0]["shape"]["diameter"] = 0.8

    assert ("ZONE_OUTSIDE_DOMAIN", "BLOCKING") in found(data)


def test_no_imported_surface_is_left_unchecked(tmp_path: Path) -> None:
    # G5 changed this test: imported zones are checked now, so the INFO is gone.
    assert ("IMPORTED_SURFACES_NOT_CHECKED", "INFO") not in found(
        configs.francis(tmp_path / "f"))
    assert found(configs.duct(tmp_path / "d")) == []


def _layered(tmp_path: Path, **layers: Any) -> dict[str, Any]:
    data = configs.hawt(tmp_path)
    data["bodies"][0]["layers"] = {"enabled": True, **layers}
    return data


def test_layer_checks_per_body(tmp_path: Path) -> None:
    # Rotor cells: zone 0.025 m / 2^2 = 6.25 mm.
    thick = _layered(tmp_path / "a", sizing="ABSOLUTE", first_layer_thickness=0.01,
                     min_thickness_m=1e-4)
    thin = _layered(tmp_path / "b", sizing="ABSOLUTE", first_layer_thickness=1e-5,
                    min_thickness_m=1e-6)
    too_min = _layered(tmp_path / "c", min_thickness=1.0, final_layer_thickness=0.3)

    assert {c for c, _ in found(thick)} >= {"ABSOLUTE_LAYER_TOO_THICK"}
    assert ("LAYERS_TOO_THIN_FOR_CELLS", "WARNING") in found(thin)
    assert ("LAYER_MIN_THICKNESS_TOO_LARGE", "ERROR") in found(too_min)
    assert found(_layered(tmp_path / "d")) == []


def test_one_layer_sizing_per_mesh(tmp_path: Path) -> None:
    data = configs.pole(tmp_path, motion="SPLIT")
    data["bodies"][0]["layers"] = {"enabled": True}
    data["bodies"][1]["layers"] = {"enabled": True, "sizing": "ABSOLUTE",
                                   "first_layer_thickness": 1e-3, "min_thickness_m": 1e-4}

    mixed = [i for i in check_layers(config_of(data)) if i.code == "LAYER_SIZING_MIXED"]

    assert [i.details["case"] for i in mixed] == ["zone 'rotating'"]


def test_vawt_projects_keep_their_own_layer_checks(tmp_path: Path) -> None:
    raw = preset_draft(tmp_path)
    raw["layers"] = {**raw["layers"], "enabled": True, "sizing": "ABSOLUTE",
                     "min_thickness_m": 1e-6, "first_layer_thickness": 1.0}
    machine = from_vawt(VawtProjectConfig.model_validate(raw))

    assert check_layers(machine) == ()  # check_vawt reports them
    assert ("ABSOLUTE_LAYER_TOO_THICK", "ERROR") in found(machine.model_dump(mode="json"))


def test_to_vawt_refuses_zone_segments(tmp_path: Path) -> None:
    machine = from_vawt(VawtProjectConfig.model_validate(preset_draft(tmp_path)))
    zone = machine.rotating_zones[0]
    changed = machine.model_copy(update={"rotating_zones": (zone.model_copy(update={
        "shape": zone.shape.model_copy(update={"segments": 48})}),)})

    with pytest.raises(NotVawtConvertible):
        to_vawt(changed)


# --- result checks on the assembled mesh ---------------------------------------------------------

LOG = """AMI: Creating AMI for source:rotating_outer_stat and target:rotating_outer_rot
AMI: Patch source faces: 792
AMI: Patch target faces: 4988
AMI: Patch source sum(weights) min:0.99999999 max:1.0110374 average:1.000372
AMI: Patch target sum(weights) min:0.92467585 max:1.0065947 average:0.99879076
AMI: Creating AMI for source:rotating_outer_stat and target:rotating_outer_rot
AMI: Patch source sum(weights) min:0.1 max:0.2 average:0.15
"""


def test_parse_ami_weights_reads_the_first_build_of_each_pair() -> None:
    (weights,) = parse_ami_weights(LOG)

    assert (weights.source, weights.target) == ("rotating_outer_stat", "rotating_outer_rot")
    assert (weights.minimum, weights.maximum) == (0.92467585, 1.0110374)


def _boundary(patches: dict[str, tuple[str, int]]) -> str:
    rows = "".join(f"    {n}\n    {{\n        type {t};\n        nFaces {f};\n"
                   f"        startFace 0;\n    }}\n" for n, (t, f) in patches.items())
    return ("FoamFile\n{\n    version 2.0;\n    format ascii;\n    class polyBoundaryMesh;\n"
            f"    object boundary;\n}}\n\n{len(patches)}\n(\n{rows})\n")


ZONES = ("FoamFile\n{\n    version 2.0;\n    class regIOobject;\n    meta\n    {\n    }\n"
         "    object cellZones;\n}\n\n1\n(\nrotating\n{\n    type cellZone;\n"
         "    cellLabels List<label> 0();\n}\n)\n")


def merged_mesh(directory: Path, patches: dict[str, tuple[str, int]],
                zones: str | None = ZONES) -> Path:
    poly = directory / "constant/polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text(_boundary(patches))
    if zones is not None:
        (poly / "cellZones").write_text(zones)
    return directory


GOOD = {"inlet": ("patch", 7), "outlet": ("patch", 7), "side": ("patch", 40),
        "rotor": ("wall", 90), "rotating_outer_stat": ("cyclicAMI", 50),
        "rotating_outer_rot": ("cyclicAMI", 80)}


def test_assembly_checks_pass(tmp_path: Path) -> None:
    cases = render(configs.hawt(tmp_path / "g"))
    merged = merged_mesh(tmp_path / "m", GOOD)

    assert cell_zone_names(merged) == {"rotating"}  # the v2512 header's meta is not a zone
    assert check_assembly(cases, merged, "Number of regions: 2 (OK).", LOG) == ()


def test_assembly_checks_fire(tmp_path: Path) -> None:
    cases = render(configs.hawt(tmp_path / "g"))
    broken = {**GOOD, "rotating_outer_rot": ("patch", 0), "background": ("patch", 3)}
    merged = merged_mesh(tmp_path / "m", broken, zones=None)

    issues: tuple[Issue, ...] = check_assembly(cases, merged, "Number of regions: 1", LOG,
                                               ami_range=(0.95, 1.5))

    assert [(i.code, i.severity.value) for i in issues] == [
        ("DOMAIN_PATCH_EMPTY", "ERROR"), ("WRONG_REGION_KEPT", "ERROR"),
        ("CELL_ZONE_MISSING", "ERROR"), ("REGION_COUNT_UNEXPECTED", "ERROR"),
        ("AMI_WEIGHTS_OUT_OF_RANGE", "WARNING")]
    assert issues[3].details["validity"] == "INVALID"


def test_assembly_checks_without_logs(tmp_path: Path) -> None:
    cases = render(configs.hawt(tmp_path / "g"))
    merged = merged_mesh(tmp_path / "m", GOOD)

    assert [i.code for i in check_assembly(cases, merged, "", "")] == [
        "REGION_COUNT_UNKNOWN", "AMI_WEIGHTS_UNKNOWN"]
    assert [i.code for i in check_assembly(cases, merged, "Number of regions: 2", None)] == []
