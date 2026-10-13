"""V6 display data (machines.previews): scenes, thinning, cuts, problem
locations, arrows, and the store that builds each scene once per content hash."""

from __future__ import annotations

import os
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from machines import previews
from machines.config import MachineProjectConfig
from machines.previews import (
    INTERFACE,
    KEEP_PER_VIEW,
    PROBLEM,
    UNASSIGNED,
    Plane,
    PreviewLimits,
    PreviewStore,
    allocate,
    box_face_mesh,
    build_mesh_scene,
    build_setup_scene,
    cut_planes,
    cut_segments,
    flow_arrows,
    mesh_preview,
    rotation_arrows,
    setup_preview,
)
from machines.validation import parse_config
from machines.vawt_migration import from_vawt
from tests.fixtures.machines import configs
from tests.fixtures.vawt.drafts import preset_draft
from tests.foam_block import write_block_mesh, write_set
from vawt.config import Axis
from vawt.validation import parse_config as parse_vawt

REPO = Path(__file__).resolve().parents[3]
KINDS = {"inlet": "INLET", "outlet": "OUTLET", "walls": "WALL"}


def machine(name: str, directory: Path, **update: Any) -> MachineProjectConfig:
    raw = getattr(configs, name)(directory)
    raw.update(update)
    config, issues = parse_config(raw)
    assert config is not None, issues
    return config


def by_name(items: Any) -> dict[str, Any]:
    return {item.name: item for item in items}


# --- thinning -------------------------------------------------------------------------

def test_allocate_keeps_everything_under_the_cap() -> None:
    assert allocate([10, 20, 30], 100, 5) == [10, 20, 30]


def test_allocate_is_proportional_with_a_minimum_per_item() -> None:
    # Floors 100 each; the spare 300 is shared by size (300, 300, 1500 above the floors).
    assert allocate([400, 400, 1600], 600, 100) == [142, 142, 314]
    assert allocate([5, 10_000], 1_000, 100) == [5, 995]  # the small patch stays whole


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_faces"):
        PreviewLimits(max_faces=0)
    with pytest.raises(ValueError, match="max_markers"):
        PreviewLimits(max_markers=-1)


# --- the mesh scene ---------------------------------------------------------------------

def test_mesh_scene_has_every_patch_by_kind_with_face_counts(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (3, 2, 2), patches={
        "x_min": ("inlet", "patch"), "x_max": ("outlet", "patch"),
        "y_min": ("rotating_outer_rot", "cyclicAMI"), "y_max": ("free", "patch"),
        "z_min": ("walls", "wall"), "z_max": ("walls", "wall")})
    scene = build_mesh_scene(poly, {"inlet": "INLET", "rotating_outer_rot": "WALL"}, ())

    found = {s.name: (s.kind, s.shown, s.total, len(s.triangles)) for s in scene.surfaces}
    assert found == {
        "inlet (patch)": ("INLET", 4, 4, 8),  # 2 x 2 quads, two triangles each
        "outlet (patch)": (UNASSIGNED, 4, 4, 8),  # not configured: no type to show
        "rotating_outer_rot (cyclicAMI)": (INTERFACE, 6, 6, 12),  # AMI always interface
        "free (patch)": (UNASSIGNED, 6, 6, 12),
        "walls (wall)": ("WALL", 12, 12, 24),
    }
    assert scene.notes == ()
    assert scene.bounds == ((0.0, 0.0, 0.0), (3.0, 2.0, 2.0))


def test_mesh_scene_thins_above_the_face_cap_with_a_note(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (20, 20, 20))  # 400 + 400 + 1600 boundary faces
    limits = PreviewLimits(max_faces=600, min_faces_per_patch=100)
    scene = build_mesh_scene(poly, KINDS, (), limits)

    assert [(s.name, s.shown, s.total) for s in scene.surfaces] == [
        ("inlet (patch)", 142, 400), ("outlet (patch)", 142, 400), ("walls (wall)", 314, 1600)]
    assert all(len(s.triangles) == 2 * s.shown for s in scene.surfaces)
    assert "Showing 598 of 2,400 boundary faces (display limit 600)." in scene.notes
    again = build_mesh_scene(poly, KINDS, (), limits)
    for first, second in zip(scene.surfaces, again.surfaces, strict=True):
        assert np.array_equal(first.vertices, second.vertices)
        assert np.array_equal(first.triangles, second.triangles)


