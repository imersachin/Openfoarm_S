"""M2b-1: a machine from several .msh files, without OpenFOAM.

The configuration (a MachineParts mesh source beside the single file, whose
saved form is unchanged), the passage count found by rotation about the
machine axis (also off the origin), the copies by doubling, the prefixing
dictionaries, the stitch seams, the checks before any conversion, and the
part views' wording. The commands themselves run on v2512 in
tests/integration/test_m2b_parts_real.py.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from machines.config import ImportedMesh, MachineParts, MachineProjectConfig
from machines.msh_import import parts
from machines.msh_import.detect import file_sha256
from machines.msh_import.part_views import suggest_role
from machines.msh_import.surface import BoundarySurface
from machines.msh_import.validation import check_imported_mesh, machine_info
from machines.validation import parse_config
from tests.fixtures.machines.configs import patch, vec
from tests.fixtures.msh import configs

M2B = Path(__file__).parents[2] / "fixtures" / "msh" / "m2b"
Data = dict[str, Any]


def machine(units: str = "m") -> Data:
    """The three fixture parts: casing (whole ring), vanes (8), runner (6)."""
    files = (("Casing", "CS", "casing", 1, ()), ("Guide vanes", "GV", "vanes", 8,
                                                  (("per1", "per2"),)),
             ("Runner", "RU", "runner", 6, (("per_a", "per_b"),)))
    return {
        "project_name": "m2b parts", "machine": "FRANCIS", "rotating_zones": [],
        "imported_mesh": {
            "parts": [{"name": n, "prefix": p, "source_path": str(M2B / f"{f}.msh"),
                       "source_units": units, "sha256": file_sha256(M2B / f"{f}.msh"),
                       "passages": k, "periodic": [{"first": a, "second": b} for a, b in q]}
                      for n, p, f, k, q in files],
            "axis": "z", "origin": vec(0, 0, 0),
            "zones": [{"name": "RU", "cell_zone": "RU", "axis": "z", "origin": vec(0, 0, 0)}],
            "ami_pairs": [{"first": "CS_casing_out", "second": "GV_vane_in"},
                          {"first": "GV_vane_out", "second": "RU_run_in"}]},
        "patches": [patch("inlet", "INLET", "MESH_PATCH", "CS_inlet"),
                    patch("outlet", "OUTLET", "MESH_PATCH", "RU_outlet"),
                    *(patch(f"{p}_{w}", "ROTATING_WALL" if p == "RU" else "WALL",
                            "MESH_PATCH", f"{p}_{w}")
                      for p in ("CS", "GV", "RU") for w in ("bottom", "top"))]}


def codes(data: Data) -> dict[str, str]:
    config, issues = parse_config(data)
    assert config is not None, [i.message for i in issues]
    return {i.code: i.severity.value for i in check_imported_mesh(config)}


# --- the configuration -----------------------------------------------------------------------

def test_several_files_are_their_own_mesh_source() -> None:
    config, issues = parse_config(machine())
    assert config is not None and not issues
    assert isinstance(config.imported_mesh, MachineParts)
    assert [p.prefix for p in config.imported_mesh.parts] == ["CS", "GV", "RU"]
    dumped = config.model_dump(mode="json")["imported_mesh"]
    assert MachineProjectConfig.model_validate({**machine(), "imported_mesh": dumped}) == config


def test_a_single_file_project_is_written_as_before(tmp_path: Path) -> None:
    raw = configs.rotating(configs.mesh_file("rotating_41.msh", tmp_path))
    config, _ = parse_config(raw)
    assert config is not None and isinstance(config.imported_mesh, ImportedMesh)
    assert list(config.model_dump(mode="json")["imported_mesh"]) == [
        "source_path", "source_units", "sha256", "zones", "ami_pairs"]


def test_errors_name_the_field_without_the_union_tag(tmp_path: Path) -> None:
    raw = configs.rotating(configs.mesh_file("rotating_41.msh", tmp_path))
    del raw["imported_mesh"]["source_units"]
    _, issues = parse_config(raw)
    assert [(i.code, i.details["field"]) for i in issues] == [
        ("UNITS_NOT_CHOSEN", "imported_mesh.source_units")]
    data = machine()
    del data["imported_mesh"]["parts"][1]["source_units"]
    _, issues = parse_config(data)
    found = next(i for i in issues if i.code == "UNITS_NOT_CHOSEN")
    assert found.details["field"] == "imported_mesh.parts.1.source_units"
    assert "mesh part 2" in found.message and ".parts.parts" not in str(issues)


@pytest.mark.parametrize("prefix", ["", "1GV", "G_V", "TOOLONGPREFIX"])
def test_a_prefix_is_a_short_word(prefix: str) -> None:
    data = machine()
    data["imported_mesh"]["parts"][0]["prefix"] = prefix
    assert parse_config(data)[0] is None


# --- the passage count ---------------------------------------------------------------------------

def sector(span: float, origin: tuple[float, float, float] = (0, 0, 0),
           extra: float | None = None) -> BoundarySurface:
    """A sector's surface about z through `origin`: start and end faces (4 x 2
    quads each, the end the start turned by `span` deg), an outer face, and,
    with `extra`, a second pair turned by that angle instead."""
    quads: list[np.ndarray] = []
    region: list[int] = []

    def side(angle: float, index: int) -> None:
        a = math.radians(angle)
        for i in range(4):
            for k in range(2):
                r0, r1, z0, z1 = 1 + 0.25 * i, 1.25 + 0.25 * i, 0.2 * k, 0.2 * (k + 1)
                corners = [(r0, z0), (r1, z0), (r1, z1), (r0, z1)]
                quads.append(np.array([[r * math.cos(a) + origin[0], r * math.sin(a) + origin[1],
                                        z + origin[2]] for r, z in corners]))
                region.append(index)

    side(0.0, 0)
    side(span, 1)
    side(span / 2, 2)  # the outer wall stands in: a third patch, matched to nothing
    names = ["per1", "per2", "wall"]
    if extra is not None:
        side(10.0, 3)
        side(10.0 + extra, 4)
        names += ["hub_a", "hub_b"]
    points = np.concatenate(quads)
    offsets = np.arange(0, 4 * len(quads) + 1, 4)
    return BoundarySurface(points, offsets, np.arange(len(points)), np.array(region),
                           tuple(names))


def test_a_passage_count_is_found_from_a_rotation_about_the_axis() -> None:
    found = parts.detect(sector(360 / 28), (0, 0, 0), (0, 0, 1))
    assert found.passages == 28 and found.pairs == (("per1", "per2", pytest.approx(360 / 28)),)
    assert found.spread_deg == 0 and not found.problem


def test_the_axis_may_be_off_the_origin() -> None:
    origin = (0.3, -0.2, 1.5)
    assert parts.detect(sector(24.0, origin), origin, (0, 0, 1)).passages == 15
    assert parts.detect(sector(24.0, origin), (0, 0, 0), (0, 0, 1)).whole_ring


def test_the_pair_is_ordered_so_the_second_side_is_turned_positively() -> None:
    found = parts.detect(sector(-30.0), (0, 0, 0), (0, 0, 1))
    assert found.passages == 12 and found.pairs[0][:2] == ("per2", "per1")


def test_a_part_without_a_rotation_is_a_whole_ring() -> None:
    found = parts.detect(sector(30.0), (0, 0, 0), (1, 0, 0))
    assert found.whole_ring and found.passages == 1


def test_patches_around_the_whole_axis_are_never_a_pair() -> None:
    """Two rings of faces (a hub and a shroud of a whole ring) have no angle."""
    ring = sector(30.0)
    points = ring.points.copy()
    theta = np.arctan2(points[:, 1], points[:, 0])
    radius = np.hypot(points[:, 0], points[:, 1])
    spread = theta + np.repeat(np.arange(len(points) // 4) % 8, 4) * (2 * math.pi / 8)
    whole = BoundarySurface(np.c_[radius * np.cos(spread), radius * np.sin(spread),
                                  points[:, 2]], ring.offsets, ring.connectivity,
                            ring.region, ring.names)
    assert parts.detect(whole, (0, 0, 0), (0, 0, 1)).whole_ring


def test_an_axis_point_in_metres_is_found_in_a_files_own_units() -> None:
    """A part in mm, its axis through (0.3, -0.2, 1.5) m: the conversion at
    scale 1 is in mm, so the point is (300, -200, 1500) there."""
    origin_mm = (300.0, -200.0, 1500.0)
    surface_mm = sector(24.0, origin_mm)
    point = parts.in_file_units((0.3, -0.2, 1.5), 0.001)
    assert point == pytest.approx(origin_mm)
    assert parts.detect(surface_mm, point, (0, 0, 1)).passages == 15
    assert parts.detect(surface_mm, (0.3, -0.2, 1.5), (0, 0, 1)).whole_ring  # the old error


def test_an_angle_that_does_not_divide_360_is_named() -> None:
    found = parts.detect(sector(25.0), (0, 0, 0), (0, 0, 1))
    assert found.passages is None and "is not a whole number" in found.problem


def test_pairs_turning_by_different_angles_are_named() -> None:
    found = parts.detect(sector(30.0, extra=45.0), (0, 0, 0), (0, 0, 1))
    assert found.passages is None and "different angles" in found.problem


def test_a_periodicity_survives_its_report() -> None:
    found = parts.detect(sector(30.0), (0, 0, 0), (0, 0, 1))
    assert parts.Periodicity.from_dict(found.as_dict()) == found


# --- the commands --------------------------------------------------------------------------------

@pytest.mark.parametrize("passages", [2, 3, 7, 8, 12, 15, 28, 31])
def test_the_copies_by_doubling_make_each_passage_once(passages: int) -> None:
    """Follow the cases: every passage index ends up in the ring exactly once."""
    pitch = 360 / passages
    held: dict[str, list[float]] = {"part_GV": [0.0]}
    for step in parts.copy_steps("GV", passages, (0, 0, 0), (0, 0, 1)):
        case = step.case.removeprefix("cases/")
        if step.kind == "copy_case":
            held[case] = list(held[step.source.removeprefix("cases/")])
        elif step.kind == "remove":
            held.pop(case)
        elif step.argv[0] == "transformPoints":
            angle = float(step.argv[-1].rsplit(" ", 1)[1].rstrip(")"))
            held[case] = [a + angle for a in held[case]]
        else:
            held[case] += held[step.argv[-1].removeprefix("../")]
    assert sorted(round(a / pitch) for a in held["part_GV"]) == list(range(passages))
    assert set(held) == {"part_GV"}


def test_a_whole_ring_needs_no_copy() -> None:
    assert parts.copy_steps("SC", 1, (0, 0, 0), (0, 0, 1)) == ()


def test_copies_turn_about_the_axis_through_its_point() -> None:
    step = next(s for s in parts.copy_steps("RU", 15, (0.3, -0.2, 0.0), (0.0, 0.0, 1.0))
                if s.argv and s.argv[0] == "transformPoints")
    assert step.argv[3:] == ("-centre", "(0.29999999999999999 -0.20000000000000001 0)",
                             "-rotate-angle", "((0 0 1) 24)")


def test_every_patch_and_zone_is_prefixed() -> None:
    patches = parts.prefix_patch_dict("GV", [("FaceGroup22", "wall"), ("SV-Outlet", "patch")])
    assert "name GV_FaceGroup22; patchInfo { type wall; }" in patches
    assert "name GV_SV-Outlet; patchInfo { type patch; } constructFrom patches; " \
           "patches (SV-Outlet);" in patches
    zones = parts.prefix_zone_dict("RU", ["fluid-1"])
    for action in ("{ name RU_fluid-1; type cellSet; action new; source zoneToCell; "
                   "zones (fluid-1); }",
                   "{ name fluid-1; type cellZoneSet; action remove; }"):
        assert action in zones
    assert "name RU;" not in zones  # the part's zone is made after the merge
    none = parts.prefix_zone_dict("RU", [])
    assert "{ name RU_cells; type cellSet; action invert; }" in none


def test_each_part_zone_is_made_from_its_own_zones_after_the_merge() -> None:
    from machines.msh_import.cases import part_zones_dict

    text = part_zones_dict(["GV", "RU"])
    assert '{ name GV; type cellSet; action new; source zoneToCell; zones ("GV_.*"); }' in text
    assert "{ name RU; type cellZoneSet; action new; source setToCellZone; set RU; }" in text


def test_seams_are_read_from_a_binary_face_zones_file(tmp_path: Path) -> None:
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "faceZones").write_bytes(
        b"FoamFile\n{\n    format      binary;\n}\n\n2\n(\nGV_per1GV_per2CutFaceZone\n{\n"
        b"    type            faceZone;\nfaceLabels      List<label> 48(" + bytes(range(192))
        + b")\n;\nflipMap         List<bool> 48(" + bytes(48) + b")\n;\n}\ninterior-1\n{\n"
        b"    type            faceZone;\nfaceLabels      List<label> 3(\x00\x01\x02)\n;\n}\n)\n")
    assert parts.seams(tmp_path) == [("GV_per1GV_per2CutFaceZone", 48)]


# --- the checks before any conversion ----------------------------------------------------------

def test_a_complete_machine_has_no_finding() -> None:
    assert codes(machine()) == {}


def test_the_machine_is_the_files_under_their_prefixes() -> None:
    config, _ = parse_config(machine())
    assert config is not None and isinstance(config.imported_mesh, MachineParts)
    info = machine_info(config.imported_mesh)
    assert "GV_per1" not in info.patches and "GV_vane_in" in info.patches
    assert info.patches.count("CS_bottom") == 1 and "RU_bottom" in info.patches
    assert {"CS", "GV", "RU", "RU_fluid-1"} <= set(info.cell_zones)
    assert info.declared_cells == 96 + 18 * 8 + 24 * 6


def test_passages_are_confirmed_never_assumed() -> None:
    data = machine()
    data["imported_mesh"]["parts"][1]["passages"] = None
    assert codes(data)["PART_PASSAGES_NOT_CONFIRMED"] == "BLOCKING"
    data["imported_mesh"]["parts"][1]["passages"] = 1  # one passage with a pair
    assert codes(data)["PART_PASSAGES_INVALID"] == "BLOCKING"


def test_two_parts_never_share_a_prefix() -> None:
    data = machine()
    data["imported_mesh"]["parts"][2]["prefix"] = "GV"
    assert codes(data)["PART_PREFIX_DUPLICATE"] == "BLOCKING"


def test_a_periodic_patch_must_be_the_files() -> None:
    data = machine()
    data["imported_mesh"]["parts"][1]["periodic"] = [{"first": "per1", "second": "nope"}]
    assert codes(data)["PART_PERIODIC_UNKNOWN"] == "BLOCKING"


def test_each_part_patch_needs_a_type_under_its_prefixed_name() -> None:
    data = machine()
    data["patches"] = [p for p in data["patches"] if p["source"]["ref"] != "GV_top"]
    assert codes(data)["MESH_PATCH_WITHOUT_TYPE"] == "BLOCKING"
    data = machine()
    data["patches"][0]["source"]["ref"] = "inlet"  # the file's own name is not the mesh's
    assert codes(data)["PATCH_SOURCE_UNKNOWN"] == "BLOCKING"


def test_the_rotating_zone_is_a_part() -> None:
    data = machine()
    data["imported_mesh"]["zones"][0]["cell_zone"] = "fluid-1"
    assert codes(data)["MSH_NO_CELL_ZONE"] == "BLOCKING"


def test_units_are_checked_per_part() -> None:
    data = machine()
    data["imported_mesh"]["parts"][0]["source_units"] = "mm"
    found = check_imported_mesh(parse_config(data)[0])  # type: ignore[arg-type]
    issue = next(i for i in found if i.code == "MSH_UNITS_SUSPECT")
    assert issue.details["field"] == "imported_mesh.parts.0.source_units"
    assert "Casing (casing.msh)" in issue.message


def test_a_changed_part_file_is_found(tmp_path: Path) -> None:
    data = machine()
    moved = tmp_path / "vanes.msh"
    moved.write_bytes((M2B / "vanes.msh").read_bytes() + b"\n")
    data["imported_mesh"]["parts"][1]["source_path"] = str(moved)
    assert codes(copy.deepcopy(data))["MSH_SOURCE_CHANGED"] == "BLOCKING"


@pytest.mark.parametrize("name, role", [
    ("GV-03-91-AG5-HighRe.msh", "GV"), ("RU-AG5-HighRe.msh", "RU"), ("DT-HighRe-uns.msh", "DT"),
    ("SC-SV-HH-HighRe.msh", "SC"), ("draft_tube.msh", "DT"), ("box.msh", None)])
def test_the_file_name_suggests_a_role(name: str, role: str | None) -> None:
    assert suggest_role(Path(name)) == role
