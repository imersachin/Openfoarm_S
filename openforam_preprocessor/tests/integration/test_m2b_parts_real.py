"""M2b-1 on real OpenFOAM v2512: a machine from several .msh files.

1. The M2b test parts (tests/fixtures/msh/m2b, synthetic): a casing ring, one
   45 deg guide-vane passage and one 60 deg runner passage whose patch and
   zone names clash. Read (converted at scale 1), the passage counts found,
   assembled (prefixed, copied, stitched, merged), checked, reused. With an
   outer ring (M2b-2): the interfaces suggested, made as AMI, ACMI and a
   stitch, the case set up and two pimpleFoam steps.
2. Opt-in, large real meshes: with MACHINES_E0_DIR pointing at the owner's
   Francis files (never committed), the parts are read and their passage
   counts found (evidence 1.1: GV 28, RU 15, SC and DT whole rings).

    pytest -m openfoam tests/integration/test_m2b_parts_real.py -v -s
    MACHINES_E0_DIR=/root/meshes pytest -m openfoam tests/integration/test_m2b_parts_real.py -v -s
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from machines.msh_import import parts
from machines.msh_import.checks import mesh_zone_names
from machines.msh_import.detect import file_sha256
from machines.msh_import.part_views import confirmation, summary
from machines.service import MachineService
from tests.fixtures.machines.configs import patch
from tests.unit.mesh_import.test_m2b_parts import M2B, machine
from vawt.service import PreviewView
from visualization.foam_reader import read_boundary

TOOLS = ("fluent3DMeshToFoam", "transformPoints", "topoSet", "surfaceMeshExtract",
         "createPatch", "mergeMeshes", "stitchMesh", "mergeOrSplitBaffles", "checkMesh",
         "postProcess", "foamDictionary")
E0_DIR = os.environ.get("MACHINES_E0_DIR")
pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


def unconfirmed() -> dict[str, Any]:
    data = machine()
    for part in data["imported_mesh"]["parts"]:
        part.pop("passages")
        part.pop("periodic")
    return data


def read_and_confirm(service: MachineService, data: dict[str, Any]) -> list[str]:
    assert service.read_parts(data) == ()
    found = []
    for view in service.part_views(data):
        assert view.converted, view
        found.append(f"{view.prefix}: {summary(view)}")
        data["imported_mesh"]["parts"][view.index].update(confirmation(view) or {})
    return found


def boundary(root: Path, case: str) -> dict[str, int]:
    return {p.name: p.n_faces for p in read_boundary(
        root / "cases" / case / "constant/polyMesh/boundary")}


def test_the_parts_are_read_and_their_passages_found(tmp_path: Path) -> None:
    service = MachineService(tmp_path / "project")
    data = unconfirmed()
    assert read_and_confirm(service, data) == [
        "CS: a whole ring: 360 deg, no periodic pair",
        "GV: 1 of 8 passages (45.000000 deg; 1 periodic pair: per1 / per2)",
        "RU: 1 of 6 passages (59.999999 deg; 1 periodic pair: per_a / per_b)"]
    assert data == machine()  # what was found is what the fixture says
    assert service.read_parts(data) == ()  # read once


def test_the_machine_is_assembled_from_its_parts(tmp_path: Path) -> None:
    service = MachineService(tmp_path / "project")
    data = unconfirmed()
    read_and_confirm(service, data)
    service.save(data)
    config = service.saved_config()
    assert config is not None
    result = service.pipeline.run_sync(service.root, config)
    assert result.succeeded, (result.message, [i.message for i in result.issues])
    root = service.root
    for prefix in ("CS", "GV", "RU"):  # the scale-1 conversions of "Read" are reused
        assert f"machine_import_mesh:part_{prefix}_convert" in result.reused

    # Prefixed, the copies stitched, the empty periodic patches gone: no clash left.
    found = boundary(root, "imported")
    assert set(found) == {f"{p}_{n}" for p, names in (
        ("CS", ("casing_out", "inlet", "bottom", "top")),
        ("GV", ("vane_out", "vane_in", "bottom", "top")),
        ("RU", ("outlet", "run_in", "bottom", "top"))) for n in names}
    assert found["GV_bottom"] == 8 * 9 and found["RU_bottom"] == 6 * 12
    assert mesh_zone_names(root / "cases" / "imported") == {
        "CS", "CS_fluid-1", "GV", "GV_fluid-1", "RU", "RU_fluid-1"}
    # Each part's zone holds all of its cells, copies included (mergeMeshes keeps
    # one zone a cell: the zone is made after the merge).
    assert parts.zone_sizes(root / "cases" / "imported") == {
        "CS_fluid-1": 96, "CS": 96, "GV_fluid-1": 144, "GV": 144, "RU_fluid-1": 144,
        "RU": 144}
    assert parts.zone_sizes(root / "cases" / "merged")["RU"] == 144
    report = json.loads((root / "reports/mesh_import.json").read_text("utf-8"))
    assert report["cells"] == 96 + 8 * 18 + 6 * 24
    assert report["seams"] == [["GV_per1GV_per2CutFaceZone", 8 * 6],
                               ["RU_per_aRU_per_bCutFaceZone", 6 * 6]]
    assert [p["passages"] for p in report["parts"]] == [1, 8, 6]
    mesh = json.loads((root / "reports/mesh_quality_report.json").read_text("utf-8"))
    assert mesh["regions"] == {"expected": 3, "found": 3} and mesh["status"] == "passed"
    assert [round(w["minimum"], 3) for w in mesh["ami_weights"]] == [1.0, 1.0]
    check = (root / "logs/merged/checkMesh.log").read_text("utf-8")
    assert "-allTopology" not in check.split("Exec", 1)[1].splitlines()[0]
    assert "duplicate faces" in (root / "logs/imported/02_baffles.log").read_text("utf-8")

    again = service.pipeline.run_sync(root, config)
    assert again.succeeded and "machine_import_mesh" in again.reused


def test_a_changed_part_rebuilds_its_ring_only(tmp_path: Path) -> None:
    service = MachineService(tmp_path / "project")
    data = unconfirmed()
    read_and_confirm(service, data)
    service.save(data)
    first = service.saved_config()
    assert first is not None and service.pipeline.run_sync(service.root, first).succeeded
    data["imported_mesh"]["parts"][0]["source_units"] = "cm"  # the casing only
    service.save(data)
    config = service.saved_config()
    assert config is not None
    result = service.pipeline.run_sync(service.root, config)
    assert "machine_import_mesh" in result.executed
    assert {"machine_import_mesh:part_GV", "machine_import_mesh:part_RU"} <= set(result.reused)
    assert "machine_import_mesh:part_CS" not in result.reused


def test_a_passage_count_that_is_no_longer_found_stops_the_run(tmp_path: Path) -> None:
    service = MachineService(tmp_path / "project")
    data = unconfirmed()
    read_and_confirm(service, data)
    data["imported_mesh"]["axis"] = "x"  # the vanes no longer repeat about it
    for zone in data["imported_mesh"]["zones"]:
        zone["axis"] = "x"
    service.save(data)
    config = service.saved_config()
    assert config is not None
    result = service.pipeline.run_sync(service.root, config)
    assert not result.succeeded
    issue = next(i for i in result.issues if i.code == "PART_PASSAGES_CHANGED")
    assert "confirmed 8 passage(s)" in issue.message and "found 1 passage(s)" in issue.message
    assert not (service.root / "cases" / parts.part_case("GV") / parts.RING_RECORD).exists()


# --- opt-in: the owner's Francis files (MACHINES_E0_DIR) -------------------------------------

FRANCIS = {"SC-SV-HH-HighRe.msh": ("SC", "a whole ring"),
           "GV-03-91-AG5-HighRe.msh": ("GV", "1 of 28 passages (12.857143 deg; 3 periodic"),
           "RU-AG5-HighRe.msh": ("RU", "1 of 15 passages (24.000000 deg; 5 periodic"),
           "DT-HighRe-uns.msh": ("DT", "a whole ring")}


@pytest.mark.skipif(not E0_DIR, reason="MACHINES_E0_DIR is not set (large real meshes)")
def test_the_francis_parts_are_read_and_their_passages_found(tmp_path: Path) -> None:
    folder = Path(str(E0_DIR))
    if not all((folder / name).is_file() for name in FRANCIS):
        pytest.skip("the four Francis files are not in MACHINES_E0_DIR")
    service = MachineService(tmp_path / "project")
    data = {"project_name": "francis", "machine": "FRANCIS", "rotating_zones": [],
            "imported_mesh": {
                "parts": [{"name": prefix, "prefix": prefix, "source_path": str(folder / name),
                           "source_units": "m", "sha256": file_sha256(folder / name)}
                          for name, (prefix, _) in FRANCIS.items()],
                "axis": "z", "origin": {"x": 0, "y": 0, "z": 0}}}
    start = time.perf_counter()
    assert service.read_parts(data) == ()
    read = time.perf_counter() - start
    views = {v.prefix: summary(v) for v in service.part_views(data)}
    for prefix, expected in FRANCIS.values():
        assert views[prefix].startswith(expected), views[prefix]
    print(f"\nread the four parts in {read:.0f} s: {views}")


# --- M2b-2: interfaces ---------------------------------------------------------------------------

def four_parts(service: MachineService) -> dict[str, Any]:
    """The fixture machine with the outer ring (its inner face is the casing's
    inlet face by face), read and confirmed, without pairs."""
    data = unconfirmed()
    data["imported_mesh"]["ami_pairs"] = []
    data["imported_mesh"]["parts"].append({
        "name": "Outer", "prefix": "OR", "source_path": str(M2B / "outer.msh"),
        "source_units": "m", "sha256": file_sha256(M2B / "outer.msh")})
    read_and_confirm(service, data)
    return data


def test_the_interfaces_suggested_are_the_coincident_sides(tmp_path: Path) -> None:
    service = MachineService(tmp_path / "project")
    found = service.interface_candidates(four_parts(service))
    assert found is not None
    suggested = {(c.first, c.second, c.one_to_one) for c in found if c.suggested}
    assert suggested == {(("CS_casing_out",), ("GV_vane_in",), False),
                         (("CS_inlet",), ("OR_ring_in",), True),
                         (("GV_vane_out",), ("RU_run_in",), True)}
    assert all(min(c.coverage) == 1.0 for c in found if c.suggested)
    assert not [c for c in found if not c.suggested]  # walls meeting at edges are not listed


def assembled(tmp_path: Path) -> tuple[MachineService, Any]:
    """The four fixture parts with their suggested interfaces as AMI (casing /
    vanes), ACMI (vanes / runner) and a stitch (outer ring / casing), physics,
    run: (service, run result)."""
    service = MachineService(tmp_path / "project")
    data = four_parts(service)
    found = service.interface_candidates(data)
    assert found is not None
    joint = {("CS", "GV"): "AMI", ("GV", "RU"): "ACMI", ("CS", "OR"): "STITCH"}
    data["imported_mesh"]["interfaces"] = [
        {"name": c.name(), "first": list(c.first), "second": list(c.second),
         "joint": joint[(c.parts[0], c.parts[1])]} for c in found if c.suggested]
    taken = {n for c in found for n in (*c.first, *c.second)}
    data["patches"] = [p for p in data["patches"]
                       if p["source"]["ref"] not in taken and p["name"] != "inlet"]
    data["patches"] += [patch("inlet", "INLET", "MESH_PATCH", "OR_far"),
                        *(patch(f"OR_{w}", "WALL", "MESH_PATCH", f"OR_{w}")
                          for w in ("bottom", "top"))]
    data["physics"] = {
        "fluid": {"density": 998.0, "kinematic_viscosity": 1e-6},
        "turbulence": {"length_scale": 0.05},
        "rotation": {"RU": {"speed": {"kind": "RPM", "rpm": 60.0}, "direction": "NEGATIVE"}},
        "inlets": {"inlet": {"kind": "FLOW_RATE", "volumetric_flow_rate": 0.5}},
        "outlets": {"outlet": {"pressure_pa": 0.0}},
        "time": {"end_revolutions": 2 / 360, "write_interval": 2, "write_unit": "DEGREES"}}
    service.save(data)
    config = service.saved_config()
    assert config is not None
    return service, service.pipeline.run_sync(service.root, config)


def test_ami_acmi_and_a_stitch_are_made_and_the_case_runs(tmp_path: Path) -> None:
    service, result = assembled(tmp_path)
    assert result.succeeded, (result.message, [i.message for i in result.issues])
    root = service.root
    found_patches = boundary(root, "merged")
    assert "CS_OR_CS" not in found_patches and "OR_ring_in" not in found_patches  # stitched
    sizes = parts.zone_sizes(root / "cases" / "merged")  # made again after the stitch
    assert {p: sizes[p] for p in ("CS", "GV", "RU", "OR")} == {"CS": 96, "GV": 144, "RU": 144,
                                                               "OR": 64}
    assert {n: found_patches[n] for n in ("CS_GV_CS", "CS_GV_GV", "GV_RU_GV",
                                          "GV_RU_GV_blockage")} == {
        "CS_GV_CS": 32, "CS_GV_GV": 48, "GV_RU_GV": 48, "GV_RU_GV_blockage": 48}
    sizes = parts.zone_sizes(root / "cases" / "merged")  # made again after the stitch
    assert {p: sizes[p] for p in ("CS", "GV", "RU", "OR")} == {"CS": 96, "GV": 144, "RU": 144,
                                                               "OR": 64}
    mesh = json.loads((root / "reports/mesh_quality_report.json").read_text("utf-8"))
    assert mesh["regions"] == {"expected": 3, "found": 3}
    assert {(w["source"], round(w["minimum"], 3)) for w in mesh["ami_weights"]} == {
        ("CS_GV_CS", 1.0), ("GV_RU_GV", 1.0)}
    u = (root / "case/0/U").read_text("utf-8")
    assert "GV_RU_RU_blockage" in u and "movingWallVelocity" in u.split(
        "GV_RU_RU_blockage", 1)[1].split("}", 1)[0]
    # The solver smoke test (CLAUDE.md: in the tests only): two steps.
    run = subprocess.run(["pimpleFoam", "-case", str(root / "case")], capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-2000:]
    assert run.stdout.count("\nTime = ") == 2


@pytest.mark.skipif(not E0_DIR, reason="MACHINES_E0_DIR is not set (large real meshes)")
def test_the_francis_interfaces_suggested_are_e0s(tmp_path: Path) -> None:
    folder = Path(str(E0_DIR))
    if not all((folder / name).is_file() for name in FRANCIS):
        pytest.skip("the four Francis files are not in MACHINES_E0_DIR")
    service = MachineService(tmp_path / "project")
    data = {"project_name": "francis", "machine": "FRANCIS", "rotating_zones": [],
            "imported_mesh": {
                "parts": [{"name": prefix, "prefix": prefix, "source_path": str(folder / name),
                           "source_units": "m", "sha256": file_sha256(folder / name)}
                          for name, (prefix, _) in FRANCIS.items()],
                "axis": "z", "origin": {"x": 0, "y": 0, "z": 0}}}
    read_and_confirm(service, data)
    start = time.perf_counter()
    found = service.interface_candidates(data)
    took = time.perf_counter() - start
    assert found is not None
    suggested = {(c.first, c.second) for c in found if c.suggested}
    assert suggested == {(("SC_SV-Outlet",), ("GV_FaceGroup27",)),
                         (("GV_FaceGroup13",), ("RU_r1_inflow_1", "RU_r1_inflow_2")),
                         (("RU_r1_outflow_1", "RU_r1_outflow_2"), ("DT_DT_INLET",))}
    for c in found[:5]:
        print(f"\n{'+'.join(c.first)} ~ {'+'.join(c.second)}: {100 * c.coverage[0]:.1f} / "
              f"{100 * c.coverage[1]:.1f} %, ring areas {c.ring_area[0]:.5f} / "
              f"{c.ring_area[1]:.5f}, faces {c.ring_faces}")
    print(f"candidates in {took:.1f} s")



# --- M2b-3 and M2b-4 ------------------------------------------------------------------------------

def test_the_machine_is_drawn_by_part(tmp_path: Path) -> None:
    service, result = assembled(tmp_path)
    assert result.succeeded
    view = service.preview(PreviewView.MESH)
    assert view.scene is not None and not view.issues
    groups = {s.group for s in view.scene.surfaces}
    assert {"CS: Casing", "GV: Guide vanes", "RU: Runner", "OR: Outer"} <= groups
    zones = [s for s in view.scene.surfaces if s.hidden]
    assert zones and all(s.kind == "CELL_ZONE" for s in zones)
    assert {s.name.split(" ")[2] for s in zones} == {"CS_fluid-1", "GV_fluid-1", "RU_fluid-1",
                                                     "OR_fluid-1"}
    sides = {s.name.split(" ")[0]: s.kind for s in view.scene.surfaces}
    assert sides["GV_RU_RU"] == sides["CS_GV_CS"] == "INTERFACE"
    foam, windows = service.paraview_file() or (None, None)
    assert foam is not None and foam.is_file() and foam.suffix == ".foam"


def test_the_runner_is_checked_over_one_blade_pitch(tmp_path: Path) -> None:
    service, result = assembled(tmp_path)
    assert result.succeeded
    found = service.check_ami_revolution()
    assert found.done and found.steps == 12, found.issues
    report = json.loads((service.root / "reports/ami_revolution.json").read_text("utf-8"))
    assert report["when"].startswith("over one blade pitch (60 deg, 12 x 5 deg)")
    assert service.revolution_is_current(report)
    assert {w.source for w in found.weights} == {"CS_GV_CS", "GV_RU_GV"}
    assert all(abs(w.minimum - 1) < 0.05 for w in found.weights if w.source == "GV_RU_GV")


def test_a_study_project_is_made_beside_the_machine(tmp_path: Path) -> None:
    service, result = assembled(tmp_path)
    assert result.succeeded
    assert service.study_parts() == ("RU",)
    target, notes, issues = service.make_study()
    assert target is not None and target.name == "project-study", (notes, issues)
    other = MachineService(target)
    config = other.saved_config()
    assert config is not None and config.project_name == "m2b parts study"
    raw = config.model_dump(mode="json")
    assert [p["prefix"] for p in raw["imported_mesh"]["parts"]] == ["RU"]
    assert [p["source"]["ref"] for p in raw["patches"] if p["type"] == "INLET"] == ["RU_run_in"]
    assert raw["physics"]["inlets"] == {"RU_run_in": {"kind": "FLOW_RATE",
                                                      "volumetric_flow_rate": 0.5}}
    estimate = service.solve_estimate()
    assert estimate is not None and estimate.cells == 96 + 144 + 144 + 64 and estimate.fits
