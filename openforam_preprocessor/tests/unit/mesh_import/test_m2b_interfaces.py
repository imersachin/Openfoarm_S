"""M2b-2: interfaces between the parts of a machine, without OpenFOAM.

An interface's two sides (patches of two parts) and the patches createPatch
makes of them for each joint (AMI, ACMI with its blockage walls, a stitch);
the coverage that suggests a pair (coincident sides cover each other, walls
meeting at an edge do not); the checks; the merged case's steps; the kinds
case setup gives the ACMI walls. On v2512: tests/integration/test_m2b_parts_real.py.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from case_setup.setup import PatchKind, configured_kinds
from machines.assembly import AMI_WEIGHTS_DICT
from machines.config import InterfaceJoint, MachineParts, MeshInterface
from machines.msh_import import interfaces
from machines.msh_import.cases import EMPTY_PATCH_DICT, IMPORTED_CASE, import_cases
from machines.msh_import.validation import check_imported_mesh
from machines.validation import parse_config
from tests.unit.mesh_import.test_m2b_parts import machine

Data = dict[str, Any]


def with_interfaces(*joints: str) -> Data:
    """The fixture machine with its two interfaces instead of AMI pairs."""
    data = machine()
    mesh = data["imported_mesh"]
    mesh["ami_pairs"] = []
    mesh["interfaces"] = [
        {"name": "CS_GV", "first": ["CS_casing_out"], "second": ["GV_vane_in"],
         "joint": joints[0] if joints else "AMI"},
        {"name": "GV_RU", "first": ["GV_vane_out"], "second": ["RU_run_in"],
         "joint": joints[1] if len(joints) > 1 else "AMI"}]
    return data


def codes(data: Data) -> dict[str, str]:
    config, issues = parse_config(data)
    assert config is not None, [i.message for i in issues]
    return {i.code: i.severity.value for i in check_imported_mesh(config)}


def parts_of(data: Data) -> MachineParts:
    config, _ = parse_config(data)
    assert config is not None and isinstance(config.imported_mesh, MachineParts)
    return config.imported_mesh


# --- the patches an interface makes ------------------------------------------------------------

def test_each_side_is_made_under_the_interface_name_and_its_part() -> None:
    interface = MeshInterface(name="GV_RU", first=("GV_FaceGroup13",),
                              second=("RU_r1_inflow_1", "RU_r1_inflow_2"),
                              joint=InterfaceJoint.AMI)
    assert interfaces.sides(interface) == ("GV_RU_GV", "GV_RU_RU")


def test_an_ami_interface_is_a_cyclic_ami_pair_of_the_grouped_patches() -> None:
    mesh = parts_of(with_interfaces("AMI", "AMI"))
    entries = "\n".join(interfaces.create_patch_entries(mesh))
    assert "name CS_GV_CS;" in entries and "patches (CS_casing_out);" in entries
    assert "type            cyclicAMI;\n            neighbourPatch  CS_GV_GV;" in entries
    assert "blockage" not in entries
    assert interfaces.coupled(mesh) == (("CS_GV_CS", "CS_GV_GV"), ("GV_RU_GV", "GV_RU_RU"))
    assert interfaces.expected(mesh) == dict.fromkeys(
        ("CS_GV_CS", "CS_GV_GV", "GV_RU_GV", "GV_RU_RU"), "cyclicAMI")


def test_an_acmi_side_has_a_wall_on_the_same_faces() -> None:
    mesh = parts_of(with_interfaces("AMI", "ACMI"))
    entries = interfaces.create_patch_entries(mesh)
    couple = next(e for e in entries if "name GV_RU_RU;" in e)
    assert "cyclicACMI" in couple and "nonOverlapPatch GV_RU_RU_blockage;" in couple
    wall = next(e for e in entries if "name GV_RU_RU_blockage;" in e)
    assert "type            wall;" in wall and "patches (RU_run_in);" in wall
    assert interfaces.expected(mesh)["GV_RU_GV_blockage"] == "wall"
    assert interfaces.expected(mesh)["GV_RU_GV"] == "cyclicACMI"


def test_the_rotating_sides_acmi_wall_moves_with_it() -> None:
    config, _ = parse_config(with_interfaces("AMI", "ACMI"))
    assert config is not None
    kinds = configured_kinds(config)
    assert kinds["GV_RU_RU_blockage"] is PatchKind.MOVING_WALL
    assert kinds["GV_RU_GV_blockage"] is PatchKind.WALL


def test_a_stitch_is_made_one_and_its_emptied_sides_removed() -> None:
    data = with_interfaces("STITCH", "AMI")
    config, _ = parse_config(data)
    assert config is not None
    cases = import_cases(config)
    assert cases.stitch == (("CS_GV_CS", "CS_GV_GV"),)
    argv = [s.argv for s in cases.merged_steps() if s.kind == "run"]
    stitch = argv.index(("stitchMesh", "-case", ".", "-perfect", "-overwrite", "CS_GV_CS",
                         "CS_GV_GV"))
    assert argv[stitch + 1][-2:] == ("-dict", EMPTY_PATCH_DICT)
    assert EMPTY_PATCH_DICT in cases.merged
    assert "CS_GV_CS" not in cases.expected and cases.expected["GV_RU_RU"] == "cyclicAMI"


def test_the_merged_case_checks_the_interface_pairs_and_counts_baffles_on_the_parts() -> None:
    config, _ = parse_config(with_interfaces("AMI", "ACMI"))
    assert config is not None
    cases = import_cases(config)
    assert AMI_WEIGHTS_DICT in cases.merged
    steps = cases.check_steps(True)
    baffles = next(s for s in steps if s.argv[0] == "mergeOrSplitBaffles")
    assert baffles.case == f"cases/{IMPORTED_CASE}"  # ACMI shares its faces: not on merged
    assert "-allTopology" not in steps[0].argv


# --- candidates ---------------------------------------------------------------------------------

def trace(name: str, points: list[tuple[float, float]], segments: list[tuple[tuple[float, float],
                                                                         tuple[float, float]]],
          ) -> Any:
    return interfaces._trace(name.split("_")[0], name, np.asarray(points, dtype=float),
                             np.asarray(segments, dtype=float), np.ones(len(points)),
                             float(len(points)), len(points))


def column(name: str, r: float, z0: float, z1: float, n: int) -> Any:
    """A cylinder r, z0..z1 in n faces: points mid-face, segments along z."""
    edges = np.linspace(z0, z1, n + 1)
    return trace(name, [(r, (a + b) / 2) for a, b in zip(edges, edges[1:], strict=False)],
                 [((r, a), (r, b)) for a, b in zip(edges, edges[1:], strict=False)])


def row(name: str, z: float, r0: float, r1: float, n: int) -> Any:
    edges = np.linspace(r0, r1, n + 1)
    return trace(name, [((a + b) / 2, z) for a, b in zip(edges, edges[1:], strict=False)],
                 [((a, z), (b, z)) for a, b in zip(edges, edges[1:], strict=False)])


def test_coincident_sides_cover_each_other_whatever_their_faces() -> None:
    a, b = column("GV_out", 0.6, 0.0, 0.4, 7), column("RU_in", 0.6, 0.0, 0.4, 3)
    assert interfaces._coverage(a, b) == 1.0 and interfaces._coverage(b, a) == 1.0


def test_a_side_reaching_beyond_the_other_is_covered_in_part() -> None:
    a, b = column("RU_out", 0.2, 0.0, 0.4, 8), column("DT_in", 0.2, 0.0, 0.3, 6)
    assert interfaces._coverage(a, b) == pytest.approx(0.75)
    assert interfaces._coverage(b, a) == 1.0


def test_walls_meeting_at_an_edge_do_not_cover_each_other() -> None:
    a, b = row("CS_top", 0.4, 1.0, 1.5, 3), row("OR_top", 0.4, 1.5, 2.0, 2)
    assert interfaces._coverage(a, b) == 0.0 and interfaces._coverage(b, a) == 0.0


def test_patches_lying_on_one_side_are_grouped() -> None:
    gv = [column("GV_FaceGroup13", 0.32, 0.0, 0.4, 8), row("GV_hub", 0.4, 0.32, 0.38, 3)]
    ru = [column("RU_r1_inflow_1", 0.32, 0.0, 0.2, 4), column("RU_r1_inflow_2", 0.32, 0.2, 0.4, 4),
          row("RU_hub", 0.4, 0.2, 0.32, 3)]
    found = interfaces._between(gv, ru)
    best = next(c for c in found if c.first == ("GV_FaceGroup13",))
    assert best.second == ("RU_r1_inflow_1", "RU_r1_inflow_2")
    assert best.coverage == (1.0, 1.0) and best.suggested and not best.partial
    assert all("GV_hub" not in c.first for c in found)


def test_a_suggestion_needs_both_coverages_and_the_ring_areas() -> None:
    candidate = interfaces.Candidate(("RU_out",), ("DT_in",), (1.0, 0.86), (0.0978, 0.0977),
                                     (100, 90), ((0, 1), (0, 1)), ((0, 1), (0, 1)))
    assert candidate.suggested and candidate.partial and not candidate.one_to_one
    assert candidate.name() == "RU_DT"
    assert not interfaces.Candidate(("a",), ("b",), (1.0, 0.79), (1.0, 1.0), (1, 1),
                                    ((0, 1), (0, 1)), ((0, 1), (0, 1))).suggested
    assert not interfaces.Candidate(("a",), ("b",), (1.0, 1.0), (1.0, 1.03), (1, 1),
                                    ((0, 1), (0, 1)), ((0, 1), (0, 1))).suggested


def test_a_stitch_is_offered_between_stationary_parts_with_as_many_faces() -> None:
    same = interfaces.Candidate(("CS_in",), ("OR_out",), (1.0, 1.0), (1.0, 1.0), (32, 32),
                                ((0, 1), (0, 1)), ((0, 1), (0, 1)))
    assert InterfaceJoint.STITCH in interfaces.joints(same, "RU")
    assert InterfaceJoint.STITCH not in interfaces.joints(same, "CS")


# --- the checks ---------------------------------------------------------------------------------

def test_the_rotating_part_needs_an_interface_of_its_own() -> None:
    data = with_interfaces()
    data["imported_mesh"]["interfaces"].pop(1)  # only casing / vanes is left
    found = check_imported_mesh(parse_config(data)[0])  # type: ignore[arg-type]
    issue = next(i for i in found if i.code == "MSH_NO_INTERFACE")
    assert issue.severity.value == "BLOCKING" and "rotating part RU" in issue.message


def test_interfaces_take_the_place_of_ami_pairs() -> None:
    assert codes(with_interfaces("AMI", "ACMI")) == {}
    data = with_interfaces()
    data["imported_mesh"]["interfaces"] = []
    assert codes(data)["MSH_NO_INTERFACE"] == "BLOCKING"


@pytest.mark.parametrize("change, code", [
    (lambda i: i[0].update(second=["CS_inlet"]), "INTERFACE_SIDE_INVALID"),
    (lambda i: i[0].update(first=["CS_casing_out", "GV_top"]), "INTERFACE_SIDE_INVALID"),
    (lambda i: i[0].update(first=["CS_nope"]), "INTERFACE_PATCH_UNKNOWN"),
    (lambda i: i[1].update(first=["CS_casing_out"]), "INTERFACE_PATCH_TWICE"),
    (lambda i: i[1].update(name="CS_GV"), "INTERFACE_NAME_DUPLICATE"),
    (lambda i: i[1].update(joint="STITCH"), "INTERFACE_STITCH_ROTATING"),
    (lambda i: i[0].update(name="CS", first=["CS_casing_out"]), None),
])
def test_an_interface_is_checked(change: Any, code: str | None) -> None:
    data = with_interfaces()
    change(data["imported_mesh"]["interfaces"])
    found = codes(data)
    if code is None:
        assert not any(c.startswith("INTERFACE") for c in found)
    else:
        assert found[code] == "BLOCKING"


def test_an_interface_patch_is_coupled_not_typed() -> None:
    data = with_interfaces()
    data["patches"].append({"name": "vane_in", "type": "WALL",
                            "source": {"kind": "MESH_PATCH", "ref": "GV_vane_in"}})
    assert codes(data)["MSH_AMI_PATCH_TYPED"] == "BLOCKING"


ACMI_LOG = """AMI: Creating AMI for source:GV_RU_GV and target:GV_RU_RU
AMI: Patch source faces: 48
AMI: Patch target faces: 48
AMI: Patch source sum(weights) min:0.648 max:1 average:0.98
AMI: Patch target sum(weights) min:0.7 max:1 average:0.99
"""


def test_an_acmi_pairs_weights_are_reported_not_ranged() -> None:
    mesh = parts_of(with_interfaces("AMI", "ACMI"))
    assert interfaces.partial(mesh) == (("GV_RU_GV", "GV_RU_RU"),)
    notes = interfaces.weights_notes(interfaces.partial(mesh), ACMI_LOG, "at rest (0 deg)")
    assert [(n.code, n.severity.value) for n in notes] == [("ACMI_WEIGHTS", "INFO")]
    assert "0.648-1" in notes[0].message and "blockage" in notes[0].message
