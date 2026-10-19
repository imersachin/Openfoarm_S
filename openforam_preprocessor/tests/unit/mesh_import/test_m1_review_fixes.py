"""M1-MSH: fixes after the M1b testing review (fake OpenFOAM, tests/fakes_msh.py).

HIGH: a file cut short inside a node section raised RuntimeError; a Gmsh file
without $Nodes passed detection and crashed gmshToFoam (exit 134); Fluent
zone sections with an extra field or extra spaces were not read. MEDIUM: the
scratch conversion's promotion through the service, and promotion refused
for the right reason. LOW: the import report's zone radius, a stopped
import's mesh, patch evidence from another file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from machines.config import MachineProjectConfig
from machines.msh_import.cases import IMPORTED_CASE
from machines.msh_import.detect import inspect_mesh_file
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT,
    IMPORT_EXECUTABLES,
    IMPORT_REPORT,
)
from machines.pipeline import MachinePipeline, MachineRunResult
from machines.service import MachineService, _CheckCache
from machines.validation import check_all
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.machines.configs import vec
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1

TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES)


def lines_after(name: str, marker: str, keep: int, target: Path) -> Path:
    """The file cut `keep` lines after the first line starting with `marker`."""
    text = (M1 / name).read_text("latin-1").split("\n")
    start = next(i for i, line in enumerate(text) if line.startswith(marker))
    target.write_text("\n".join(text[: start + keep]) + "\n", encoding="latin-1")
    return target


def codes(path: Path) -> list[str]:
    return [i.code for i in inspect_mesh_file(path).issues]


# --- HIGH: files cut short -----------------------------------------------------------------

@pytest.mark.parametrize("name, marker", [("fluent_box.msh", "(10 (3 "),
                                          ("box_22.msh", "$Nodes"),
                                          ("box_41.msh", "$Nodes")])
def test_a_file_cut_inside_its_nodes_is_blocking_not_an_exception(
        tmp_path: Path, name: str, marker: str) -> None:
    path = lines_after(name, marker, 6, tmp_path / name)
    info = inspect_mesh_file(path)  # no RuntimeError

    assert [i.code for i in info.issues] == ["MSH_FILE_UNREADABLE"]
    assert "ends" in info.issues[0].message  # inside the nodes, or early
    config = MachineProjectConfig.model_validate(configs.box(path))
    assert "MSH_FILE_UNREADABLE" in {i.code for i in check_all(config).issues}


def test_gmsh_without_nodes_is_blocking(tmp_path: Path) -> None:
    path = lines_after("box_22.msh", "$Nodes", 0, tmp_path / "no_nodes.msh")
    assert codes(path) == ["MSH_FILE_TRUNCATED"]
    assert "$Nodes" in inspect_mesh_file(path).issues[0].message


def test_gmsh_cut_inside_its_elements_is_blocking(tmp_path: Path) -> None:
    path = lines_after("box_22.msh", "$Elements", 5, tmp_path / "cut_elements.msh")
    assert codes(path) == ["MSH_FILE_TRUNCATED"]
    assert "end of $Elements" in inspect_mesh_file(path).issues[0].message


def test_a_whole_gmsh_file_still_reads(tmp_path: Path) -> None:
    for name in ("box_22.msh", "box_41.msh", "rotating_41.msh", "box_22_bin.msh"):
        assert inspect_mesh_file(M1 / name).readable, name


# --- HIGH: Fluent zone sections -----------------------------------------------------------

def test_a_zone_section_with_an_extra_field_is_read(tmp_path: Path) -> None:
    """fluent3DMeshToFoam reads "(39 (12 wall walls 1)())" (review probe)."""
    text = (M1 / "fluent_box.msh").read_text("latin-1")
    path = tmp_path / "extra.msh"
    path.write_text(text.replace("(39 (12 wall walls)())", "(39 (12 wall walls 1)())")
                    .replace("(39 (1 fluid fluid-1)())", "(39 ( 1  fluid  fluid-1 )())"),
                    encoding="latin-1")
    info = inspect_mesh_file(path)

    assert info.issues == () and info.patches == ("inlet", "outlet", "walls")
    assert info.patch_kinds["walls"] == "wall" and info.cell_zones == ("fluid-1",)


def test_a_zone_section_that_cannot_be_read_is_never_dropped(tmp_path: Path) -> None:
    text = (M1 / "fluent_box.msh").read_text("latin-1")
    path = tmp_path / "odd.msh"
    path.write_text(text.replace("(39 (12 wall walls)())", "(39 (12 wall)())"),
                    encoding="latin-1")
    info = inspect_mesh_file(path)

    assert [i.code for i in info.issues] == ["FLUENT_ZONE_UNREADABLE"]
    assert info.issues[0].details["sections"] == ["(39 (12 wall)())"]


# --- MEDIUM: the scratch conversion through the service --------------------------------------

class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)
        self.runner = FakeMshRunner()
        self.service = MachineService(
            tmp_path / "project", env=self.env, system_probe=lambda _: PLENTY,
            pipeline=MachinePipeline(self.runner, environment=self.env,
                                     system_probe=lambda _: PLENTY),
            check_cache=_CheckCache())

    def run(self, data: dict[str, Any]) -> MachineRunResult:
        self.service.save(data)
        config = self.service.saved_config()
        assert config is not None
        return self.service.pipeline.run_sync(self.service.root, config)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_box_conversion_then_units_then_run_converts_once(setup: Setup) -> None:
    """Owner answer 4 through the service: convert_for_box, confirm mm, run."""
    path = setup.service.store_mesh_file("rotating_41.msh",
                                         (M1 / "rotating_41.msh").read_bytes())
    assert setup.service.convert_for_box(path).issues == ()
    result = setup.run(configs.rotating(path, units="mm"))

    assert result.succeeded, result.message
    assert setup.runner.names().count("gmshToFoam") == 1
    assert setup.runner.names()[1] == "transformPoints"
    assert f"{IMPORT}:scratch" in result.reused


def test_a_scratch_conversion_of_the_file_before_it_changed_is_not_used(
        setup: Setup) -> None:
    path = setup.service.store_mesh_file("rotating_41.msh",
                                         (M1 / "rotating_41.msh").read_bytes())
    setup.service.convert_for_box(path)  # the real tool identity
    path.write_bytes(path.read_bytes() + b"\n")  # changed after the box was shown
    result = setup.run(configs.rotating(path))  # the new hash is chosen again

    assert result.succeeded
    assert setup.runner.names().count("gmshToFoam") == 2
    assert f"{IMPORT}:scratch" not in result.reused


def test_force_converts_again_instead_of_promoting(setup: Setup) -> None:
    path = setup.service.store_mesh_file("rotating_41.msh",
                                         (M1 / "rotating_41.msh").read_bytes())
    setup.service.convert_for_box(path)
    setup.service.save(configs.rotating(path))
    config = setup.service.saved_config()
    assert config is not None
    result = setup.service.pipeline.run_sync(setup.service.root, config, force=True)

    assert result.succeeded and setup.runner.names().count("gmshToFoam") == 2
    assert f"{IMPORT}:scratch" not in result.reused


# --- LOW --------------------------------------------------------------------------------------

def test_the_report_radius_follows_the_origin_when_the_import_is_reused(
        setup: Setup) -> None:
    path = setup.service.store_mesh_file("rotating_41.msh",
                                         (M1 / "rotating_41.msh").read_bytes())
    data = configs.rotating(path)
    setup.run(data)
    data["imported_mesh"]["zones"][0]["origin"] = vec(0, 1, 1)
    result = setup.run(data)
    report = json.loads((setup.service.root / IMPORT_REPORT).read_text("utf-8"))

    assert IMPORT in result.reused
    # The fake's points are the box corners (y, z = -1 or 1): the farthest from
    # the axis through (0, 1, 1) is 2 sqrt(2) away.
    assert report["zone_radius_m"]["rotor_zone"] == pytest.approx(8**0.5)


def test_a_stopped_import_leaves_no_mesh(setup: Setup) -> None:
    path = setup.service.store_mesh_file("rotating_41.msh",
                                         (M1 / "rotating_41.msh").read_bytes())
    setup.runner.default_faces = 5
    result = setup.run(configs.rotating(path))

    assert not result.succeeded
    assert not (setup.service.root / "cases" / IMPORTED_CASE / "constant/polyMesh").exists()
