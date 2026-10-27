"""M2b-1 on real OpenFOAM v2512: a machine from several .msh files.

1. The M2b test parts (tests/fixtures/msh/m2b, synthetic): a casing ring, one
   45 deg guide-vane passage and one 60 deg runner passage whose patch and
   zone names clash. Read (converted at scale 1), the passage counts found,
   assembled (prefixed, copied, stitched, merged), checked, reused.
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
import time
from pathlib import Path
from typing import Any

import pytest

from machines.msh_import import parts
from machines.msh_import.checks import mesh_zone_names
from machines.msh_import.detect import file_sha256
from machines.msh_import.part_views import confirmation, summary
from machines.service import MachineService
from tests.unit.mesh_import.test_m2b_parts import machine
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
    assert "duplicate faces" in (root / "logs/merged/02_baffles.log").read_text("utf-8")

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