def test_cut_draws_the_cell_outlines_on_the_plane(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (3, 2, 2))
    plane = Plane("cut x", (1.5, 0.0, 0.0), (1.0, 0.0, 0.0))
    scene = build_mesh_scene(poly, KINDS, (plane,))

    (cut,) = scene.lines
    assert (cut.name, cut.kind, cut.shown, cut.total) == ("cut x", "CUT", 12, 12)
    drawn = cut.points[np.all(np.isfinite(cut.points), axis=1)]
    assert np.allclose(drawn[:, 0], 1.5)
    # The 12 segments are the edges of the 2 x 2 grid of cells on the plane.
    segments = cut.points.reshape(-1, 3)[[i for i in range(len(cut.points)) if i % 3 != 2]]
    lengths = np.linalg.norm(segments[0::2] - segments[1::2], axis=1)
    assert np.allclose(lengths, 1.0)


def test_cut_through_mesh_points_has_no_division_by_zero(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (3, 2, 2))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a RuntimeWarning (divide by zero) fails
        scene = build_mesh_scene(poly, KINDS, (Plane("on grid", (1.0, 0, 0), (1.0, 0, 0)),))
    (cut,) = scene.lines
    drawn = cut.points[np.all(np.isfinite(cut.points), axis=1)]
    assert cut.total == 12 and np.allclose(drawn[:, 0], 1.0)


