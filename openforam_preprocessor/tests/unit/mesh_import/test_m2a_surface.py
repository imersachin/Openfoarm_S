"""M2a commit 1: binary imported meshes, read through the surface the tools
extract (machines.msh_import.surface; fake OpenFOAM, tests/fakes_msh.py; real
v2512 in tests/integration/test_m2a_real.py).

Every imported case writes binary; nothing reads its points, faces or owner
in Python. Areas, the zone radius, the tip radius, the box and the 3D pane
come from one extracted surface per case; the 3D pane's mesh view of an
imported project shows the boundary by patch and two cuts.
"""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core.issues import IssueStage
from machines.case_generator import _control_dict
from machines.msh_import import surface
from machines.msh_import.cases import IMPORTED_CASE, MERGED_CASE, import_cases
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT,
    IMPORT_EXECUTABLES,
    MESH_VIEWS,
)
from machines.pipeline import MachinePipeline
from machines.previews import cluster
from machines.service import MachineService, _CheckCache
from openfoam.runner import CommandResult
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner, vtp
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from vawt.service import PreviewView
from visualization.foam_reader import FoamReadError, read_boundary, read_points

TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES)
Data = dict[str, Any]


class Setup:
    def __init__(self, tmp_path: Path, runner: FakeMshRunner | None = None) -> None:
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)
        self.runner = runner or FakeMshRunner()
        self.service = MachineService(
            tmp_path / "project", env=self.env, system_probe=lambda _: PLENTY,
            pipeline=MachinePipeline(self.runner, environment=self.env,
                                     system_probe=lambda _: PLENTY),
            check_cache=_CheckCache())
        self.root = self.service.root

    def upload(self, name: str = "rotating_41.msh") -> Path:
        return self.service.store_mesh_file(name, (M1 / name).read_bytes())

    def run(self, data: Data) -> Any:
        self.service.save(copy.deepcopy(data))
        config = self.service.saved_config()
        assert config is not None
        return self.service.pipeline.run_sync(self.root, config)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


# --- binary ------------------------------------------------------------------------------------

def test_every_imported_case_writes_binary_and_the_stl_path_keeps_ascii(
        tmp_path: Path) -> None:
    config = configs.rotating(configs.mesh_file("rotating_41.msh", tmp_path))
    from machines.validation import parse_config

    parsed, _ = parse_config(config)
    assert parsed is not None
    cases = import_cases(parsed)
    for files in (cases.imported, cases.merged):
        assert "writeFormat     binary;" in files["system/controlDict"]
    assert "writeFormat     ascii;" in _control_dict()  # STL meshing, unchanged


def test_the_boundary_of_a_binary_mesh_is_read_its_points_are_not(setup: Setup) -> None:
    """A boundary file is text under a binary header (v2512); the reader takes
    it, and still refuses binary points rather than guess."""
    assert setup.run(configs.rotating(setup.upload())).succeeded
    poly = setup.root / "cases" / MERGED_CASE / "constant/polyMesh"
    assert b"format      binary;" in (poly / "points").read_bytes()[:400]
    assert {p.name for p in read_boundary(poly / "boundary")} >= {"inlet", "rotor"}
    with pytest.raises(FoamReadError, match="binary"):
        read_points(poly / "points")


# --- the surface -------------------------------------------------------------------------------

def test_the_import_extracts_the_surface_with_each_zone(setup: Setup) -> None:
    assert setup.run(configs.rotating(setup.upload())).succeeded
    found = surface.load(setup.root / "cases" / IMPORTED_CASE)
    assert found is not None
    assert set(found.zones) == {"rotating", "stationary"}
    assert {"inlet", "outlet", "rotor", "ami_stat", "ami_rot"} <= set(found.patches)
    for name in ("topoSet", "extract"):
        assert (setup.root / f"logs/{IMPORTED_CASE}_surface").glob(f"*_{name}.log")
    dictionary = (setup.root / f"cases/{IMPORTED_CASE}_surface/system/topoSetDict")
    assert "source cellToFace; sets (zoneSurface_rotating_cells); option outside;" in \
        dictionary.read_text("utf-8")


