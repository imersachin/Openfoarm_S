"""V6 Plotly figures of the display data (visualization.machine_scene)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from machines.previews import (
    PreviewLimits,
    build_mesh_scene,
    build_setup_scene,
    cut_planes,
    flow_arrows,
    rotation_arrows,
)
from machines.validation import parse_config
from tests.fixtures.machines import configs
from tests.foam_block import write_block_mesh, write_set
from visualization.machine_scene import KIND_STYLE, scene_figure


def hawt(tmp_path: Path, **update: object):
    raw = configs.hawt(tmp_path)
    raw.update(update)
    config, issues = parse_config(raw)
    assert config is not None, issues
    return config


def test_setup_figure_groups_patches_by_type_with_one_colour_per_type(tmp_path: Path) -> None:
    config = hawt(tmp_path)
    scene = build_setup_scene(config)
    figure = scene_figure(scene, rotation_arrows(config, scene))

    meshes = {t.name: t for t in figure.data if t.type == "mesh3d"}
    assert {name: (t.legendgroup, t.color) for name, t in meshes.items()} == {
        "rotor: body rotor (rotating) — 636 faces": ("ROTATING_WALL", "#eda100"),
        "inlet: domain face axis_min — 96 faces": ("INLET", "#2a78d6"),
        "outlet: domain face axis_max — 96 faces": ("OUTLET", "#e87ba4"),
        "side: domain face side — 192 faces": ("SLIP", "#1baf7a"),
    }
    assert meshes["inlet: domain face axis_min — 96 faces"].legendgrouptitle.text == "Inlet"
    assert figure.layout.legend.groupclick == "toggleitem"
    assert figure.layout.scene.aspectmode == "data"
    assert figure.layout.uirevision == "setup"
    seeds = next(t for t in figure.data if t.legendgroup == "SEED")
    assert tuple(seeds.text) == ("zone rotating", "domain")


def test_arrows_have_heads_only_when_defined(tmp_path: Path) -> None:
    config = hawt(tmp_path)
    scene = build_setup_scene(config)
    arrows = [*rotation_arrows(config, scene), *flow_arrows(config, scene)[0]]
    figure = scene_figure(scene, arrows)

    camera_up = figure.layout.scene.camera.up
    assert (camera_up.x, camera_up.y, camera_up.z) == (1.0, 0.0, 0.0)  # the HAWT axis
    cones = [t for t in figure.data if t.type == "cone"]
    assert [c.name for c in cones] == ["rotating: rotation axis +x", "flow along +x (flow axis)"]
    unset = next(t for t in figure.data if t.name == "rotating: rotation not set")
    assert unset.line.dash == "dash"


def test_a_defined_rotation_is_a_solid_arc_with_a_head(tmp_path: Path) -> None:
    config = hawt(tmp_path, physics={
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "rotation": {"rotating": {"speed": {"kind": "RPM", "rpm": 60},
                                  "direction": "NEGATIVE"}}})
    scene = build_setup_scene(config)
    figure = scene_figure(scene, rotation_arrows(config, scene))

    label = ("rotating: negative about +x (clockwise seen from +x), 60 rpm")
    arc = next(t for t in figure.data if t.type == "scatter3d" and t.name == label)
    assert arc.line.dash == "solid"
    assert [c.name for c in figure.data if c.type == "cone"] == [
        "rotating: rotation axis +x", label]


def test_mesh_figure_shows_thinning_cuts_and_problems(tmp_path: Path) -> None:
    # Centred on the HAWT zone (the origin), so both cuts cross it.
    poly = write_block_mesh(tmp_path, (20, 20, 20), spacing=0.1, origin=(-1.0, -1.0, -1.0))
    write_set(poly, "skewFaces", "faceSet", list(range(30)))
    config = hawt(tmp_path / "g")
    scene = build_mesh_scene(poly, {"inlet": "INLET"}, cut_planes(config),
                             PreviewLimits(max_faces=600, min_faces_per_patch=100))
    figure = scene_figure(scene)

    names = [t.name for t in figure.data]
    assert "inlet (patch) — 142 of 400 faces" in names
    problems = next(t for t in figure.data if t.legendgroup == "PROBLEM")
    assert (problems.name, problems.marker.symbol, problems.marker.color) == (
        "checkMesh: skewFaces — 30 locations", "x", KIND_STYLE["PROBLEM"][0])
    cuts = [t for t in figure.data if t.legendgroup == "CUT"]
    assert len(cuts) == 2 and all(t.mode == "lines" for t in cuts)
    assert figure.layout.uirevision == "mesh"


def test_figure_payload_is_binary_arrays(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (10, 10, 10))
    text = scene_figure(build_mesh_scene(poly, {}, ())).to_json()
    assert '"bdata"' in text  # numpy arrays travel as base64, not number lists


def test_patch_type_colours_are_distinct() -> None:
    types = ("INLET", "OUTLET", "ROTATING_WALL", "SLIP", "INTERFACE")
    colours = [KIND_STYLE[t][0] for t in types]
    assert len(set(colours)) == len(colours)
    assert KIND_STYLE["PROBLEM"][0] not in colours
    assert np.all([KIND_STYLE[t][1] for t in types])