def test_cut_segments_of_a_concave_face_pair_up() -> None:
    # An L-shaped face crossed by y = 0.5 twice: two segments.
    points = np.array([[0, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0], [2, 0.2, 0],
                       [1, 0.2, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    faces = previews._Faces.of([np.arange(8)])
    segments = cut_segments(points, faces, (0, 0.5, 0), (0, 1, 0))
    assert segments.shape == (2, 2, 3)
    assert sorted(sorted(s[:, 0].round(6)) for s in segments) == [[0.0, 1.0], [2.0, 3.0]]


def test_cut_that_misses_the_mesh_says_so(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (2, 2, 2))
    scene = build_mesh_scene(poly, KINDS, (Plane("far cut", (9, 0, 0), (1, 0, 0)),))
    assert scene.lines == ()
    assert "The far cut does not cross the mesh." in scene.notes


def test_cut_segments_are_thinned_with_a_note(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (4, 6, 6))
    scene = build_mesh_scene(poly, KINDS, (Plane("cut", (2.5, 0, 0), (1, 0, 0)),),
                             PreviewLimits(max_cut_segments=10))
    (cut,) = scene.lines
    assert (cut.shown, cut.total) == (10, 84)  # 7 x 6 + 6 x 7 faces cross
    assert "Showing 10 of 84 cell edges on the cut (display limit 10)." in scene.notes


def test_problem_locations_by_set_class_and_region_sets_left_out(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (3, 2, 2))
    inlet = 20  # boundary starts after the 20 internal faces; the inlet comes first
    write_set(poly, "nonOrthoFaces", "faceSet", [inlet])
    write_set(poly, "zeroVolumeCells", "cellSet", [0, 11])
    write_set(poly, "nonManifoldPoints", "pointSet", [0])
    write_set(poly, "region0", "cellSet", list(range(12)))
    scene = build_mesh_scene(poly, KINDS, ())

    found = {m.name: m for m in scene.markers}
    assert set(found) == {"checkMesh: nonOrthoFaces", "checkMesh: zeroVolumeCells",
                          "checkMesh: nonManifoldPoints"}
    assert all(m.kind == PROBLEM for m in scene.markers)
    assert np.allclose(found["checkMesh: nonOrthoFaces"].points, [[0, 0.5, 0.5]])
    assert np.allclose(found["checkMesh: zeroVolumeCells"].points,
                       [[0.5, 0.5, 0.5], [2.5, 1.5, 1.5]])
    assert np.allclose(found["checkMesh: nonManifoldPoints"].points, [[0, 0, 0]])
    assert any("region0" in note and "not problems" in note for note in scene.notes)


def test_problem_locations_are_capped_with_a_note(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (3, 2, 2))
    write_set(poly, "skewFaces", "faceSet", list(range(52)))
    scene = build_mesh_scene(poly, KINDS, (), PreviewLimits(max_markers=10))
    (problems,) = scene.markers
    assert (problems.shown, problems.total) == (10, 52)
    assert "Showing 10 of 52 checkMesh problem locations (display limit 10)." in scene.notes


def test_unreadable_set_is_an_issue_not_a_crash(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path, (2, 1, 1))
    sets = poly / "sets"
    sets.mkdir()
    (sets / "broken").write_text("FoamFile { format binary; class faceSet; }\n")
    scene = build_mesh_scene(poly, KINDS, ())
    assert [i.code for i in scene.issues] == ["VIEW_SET_UNREADABLE"]
    assert len(scene.surfaces) == 3


def test_missing_mesh_is_an_issue(tmp_path: Path) -> None:
    scene = build_mesh_scene(tmp_path / "nothing", KINDS, ())
    assert scene.surfaces == ()
    assert [i.code for i in scene.issues] == ["VIEW_MESH_UNAVAILABLE"]


# --- the store ----------------------------------------------------------------------------

def _reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = previews.read_points

    def spy(path: Path) -> np.ndarray:
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(previews, "read_points", spy)
    return calls


def test_mesh_scene_is_built_once_and_then_loaded_without_reading_the_mesh(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))
    reads = _reads(monkeypatch)
    first = mesh_preview(tmp_path, poly, KINDS, ())
    assert (first.built, first.hashed, reads) == (True, True, ["points"])

    hashed: list[bool] = []
    original = previews.sha256_file
    monkeypatch.setattr(previews, "sha256_file", lambda p: hashed.append(True) or original(p))
    second = mesh_preview(tmp_path, poly, KINDS, ())
    assert (second.built, second.hashed, reads, hashed) == (False, False, ["points"], [])
    assert [s.name for s in second.scene.surfaces] == [s.name for s in first.scene.surfaces]
    assert np.array_equal(second.scene.surfaces[0].vertices, first.scene.surfaces[0].vertices)
    assert sorted(p.name for p in (tmp_path / "previews").iterdir()) == [
        f"mesh-{first.scene.key}.npz", "mesh.json"]


def test_touched_but_unchanged_mesh_is_hashed_not_rebuilt(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))
    first = mesh_preview(tmp_path, poly, KINDS, ())
    stat = (poly / "points").stat()
    os.utime(poly / "points", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    reads = _reads(monkeypatch)
    again = mesh_preview(tmp_path, poly, KINDS, ())
    assert (again.built, again.hashed, reads) == (False, True, [])
    assert again.scene.key == first.scene.key


def test_changed_mesh_is_rebuilt(tmp_path: Path) -> None:
    case = tmp_path / "case"
    first = mesh_preview(tmp_path, write_block_mesh(case, (3, 2, 2)), KINDS, ())
    second = mesh_preview(tmp_path, write_block_mesh(case, (4, 2, 2)), KINDS, ())
    assert second.built and second.scene.key != first.scene.key
    assert sum(s.total for s in second.scene.surfaces) == 40


def test_a_new_set_changes_the_key(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))
    first = mesh_preview(tmp_path, poly, KINDS, ())
    write_set(poly, "skewFaces", "faceSet", [20])
    second = mesh_preview(tmp_path, poly, KINDS, ())
    assert second.built and second.scene.key != first.scene.key
    assert [m.name for m in second.scene.markers] == ["checkMesh: skewFaces"]


def test_other_limits_or_planes_are_another_scene(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))
    first = mesh_preview(tmp_path, poly, KINDS, ())
    other = mesh_preview(tmp_path, poly, KINDS, (), PreviewLimits(max_faces=10))
    cut = mesh_preview(tmp_path, poly, KINDS, (Plane("c", (1.5, 0, 0), (1, 0, 0)),))
    assert len({first.scene.key, other.scene.key, cut.scene.key}) == 3
    assert other.built and cut.built


