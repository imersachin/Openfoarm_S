"""G4: HAWT preset (H1), machine project store (H4), resource estimate."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from geometry.importer import import_stl
from machines.assembly import MachineCaseGenerator, meshing_steps
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.preflight import estimate_cells
from machines.presets.hawt import G0_UNVERIFIED, HAWT_VALUES, hawt_draft, hawt_rotor
from machines.project_store import (
    CONFIG_PATH,
    LAST_MESHED_PATH,
    MachineProjectStore,
    read_last_meshed,
    write_last_meshed,
)
from machines.validation import load_bodies, validate_machine
from tests.fixtures.machines import configs
from tests.fixtures.machines.g0.common import make_geometry
from vawt.config import Axis
from vawt.rotor_metrics import compute_rotor_metrics


@pytest.fixture
def rotor_path(tmp_path: Path) -> Path:
    make_geometry.hawt(tmp_path)
    return tmp_path / "hawt_rotor.stl"


def mesh_of(path: Path):  # type: ignore[no-untyped-def]
    mesh = import_stl(path).mesh
    assert mesh is not None
    return mesh


# --- the rotor about its axis --------------------------------------------------------------

def test_three_blade_rotor_is_measured_about_its_axis(rotor_path: Path) -> None:
    mesh = mesh_of(rotor_path)
    box = compute_rotor_metrics(mesh.vertices, Axis.X)

    rotor = hawt_rotor(mesh, Axis.X)

    # The bounding box is off the axis for three blades; the area centroid is not.
    assert abs(box.centre[1]) > 0.1
    assert rotor.centre == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    assert rotor.diameter == pytest.approx(1.0013, abs=1e-3)  # tip r 0.5 plus the chord corner
    assert box.diameter is not None and box.diameter < rotor.diameter
    assert (rotor.axial_min, rotor.axial_max) == pytest.approx((-0.08, 0.08))  # the hub


def test_rotation_centre_can_be_given(rotor_path: Path) -> None:
    rotor = hawt_rotor(mesh_of(rotor_path), Axis.X, centre_uv=(0.1, 0.0))

    assert rotor.centre[1:] == (0.1, 0.0)
    assert rotor.diameter > 1.0013 + 0.1


# --- the draft ------------------------------------------------------------------------------

def test_draft_reproduces_g0_r3(rotor_path: Path) -> None:
    rotor = hawt_rotor(mesh_of(rotor_path), Axis.X)
    d = rotor.diameter

    config = hawt_draft({"project_name": "HAWT", "source": configs.stl(rotor_path)}, rotor)

    domain, zone = config.domain, config.rotating_zones[0]
    assert config.machine.value == "HAWT" and config.flow_axis is Axis.X
    assert domain is not None and domain.kind == "CYLINDER"
    assert (domain.axis_min, domain.axis_max) == pytest.approx((-2 * d, 5 * d))
    assert domain.diameter == pytest.approx(4 * d) and domain.cell_size == pytest.approx(d / 8)
    shape = zone.shape
    assert shape.kind == "CYLINDER" and shape.diameter == pytest.approx(1.2 * d)
    assert (shape.axis_min, shape.axis_max) == pytest.approx((-0.08 - 0.04 * d, 0.08 + 0.04 * d))
    assert zone.cell_size == pytest.approx(d / 40)
    assert [(p.name, p.type.value, p.source.ref) for p in config.patches] == [
        ("rotor", "ROTATING_WALL", "rotor"), ("inlet", "INLET", "axis_min"),
        ("outlet", "OUTLET", "axis_max"), ("side", "SLIP", "side")]
    stopping = [i.code for i in validate_machine(config.model_dump(mode="json")).issues
                if i.is_stopping]
    assert stopping == []


def test_every_distance_names_its_source() -> None:
    distances = {k: v for k, v in HAWT_VALUES.items() if k != "zone_point_radius"}

    assert all(v.source == G0_UNVERIFIED for v in distances.values())
    assert "unverified" in G0_UNVERIFIED and "not a recommendation" in G0_UNVERIFIED
    assert all(v.unit == "D" for v in HAWT_VALUES.values())


def test_draft_needs_project_name_and_source(rotor_path: Path) -> None:
    rotor = hawt_rotor(mesh_of(rotor_path), Axis.X)
    with pytest.raises(ValueError):  # pydantic.ValidationError
        hawt_draft({"project_name": "HAWT"}, rotor)


@pytest.mark.parametrize("axis", ["y", "z"])
def test_draft_on_other_axes(tmp_path: Path, axis: str) -> None:
    make_geometry.hawt(tmp_path)
    mesh = mesh_of(tmp_path / "hawt_rotor.stl")
    order = {"y": (1, 2, 0), "z": (2, 0, 1)}[axis]  # x -> axis (a proper rotation)
    mesh.vertices = np.asarray(mesh.vertices)[:, order]
    mesh.export(tmp_path / "turned.stl")

    rotor = hawt_rotor(mesh_of(tmp_path / "turned.stl"), Axis(axis))
    config = hawt_draft({"project_name": "HAWT", "source": configs.stl(tmp_path / "turned.stl")},
                        rotor)

    stopping = [i.code for i in validate_machine(config.model_dump(mode="json")).issues
                if i.is_stopping]
    assert stopping == [] and config.flow_axis is Axis(axis)


# --- project store ---------------------------------------------------------------------------

def test_store_saves_revisions(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.hawt(tmp_path / "g"))
    store = MachineProjectStore(tmp_path / "p")

    assert store.load() is None and not store.exists()
    first, second = store.save(config), store.save(config)

    assert (first.revision, second.revision) == (1, 2)
    assert (tmp_path / "p" / CONFIG_PATH).is_file()
    assert sorted(p.name for p in store.history_dir.iterdir()) == ["000001.json",
                                                                   "000002.json"]
    loaded = store.load()
    assert loaded is not None and MachineProjectConfig.model_validate(loaded.raw) == config


def test_unreadable_store(tmp_path: Path) -> None:
    store = MachineProjectStore(tmp_path)
    store.config_path.parent.mkdir(parents=True)
    store.config_path.write_text("{not json")

    with pytest.raises(ValueError):
        store.load()


def test_last_meshed(tmp_path: Path) -> None:
    config = MachineProjectConfig.model_validate(configs.hawt(tmp_path / "g"))

    assert read_last_meshed(tmp_path) is None
    write_last_meshed(tmp_path, "run1", config, "passed")
    last = read_last_meshed(tmp_path)

    assert last is not None and (last.run_id, last.mesh_status) == ("run1", "passed")
    (tmp_path / LAST_MESHED_PATH).write_text("[]")
    assert read_last_meshed(tmp_path) is None


# --- resource estimate -------------------------------------------------------------------------

def test_estimate_per_case(tmp_path: Path) -> None:
    data = configs.hawt(tmp_path)
    config = MachineProjectConfig.model_validate(data)
    meshes, _ = load_bodies(config)

    plain = estimate_cells(config, meshes, read_imported(config))
    data["bodies"][0]["layers"] = {"enabled": True, "count": 3}
    layered_config = MachineProjectConfig.model_validate(data)
    layered = estimate_cells(layered_config, meshes, read_imported(layered_config))

    assert set(plain.per_case) == {"domain", "zone_rotating"}
    assert plain.background["domain"] > 0 and plain.surface["zone_rotating"] > 0
    assert plain.total == sum(plain.per_case.values())
    assert layered.layers["zone_rotating"] > 0 and layered.total > plain.total


# --- untyped meshes (H3) -------------------------------------------------------------------------

def test_untyped_meshes_and_retype_steps(tmp_path: Path) -> None:
    data = configs.pole(tmp_path, motion="SPLIT")
    config = MachineProjectConfig.model_validate(data)
    meshes, _ = load_bodies(config)

    cases = MachineCaseGenerator().render(config, read_imported(config), meshes, typed=False)
    typed = MachineCaseGenerator().render(config, read_imported(config), meshes)
    steps = [s.argv for s in meshing_steps(cases)]

    assert "type wall" not in cases.zones[0].dictionaries["system/snappyHexMeshDict"]
    assert all(t == "patch" for c in (*cases.domain, *cases.zones) for t in c.expected.values())
    assert cases.merged.expected == typed.merged.expected  # the final types
    assert cases.retype == {"blades": "wall", "pole": "wall", "pole_rotating": "wall"}
    assert typed.retype == {}
    assert ("foamDictionary", "constant/polyMesh/boundary", "-entry", "entry0/pole/type",
            "-set", "wall") in steps
    assert steps.index(("createPatch", "-overwrite", "-case", ".")) < steps.index(
        ("foamDictionary", "constant/polyMesh/boundary", "-entry", "entry0/blades/type",
         "-set", "wall"))
