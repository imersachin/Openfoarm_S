"""V6 fix in the generic mesh view: checkMesh region sets are whole regions
(-allTopology writes one per disconnected region), not problem cells."""

from __future__ import annotations

from pathlib import Path

from tests.foam_fixtures import write_poly_mesh, write_set
from visualization.views import mesh_view


def test_region_sets_are_not_shown_as_problems(tmp_path: Path) -> None:
    poly = write_poly_mesh(tmp_path)
    write_set(poly, "region0", "cellSet", [0, 1])
    write_set(poly, "nonOrthoFaces", "faceSet", [0])

    names = [trace.name for trace in mesh_view(tmp_path).figure.data]
    assert "checkMesh: nonOrthoFaces (1)" in names
    assert not any("region0" in name for name in names)