def test_corrupted_stored_scene_is_rebuilt(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))
    first = mesh_preview(tmp_path, poly, KINDS, ())
    (tmp_path / "previews" / f"mesh-{first.scene.key}.npz").write_bytes(b"not a zip")
    again = mesh_preview(tmp_path, poly, KINDS, ())
    assert again.built and len(again.scene.surfaces) == 3


def test_unreadable_mesh_is_not_stored(tmp_path: Path) -> None:
    result = mesh_preview(tmp_path, tmp_path / "missing" / "polyMesh", KINDS, ())
    assert [i.code for i in result.scene.issues] == ["VIEW_MESH_UNAVAILABLE"]
    assert not (tmp_path / "previews").exists()


def test_store_keeps_only_the_newest_scenes(tmp_path: Path) -> None:
    case = tmp_path / "case"
    keys = [mesh_preview(tmp_path, write_block_mesh(case, (n, 1, 1)), KINDS, ()).scene.key
            for n in range(1, 6)]
    stored = sorted(p.name for p in (tmp_path / "previews").glob("mesh-*.npz"))
    assert stored == sorted(f"mesh-{k}.npz" for k in keys[-KEEP_PER_VIEW:])


def test_store_index_is_per_view(tmp_path: Path) -> None:
    store = PreviewStore(tmp_path)
    assert store._indexed("mesh", "anything") is None
    (tmp_path / "previews").mkdir()
    (tmp_path / "previews" / "mesh.json").write_text("{broken")
    assert store._indexed("mesh", "anything") is None


# --- the setup scene ---------------------------------------------------------------------

def test_hawt_setup_scene(tmp_path: Path) -> None:
    config = machine("hawt", tmp_path)
    scene = build_setup_scene(config)

    kinds = {s.name: s.kind for s in scene.surfaces}
    assert kinds == {"rotor: body rotor (rotating)": "ROTATING_WALL",
                     "inlet: domain face axis_min": "INLET",
                     "outlet: domain face axis_max": "OUTLET",
                     "side: domain face side": "SLIP"}
    assert [(ln.name, ln.kind) for ln in scene.lines] == [
        ("domain cylinder", "DOMAIN"), ("rotating zone rotating", "ZONE")]
    (seeds,) = scene.markers
    assert seeds.labels == ("zone rotating", "domain")
    assert np.allclose(seeds.points, [[0.0013, 0.3013, 0.0113], [-1.4313, 0.0113, 0.0213]])
    frames = by_name(scene.patches)
    assert np.allclose(frames["inlet"].normal, (-1, 0, 0))  # out of the fluid
    assert np.allclose(frames["outlet"].normal, (1, 0, 0))
    (zone,) = scene.zones
    assert (zone.centre, zone.radius, zone.half_length) == ((0.0, 0.0, 0.0), 0.6, 0.12)
    assert scene.issues == ()  # geometry findings belong to the setup sections


def test_francis_setup_scene_shows_joints_and_imported_zone(tmp_path: Path) -> None:
    config = machine("francis", tmp_path)
    scene = build_setup_scene(config)

    joints = sorted(s.name for s in scene.surfaces if s.kind == INTERFACE)
    assert joints == sorted([
        "joint casing_out / guide_in: casing_out (casing)",
        "joint casing_out / guide_in: guide_in (guide)",
        "joint guide_out / runner_in: guide_out (guide)",
        "joint guide_out / runner_in: runner_in (runner)",
        "joint runner_out / draft_in: draft_in (draft)",
        "joint runner_out / draft_in: runner_out (runner)"])
    assert by_name(scene.surfaces)["runner_blades: region of runner"].kind == "ROTATING_WALL"
    assert scene.markers[0].labels == ("zone runner", "part casing", "part guide",
                                       "part draft")
    (zone,) = scene.zones
    assert zone.radius == pytest.approx(0.3) and zone.half_length == pytest.approx(0.1)