def test_nothing_is_written_through_the_links_of_the_surface_case(setup: Setup) -> None:
    assert setup.run(configs.rotating(setup.upload())).succeeded
    poly = setup.root / f"cases/{IMPORTED_CASE}_surface/constant/polyMesh"
    linked = [p for p in poly.iterdir() if p.is_symlink()]
    assert {p.name for p in linked} == {"points", "faces", "owner", "neighbour", "boundary",
                                       "cellZones"}
    assert all(p.resolve().parent == (setup.root / "cases" / IMPORTED_CASE
                                       / "constant/polyMesh").resolve() for p in linked)


def test_the_surface_of_a_changed_mesh_is_not_used_and_the_run_imports_again(
        setup: Setup) -> None:
    """An imported mesh without a current surface (an M1 project, ASCII, before
    M2a) is not "converted" for the UI, and the next run imports it again."""
    data = configs.rotating(setup.upload())
    assert setup.run(data).succeeded
    config = setup.service.saved_config()
    assert config is not None and setup.service.imported_patch_areas(config)
    (setup.root / f"cases/{IMPORTED_CASE}_surface/{surface.REGIONS_FILE}").unlink()

    assert setup.service.imported_patch_areas(config) is None  # as before conversion
    result = setup.run(data)
    assert result.succeeded and IMPORT in result.executed


def test_regions_come_from_the_extraction_log(tmp_path: Path) -> None:
    folder = tmp_path / "cases" / "c"
    (folder / "constant/polyMesh").mkdir(parents=True)
    target = surface.surface_dir(folder)
    target.mkdir(parents=True)
    (target / surface.SURFACE_FILE).write_text("x", encoding="utf-8")
    assert not surface.index(folder, "Writing merged surface\nEnd\n")  # no region named
    assert surface.index(folder, "surfZone 1 : b\nsurfZone 0 : a\n")
    record = json.loads((target / surface.REGIONS_FILE).read_text("utf-8"))
    assert record["regions"] == ["a", "b"]


def test_a_vtp_file_is_read_as_openfoam_writes_it_and_others_are_refused(
        tmp_path: Path) -> None:
    path = tmp_path / "s.vtp"
    path.write_text(vtp(np.eye(3), np.arange(3), np.array([3]), np.array([0])), "utf-8")
    arrays = surface.read_vtp(path)
    assert arrays["Points"].shape == (3, 3) and arrays["offsets"].tolist() == [3]
    path.write_text(path.read_text("utf-8").replace(
        "header_type='UInt64'", "header_type='UInt64' compressor='vtkZLibDataCompressor'"),
        "utf-8")
    with pytest.raises(surface.SurfaceError, match="compressed"):
        surface.read_vtp(path)


def test_a_header_of_another_size_is_read(tmp_path: Path) -> None:
    raw = np.arange(6, dtype="<f4").tobytes()
    data = base64.b64encode(np.uint32(len(raw)).tobytes() + raw).decode()
    path = tmp_path / "s.vtp"
    path.write_text("<VTKFile type='PolyData' header_type='UInt32'>"
                    f"<DataArray type='Float32' Name='Points' NumberOfComponents='3' "
                    f"format='binary'>{data}</DataArray></VTKFile>", "utf-8")
    assert surface.read_vtp(path)["Points"].tolist() == [[0, 1, 2], [3, 4, 5]]


# --- the 3D pane -------------------------------------------------------------------------------

