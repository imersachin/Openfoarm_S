"""M1-MSH E6 evidence (where the Fluent cell zone is lost) read back without OpenFOAM.

Logs are captured by tests/fixtures/msh/e0/E6_fluent_zones/run.sh on OpenFOAM
v2512, after the owner's E0 review (docs/msh_import_notes.md, section 2.6).
"""

from __future__ import annotations

import re
from pathlib import Path

from mesh.parser import CheckMeshParser

LOGS = Path(__file__).parents[2] / "fixtures" / "msh" / "e0" / "E6_fluent_zones" / "logs"


def log(name: str) -> str:
    return (LOGS / name).read_text(encoding="utf-8")


def lines(name: str) -> list[str]:
    return log(name).split("\n")[:-1]


def test_foam_mesh_to_fluent_writes_one_cell_section_for_two_zones() -> None:
    assert lines("export_source_cellZones.txt") == [
        "stationary cellZone cellLabels 23408", "rotating cellZone cellLabels 789"]
    cells = lines("export_msh_cells.txt")
    assert [c for c in cells if c.startswith("(12 ")] == [
        "(12 (0 1 5e85 0 0))", "(12 (1 1 5e85 1 0)"]
    assert [c for c in cells if " fluid " in c] == ["(39 (1 fluid fluid-1)())"]


def test_fluent3d_keeps_each_fluid_cell_section_as_a_cell_zone() -> None:
    codes = log("exit_codes.txt")
    convert = log("two_zones_01_fluent3DMeshToFoam.log")
    metrics = CheckMeshParser().parse_file(LOGS / "two_zones_02_checkMesh.log")

    assert all(int(c) == 0 for c in re.findall(r"exit=(\d+)", codes))
    assert "(12 (14 5b71 5e85 1 0)" in lines("two_zones_msh_cells.txt")
    assert "CellGroup: 20 start: 23408 end: 24196" in convert
    assert lines("two_zones_cellZones.txt") == [
        "stationary cellZone cellLabels 23408", "rotating cellZone cellLabels 789"]
    assert metrics.mesh_ok and metrics.cells == 24197