def test_box_domain_faces_point_out_of_the_box() -> None:
    lo, hi = (0.0, 0.0, 0.0), (2.0, 3.0, 4.0)
    for face, normal, area in (("x_min", (-1, 0, 0), 12), ("x_max", (1, 0, 0), 12),
                               ("y_min", (0, -1, 0), 8), ("y_max", (0, 1, 0), 8),
                               ("z_min", (0, 0, -1), 6), ("z_max", (0, 0, 1), 6)):
        mesh = box_face_mesh(lo, hi, face)
        assert np.allclose(mesh.face_normals, normal), face
        assert mesh.area == pytest.approx(area), face


def test_vawt_setup_scene_has_wake_box_and_box_patches(tmp_path: Path) -> None:
    from vawt.presets import PresetKind

    vawt, issues = parse_vawt(preset_draft(tmp_path, kind=PresetKind.EXTENDED))
    assert vawt is not None, issues
    config = from_vawt(vawt)
    scene = build_setup_scene(config)
    assert {ln.name for ln in scene.lines} >= {"domain box", "wake refinement box"}
    assert {s.kind for s in scene.surfaces} == {"ROTATING_WALL", "INLET", "OUTLET", "SLIP"}


def test_a_moved_body_is_also_shown_before_its_transform(tmp_path: Path) -> None:
    raw = configs.hawt(tmp_path)
    raw["bodies"][0]["source"].update(source_units="mm", scale=1000.0,
                                      translation={"x": 0.5, "y": 0.0, "z": 0.0})
    config, issues = parse_config(raw)
    assert config is not None, issues
    surfaces = by_name(build_setup_scene(config).surfaces)

    before = surfaces["body rotor before its transform (only converted mm → m)"]
    after = surfaces["rotor: body rotor (rotating)"]
    assert before.kind == previews.SOURCE
    # Units only: the file read as mm is 1000 x smaller; then scaled and shifted.
    assert np.allclose(before.vertices.min(0) * 1000 + (0.5, 0, 0),
                       after.vertices.min(0), atol=1e-5)


def test_unreadable_body_is_reported_and_the_rest_shown(tmp_path: Path) -> None:
    config = machine("hawt", tmp_path)
    (tmp_path / "hawt_rotor.stl").unlink()
    scene = build_setup_scene(config)
    assert [i.code for i in scene.issues] == ["VIEW_BODY_UNAVAILABLE"]
    assert len(scene.surfaces) == 3


def test_setup_scene_is_thinned_with_a_note(tmp_path: Path) -> None:
    config = machine("hawt", tmp_path)
    scene = build_setup_scene(config, PreviewLimits(max_faces=300, min_faces_per_patch=20))
    assert sum(s.shown for s in scene.surfaces) < sum(s.total for s in scene.surfaces)
    assert any(n.startswith("Showing ") and "surface triangles" in n for n in scene.notes)