def test_the_mesh_view_of_an_imported_project_shows_patches_and_two_cuts(
        setup: Setup) -> None:
    """M1c could not draw it (cut planes from rotating_zones, which an imported
    project leaves empty); now the boundary by patch and the cuts the run made."""
    result = setup.run(configs.rotating(setup.upload()))
    assert result.succeeded and MESH_VIEWS in result.executed
    data = setup.service.preview(PreviewView.MESH)
    assert data.scene is not None and not data.issues
    shown = {s.name.split(" ")[0]: s.kind for s in data.scene.surfaces}
    assert shown["rotor"] == "ROTATING_WALL" and shown["ami_stat"] == "INTERFACE"
    assert [line.name for line in data.scene.lines] == [
        "cut through the rotor_zone axis", "cut across the rotor_zone axis at x = 1 m"]
    key = setup.service.preview_key(PreviewView.MESH)
    assert key.signature is not None
    assert setup.service.preview(PreviewView.MESH).built is False  # stored, not rebuilt


def test_the_cuts_are_drawn_on_the_planes_they_were_made_on(setup: Setup) -> None:
    assert setup.run(configs.rotating(setup.upload())).succeeded
    planes = surface.read_cuts(setup.root / "cases" / MERGED_CASE)
    dictionary = (setup.root / f"cases/{MERGED_CASE}_surface/system/cutsDict").read_text()
    assert len(planes) == 2
    # Through the zone's mid-span: the fake zone's surface spans x -1 to 3.
    assert "along { type cuttingPlane; point (1 0 0); normal (0 0 1);" in dictionary
    assert "across { type cuttingPlane; point (1 0 0); normal (1 0 0);" in dictionary


class _FailCuts(FakeMshRunner):
    """postProcess fails in the merged mesh's surface case only."""

    async def run(self, argv: Sequence[str], *, case_root: Path, log_name: str,
                  stage: IssueStage = IssueStage.OPENFOAM_EXECUTION,
                  timeout_seconds: float | None = None, env: Mapping[str, str] | None = None,
                  cancel_event: Any = None, logs_dir: Path | None = None,
                  on_start: Callable[[int], None] | None = None) -> CommandResult:
        self.fail = ("postProcess" if case_root.name == f"{MERGED_CASE}_surface" else None)
        return await super().run(argv, case_root=case_root, log_name=log_name, stage=stage,
                                 timeout_seconds=timeout_seconds, env=env,
                                 cancel_event=cancel_event, logs_dir=logs_dir,
                                 on_start=on_start)


def test_a_view_that_cannot_be_made_is_a_warning_and_the_run_goes_on(
        tmp_path: Path) -> None:
    setup = Setup(tmp_path, _FailCuts())
    data = configs.rotating(setup.upload())
    result = setup.run(data)

    assert result.succeeded, result.message
    found = {i.code: i.severity.value for i in result.issues}
    assert found["MESH_VIEW_FAILED"] == "WARNING"
    again = setup.run(data)  # nothing recorded: the views are tried again
    assert MESH_VIEWS in again.executed


def test_the_import_does_not_extract_a_surface_for_a_mesh_it_rejects(setup: Setup) -> None:
    result = setup.run(configs.rotating(setup.upload(), cell_zone="missing"))
    assert not result.succeeded
    assert "surfaceMeshExtract" not in setup.runner.names()


def test_cluster_keeps_a_small_patch_and_coarsens_a_large_one() -> None:
    n = 60
    x, y = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    points = np.column_stack([x.ravel(), y.ravel(), np.zeros(n * n)])
    quads = [(i * n + j, i * n + j + 1, (i + 1) * n + j + 1)
             for i in range(n - 1) for j in range(n - 1)]
    triangles = np.array(quads)
    same, kept = cluster(points, triangles[:10], 100)
    assert len(kept) == 10 and len(same) == len(np.unique(triangles[:10]))
    vertices, coarse = cluster(points, triangles, 500)
    assert 0 < len(coarse) <= 500
    assert vertices.min(axis=0)[:2] == pytest.approx([0, 0], abs=0.05)
    assert vertices.max(axis=0)[:2] == pytest.approx([1, 1], abs=0.05)
