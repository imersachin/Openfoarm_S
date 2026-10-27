"""M2b-3: the 3D view of a machine of several files, without OpenFOAM.

The figure groups and colours the surfaces by part (an interface keeps its
own colour, the type stays in every name), lists the cell zones hidden; the
finer grid stays within the limit; a single file's scene is drawn as before;
the merged mesh's path as Windows sees it, for ParaView.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from machines.disk import to_windows
from machines.previews import CELL_ZONE, INTERFACE, Scene, Surface, cluster
from visualization.machine_scene import KIND_STYLE, PART_COLOURS, scene_figure


def square(n: int) -> tuple[np.ndarray, np.ndarray]:
    """An n x n grid of points on z = 0 in two triangles a cell."""
    x, y = np.meshgrid(np.arange(n + 1.0), np.arange(n + 1.0))
    points = np.c_[x.ravel(), y.ravel(), np.zeros(x.size)]
    k = np.arange(n * (n + 1)).reshape(n, n + 1)[:, :n].ravel()
    triangles = np.r_[np.c_[k, k + 1, k + n + 1], np.c_[k + 1, k + n + 2, k + n + 1]]
    return points, triangles


def surface(name: str, kind: str, group: str = "", hidden: bool = False) -> Surface:
    points, triangles = square(2)
    return Surface(name, kind, points.astype(np.float32), triangles.astype(np.int32), 8, 8,
                   group, hidden)


def test_a_machine_is_drawn_by_part_and_its_interfaces_stand_out() -> None:
    scene = Scene("mesh", "k", (
        surface("GV_FaceGroup22 (wall)", "WALL", "GV: Guide vanes"),
        surface("RU_r1_b1_1 (wall)", "ROTATING_WALL", "RU: Runner"),
        surface("GV_RU_GV (cyclicAMI)", INTERFACE, "GV: Guide vanes"),
        surface("cell zone RU_fluid", CELL_ZONE, "Cell zones (click to show)", hidden=True)))
    traces = scene_figure(scene).data
    assert [t.legendgroup for t in traces] == ["GV: Guide vanes", "RU: Runner",
                                               "GV: Guide vanes", "Cell zones (click to show)"]
    assert traces[0].color == PART_COLOURS[0] and traces[1].color == PART_COLOURS[1]
    assert traces[2].color == KIND_STYLE[INTERFACE][0]
    assert "(wall)" in traces[0].name and traces[3].visible == "legendonly"
    assert all(t.visible is True for t in traces[:3])


def test_a_single_files_scene_is_drawn_by_type_as_before() -> None:
    scene = Scene("mesh", "k", (surface("inlet (patch)", "INLET"),
                                surface("rotor (wall)", "ROTATING_WALL")))
    traces = scene_figure(scene).data
    assert [t.legendgroup for t in traces] == ["INLET", "ROTATING_WALL"]
    assert traces[1].color == KIND_STYLE["ROTATING_WALL"][0]
    figure = scene_figure(scene)
    assert figure.layout.title.text is None and figure.layout.margin.t == 0


def test_a_single_files_grid_is_coarsened_as_before() -> None:
    """The step is a choice of the caller; 0.7 stays the default (the finer
    0.9 of a machine is measured on the 23 M machine: docs, M2b-3)."""
    points, triangles = square(200)
    assert np.array_equal(cluster(points, triangles, 20_000)[1],
                          cluster(points, triangles, 20_000, 0.7)[1])
    assert len(cluster(points, triangles, 20_000, 0.9)[1]) <= 20_000


def test_a_machine_figure_has_no_title_either() -> None:
    figure = scene_figure(Scene("mesh", "k", (surface("GV_x (wall)", "WALL", "GV: Vanes"),)))
    assert figure.layout.title.text is None and figure.layout.margin.t == 0


def test_an_interface_side_is_drawn_with_its_own_part() -> None:
    """GV_RU_RU (and its ACMI wall) is the runner's side of interface GV_RU."""
    from types import SimpleNamespace

    from machines.previews import PreviewLimits, _part_surfaces

    points, triangles = square(2)
    faces = {"GV_RU_GV": [0], "GV_RU_RU": [1], "GV_RU_RU_blockage": [2]}
    found = SimpleNamespace(points=points, zones=(), zone_faces=lambda z: np.zeros(0, int))
    listed = [(SimpleNamespace(name=n, patch_type="wall"), np.asarray(f))
              for n, f in faces.items()]
    import machines.previews as previews

    original = previews._fan
    previews._fan = lambda surface, f: triangles[f]  # type: ignore[assignment]
    try:
        made = _part_surfaces(found, listed, {}, PreviewLimits(),
                              {"GV": "Guide vanes", "RU": "Runner"},
                              {"GV_RU_GV": "GV", "GV_RU_RU": "RU"})
    finally:
        previews._fan = original  # type: ignore[assignment]
    assert {s.name.split(" ")[0]: s.group for s in made} == {
        "GV_RU_GV": "GV: Guide vanes", "GV_RU_RU": "RU: Runner",
        "GV_RU_RU_blockage": "RU: Runner"}


def test_paraview_reads_the_case_where_windows_sees_it() -> None:
    assert to_windows(Path("/root/machines/demo/cases/merged/demo.foam"), "Ubuntu-24.04") == (
        "\\\\wsl.localhost\\Ubuntu-24.04\\root\\machines\\demo\\cases\\merged\\demo.foam")
    assert to_windows(Path("/mnt/e/Udit/demo.foam"), None) == "E:\\Udit\\demo.foam"
    assert to_windows(Path("/root/demo.foam"), None) is None