def test_setup_scene_is_stored_and_reused(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    config = machine("hawt", tmp_path / "geometry")
    first = setup_preview(tmp_path, config)
    calls: list[int] = []
    monkeypatch.setattr(previews, "load_bodies", lambda c: calls.append(1) or ({}, ()))
    second = setup_preview(tmp_path, config)
    assert (first.built, second.built, second.hashed, calls) == (True, False, False, [])
    assert second.scene.zones == first.scene.zones
    assert second.scene.patches == first.scene.patches
    # Physics is not part of the stored scene: arrows follow it when drawn.
    with_physics = config.model_copy(update={"physics": _physics("POSITIVE")})
    assert setup_preview(tmp_path, with_physics).built is False


# --- arrows and cuts -------------------------------------------------------------------------

def _physics(direction: str | None, **inlets: Any) -> Any:
    from case_setup.physics import PhysicsConfig

    rotation: dict[str, Any] = {"speed": {"kind": "RPM", "rpm": 120}}
    if direction is not None:
        rotation["direction"] = direction
    return PhysicsConfig.model_validate({
        "fluid": {"density": 1.2, "kinematic_viscosity": 1.5e-5},
        "rotation": {"rotating": rotation}, "inlets": inlets})


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("direction", ["POSITIVE", "NEGATIVE"])
def test_rotation_arc_follows_the_right_hand_rule(tmp_path: Path, axis: str,
                                                   direction: str) -> None:
    config = machine("hawt", tmp_path, physics=_physics(direction).model_dump(mode="json"))
    config = config.model_copy(update={"rotating_zones": tuple(
        z.model_copy(update={"axis": Axis(axis)}) for z in config.rotating_zones)})
    scene = build_setup_scene(config)
    axis_arrow, rotation = rotation_arrows(config, scene)

    e = np.eye(3)["xyz".index(axis)]
    assert np.allclose(axis_arrow.head_direction, e)
    assert axis_arrow.label == f"rotating: rotation axis +{axis}"
    path = rotation.path
    zone = scene.zones[0]
    centre = np.asarray(zone.centre) + e * (zone.half_length + 0.3 * zone.radius)
    assert np.allclose((path - centre) @ e, 0)  # the arc lies in a plane normal to the axis
    turn = float(np.cross(path[0] - centre, path[5] - centre) @ e)
    assert (turn > 0) == (direction == "POSITIVE")
    assert rotation.defined and rotation.head is not None
    assert float(np.asarray(rotation.head_direction) @ (path[-1] - path[-2])) > 0
    seen = "counter-clockwise" if direction == "POSITIVE" else "clockwise"
    assert f"{direction.lower()} about +{axis} ({seen} seen from +{axis}), 120 rpm" \
        in rotation.label


def test_unset_rotation_is_drawn_without_a_head_and_says_so(tmp_path: Path) -> None:
    config = machine("hawt", tmp_path)
    scene = build_setup_scene(config)
    _, rotation = rotation_arrows(config, scene)
    assert (rotation.defined, rotation.head) == (False, None)
    assert rotation.label == "rotating: rotation not set"

    with_speed = config.model_copy(update={"physics": _physics(None)})
    _, rotation = rotation_arrows(with_speed, scene)
    assert rotation.label == "rotating: 120 rpm, direction not set"
    assert not rotation.defined


def test_flow_from_a_velocity_inlet(tmp_path: Path) -> None:
    config = machine("hawt", tmp_path)
    config = config.model_copy(update={"physics": _physics(
        "POSITIVE", inlet={"kind": "VELOCITY", "velocity": {"x": 0, "y": 3, "z": 4}})})
    scene = build_setup_scene(config)
    (arrow,), notes = flow_arrows(config, scene)
    assert np.allclose(arrow.head_direction, (0, 0.6, 0.8)) and notes == []
    assert arrow.label == "flow at inlet: inlet velocity (0, 3, 4) m/s"


def test_flow_from_a_flow_rate_inlet_is_into_the_domain(tmp_path: Path) -> None:
    config = machine("francis", tmp_path)
    config = config.model_copy(update={"physics": _physics(
        None, inlet={"kind": "FLOW_RATE", "volumetric_flow_rate": 0.1})})
    scene = build_setup_scene(config)
    (arrow,), _ = flow_arrows(config, scene)
    assert np.allclose(arrow.head_direction, -np.asarray(scene.patch("inlet").normal))
    assert np.allclose(arrow.path[-1], np.asarray(scene.patch("inlet").centroid)
                       + 0.15 * previews._scale(scene) * np.asarray(arrow.head_direction))


def test_flow_without_inlet_conditions(tmp_path: Path) -> None:
    hawt = machine("hawt", tmp_path / "h")
    (arrow,), _ = flow_arrows(hawt, build_setup_scene(hawt))
    assert np.allclose(arrow.head_direction, (1, 0, 0))
    assert arrow.label == "flow along +x (flow axis)"

    duct = machine("duct", tmp_path / "d")
    scene = build_setup_scene(duct)
    (arrow,), _ = flow_arrows(duct, scene)
    assert np.allclose(arrow.head_direction, -np.asarray(scene.patch("inlet").normal))
    assert "no inlet condition set" in arrow.label


DUCT_INWARD = (np.cos(np.radians(20)), np.sin(np.radians(20)), 0.0)  # into the duct at x 0


def _rewind(directory: Path, names: tuple[str, ...]) -> None:
    """Rewrite the duct's per-patch STL files with their triangles reversed."""
    import trimesh

    from tests.fixtures.machines.g0.common.make_geometry import write_ascii

    for name in names:
        mesh = trimesh.load_mesh(directory / f"{name}.stl", process=False)
        write_ascii(directory / f"{name}.stl",
                    [(name, trimesh.Trimesh(mesh.vertices, mesh.faces[:, ::-1], process=False))])


@pytest.mark.parametrize("rewound", [(), ("inlet", "outlet", "wall")])
def test_inlet_arrow_points_into_the_domain_however_the_stl_is_wound(
        tmp_path: Path, rewound: tuple[str, ...]) -> None:
    config = machine("duct", tmp_path)
    _rewind(tmp_path, rewound)
    scene = build_setup_scene(config)

    (arrow,), _ = flow_arrows(config, scene)
    assert arrow.defined and np.allclose(arrow.head_direction, DUCT_INWARD)
    assert np.allclose(scene.patch("inlet").normal, -np.asarray(DUCT_INWARD))  # out of fluid
    assert scene.issues == ()
    inward_note = "duct: its STL is wound with normals into the fluid; the arrows account for it."
    assert (inward_note in scene.notes) == bool(rewound)

    flow_rate = config.model_copy(update={"physics": _physics(
        None, inlet={"kind": "FLOW_RATE", "volumetric_flow_rate": 0.1})})
    (arrow,), _ = flow_arrows(flow_rate, scene)
    assert np.allclose(arrow.head_direction, DUCT_INWARD)


def test_regions_wound_differently_make_the_direction_uncertain(tmp_path: Path) -> None:
    config = machine("duct", tmp_path)
    _rewind(tmp_path, ("inlet",))  # the inlet inward-wound, the rest outward
    scene = build_setup_scene(config)

    (issue,) = scene.issues
    assert (issue.code, issue.severity.value) == ("VIEW_WINDING_UNCERTAIN", "WARNING")
    assert "regions are wound inconsistently" in issue.message
    assert not scene.patch("inlet").certain
    (arrow,), _ = flow_arrows(config, scene)
    assert (arrow.defined, arrow.head, arrow.head_direction) == (False, None, None)
    assert arrow.label == ("flow at inlet: direction uncertain (the surface's winding is "
                           "unknown)")
    along, _ = cut_planes(config, scene)  # no flow direction: the default plane
    assert along.normal == (0.0, 0.0, 1.0)

    from visualization.machine_scene import scene_figure

    figure = scene_figure(scene, [arrow])
    line = next(t for t in figure.data if t.name == arrow.label)
    assert line.line.dash == "dash" and not [t for t in figure.data if t.type == "cone"]


def test_stored_scene_keeps_the_uncertainty(tmp_path: Path) -> None:
    config = machine("duct", tmp_path / "g")
    _rewind(tmp_path / "g", ("inlet",))
    first = setup_preview(tmp_path, config)
    again = setup_preview(tmp_path, config)
    assert not again.built and not again.scene.patch("inlet").certain
    assert [i.code for i in again.scene.issues] == ["VIEW_WINDING_UNCERTAIN"]
    assert first.scene.patches == again.scene.patches


def test_flow_unknown_is_a_note(tmp_path: Path) -> None:
    config = machine("duct", tmp_path, patches=[])
    arrows, notes = flow_arrows(config, build_setup_scene(config))
    assert arrows == []
    assert notes == ["Flow direction unknown: no inlet condition, flow axis or inlet patch "
                     "gives one."]


def test_cut_planes(tmp_path: Path) -> None:
    hawt = machine("hawt", tmp_path / "h")  # flow along the axis
    along, across = cut_planes(hawt)
    assert along.normal == (0.0, 0.0, 1.0)  # a plane containing the x axis
    assert across.normal == (1.0, 0.0, 0.0) and across.origin == (0.0, 0.0, 0.0)
    assert across.name == "cut across the rotating axis at x = 0 m"

    pole = machine("pole", tmp_path / "p")  # axis z, flow x
    along, across = cut_planes(pole)
    assert along.normal == (0.0, 1.0, 0.0)  # contains z and x
    assert across.normal == (0.0, 0.0, 1.0)


def test_cut_across_an_imported_zone_is_at_its_mid_span(tmp_path: Path) -> None:
    raw = configs.francis(tmp_path)
    raw["rotating_zones"][0]["shape"]["origin"] = {"x": 0.0, "y": 0.0, "z": -0.3}  # on axis
    config, issues = parse_config(raw)
    assert config is not None, issues
    _, across = cut_planes(config, build_setup_scene(config))
    assert across.name == "cut across the runner axis at z = 0 m"  # the runner's middle
    assert cut_planes(config)[1].origin[2] == -0.3  # without the scene: only the origin


def test_flow_rate_inlet_without_its_surface_falls_back_to_the_flow_axis(
        tmp_path: Path) -> None:
    config = machine("pole", tmp_path, flow_axis="y")
    config = config.model_copy(update={"physics": _physics(
        None, inlet={"kind": "FLOW_RATE", "volumetric_flow_rate": 0.1})})
    arrows, notes = flow_arrows(config, previews.Scene("mesh", ""))  # no patch frames
    assert [a.label for a in arrows] == ["flow along +y (flow axis)"]
    assert notes == ["Inlet inlet: its surface is not shown, so its flow-rate direction "
                     "cannot be drawn."]
    along, _ = cut_planes(config, previews.Scene("mesh", ""))
    assert along.normal == (-1.0, 0.0, 0.0)  # contains the axis (z) and the flow (y)


@pytest.mark.parametrize("start", range(8))
def test_concave_face_cut_is_right_from_any_start_vertex(start: int) -> None:
    points = np.array([[0, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0], [2, 0.2, 0],
                       [1, 0.2, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    faces = previews._Faces.of([np.roll(np.arange(8), -start)])
    segments = cut_segments(points, faces, (0, 0.5, 0), (0, 1, 0))
    assert sorted(sorted(s[:, 0].round(6)) for s in segments) == [[0.0, 1.0], [2.0, 3.0]]


def test_inputs_changing_during_the_build_are_not_stored(tmp_path: Path) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))
    signatures = iter(["before", "after"])
    result = PreviewStore(tmp_path).get(
        "mesh", lambda: next(signatures), lambda: "hash",
        lambda key: build_mesh_scene(poly, KINDS, (), key=key))
    assert result.built and len(result.scene.surfaces) == 3
    assert [i.code for i in result.scene.issues] == ["VIEW_INPUTS_CHANGED"]
    assert not (tmp_path / "previews").exists()


def test_a_store_that_cannot_be_written_is_a_warning(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    poly = write_block_mesh(tmp_path / "case", (3, 2, 2))

    def refuse(*args: Any) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(PreviewStore, "_write", refuse)
    result = mesh_preview(tmp_path, poly, KINDS, ())
    assert len(result.scene.surfaces) == 3
    (issue,) = result.scene.issues
    assert issue.code == "VIEW_NOT_STORED" and "read-only" in issue.message


def test_pruning_tolerates_a_file_removed_meanwhile(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    store = PreviewStore(tmp_path)
    store.directory.mkdir()
    for n in range(5):
        (store.directory / f"mesh-{n}.npz").write_bytes(b"x")
    original = Path.stat

    def vanished(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "mesh-2.npz":
            raise FileNotFoundError(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanished)
    store._prune("mesh", keep=store.directory / "mesh-4.npz")  # no exception


# --- boundaries ------------------------------------------------------------------------------

def test_display_data_does_not_import_plotly_or_streamlit() -> None:
    probe = ("import sys, machines.previews, vawt.service; "
             "print(sorted(m for m in sys.modules if m.split('.')[0] in "
             "('plotly', 'streamlit')))")
    result = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True,
                            text=True, env={**os.environ, "PYTHONPATH": str(REPO)},
                            check=True)
    assert result.stdout.strip() == "[]"
