"""M2b: schema version 4 (owner approved after M2b), as M1's 2 -> 3.

A version 3 project (as main at M2b's merge wrote it) opens and meshes
unchanged: the same meshing inputs, every stage of a mesh made before the
upgrade reused. A version 4 project on that older code (1b417ad, from git) is
refused as written by a newer version, a machine of several files included,
and never as an unknown field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from machines.config import MACHINE_SCHEMA_VERSION, MachineProjectConfig
from machines.operations import MACHINE_EXECUTABLES
from machines.pipeline import MachinePipeline
from machines.project_store import CONFIG_PATH, LAST_MESHED_PATH
from machines.service import MachineService, _CheckCache
from machines.validation import parse_config
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs as stl_configs
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import mesh_file
from tests.unit.mesh_import import test_m1_schema_version as m1
from tests.unit.mesh_import.test_m2b_parts import machine

BEFORE = "1b417ad"  # main at M2b's merge: schema version 3


def test_the_version_is_4() -> None:
    assert MACHINE_SCHEMA_VERSION == 4


@pytest.mark.parametrize("kind", ["stl", "imported_mesh"])
def test_a_version_3_project_loads_as_version_4(tmp_path: Path, kind: str) -> None:
    data = (stl_configs.hawt(tmp_path) if kind == "stl"
            else configs.rotating(mesh_file("rotating_41.msh", tmp_path)))
    data["schema_version"] = 3
    config, issues = parse_config(data)
    assert config is not None and issues == () and config.schema_version == 4
    assert config == MachineProjectConfig.model_validate({**data, "schema_version": 4})


def as_version_3(path: Path, key: str) -> None:
    record = json.loads(path.read_text("utf-8"))
    record[key]["schema_version"] = 3
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def test_a_version_3_project_meshed_before_the_upgrade_is_reused(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)
    runner = FakeMachineRunner()
    service = MachineService(tmp_path / "project", env=env, system_probe=lambda _: PLENTY,
                             pipeline=MachinePipeline(runner, environment=env,
                                                      system_probe=lambda _: PLENTY),
                             check_cache=_CheckCache())
    service.save(stl_configs.hawt(tmp_path / "geometry"))
    first = service.saved_config()
    assert first is not None and service.pipeline.run_sync(service.root, first).succeeded
    control = service.pipeline.run_sync(service.root, first)
    as_version_3(service.root / CONFIG_PATH, "config")
    as_version_3(service.root / LAST_MESHED_PATH, "config")
    again = service.saved_config()
    assert again is not None and again.schema_version == 4
    ran = len(runner.calls)
    result = service.pipeline.run_sync(service.root, again)
    assert result.succeeded and result.executed == control.executed
    assert result.reused == control.reused and len(runner.calls) == ran


@pytest.fixture(scope="module")
def before_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    original = m1.MAIN
    m1.MAIN = BEFORE
    try:
        tree = m1._main_tree(tmp_path_factory.mktemp("before"))
    finally:
        m1.MAIN = original
    if tree is None:
        pytest.skip(f"git or commit {BEFORE} not available")
    return tree


@pytest.mark.parametrize("kind", ["parts", "imported_mesh", "stl"])
def test_older_code_says_a_version_4_project_is_from_a_newer_version(
        before_tree: Path, tmp_path: Path, kind: str) -> None:
    data: dict[str, Any] = (machine() if kind == "parts" else
                            stl_configs.hawt(tmp_path / "geometry") if kind == "stl" else
                            configs.rotating(mesh_file("rotating_41.msh", tmp_path)))
    saved = MachineProjectConfig.model_validate(data).model_dump(mode="json")
    assert saved["schema_version"] == 4
    found = m1.open_on_main(before_tree, saved, tmp_path)
    assert found["supported"] == 3 and not found["loaded"]
    assert [i[0] for i in found["issues"]] == ["SCHEMA_VERSION_NEWER"]
    assert "parts" not in found["issues"][0][1]


def test_older_code_still_opens_a_version_3_project(before_tree: Path, tmp_path: Path) -> None:
    data = {**configs.rotating(mesh_file("rotating_41.msh", tmp_path)), "schema_version": 3}
    found = m1.open_on_main(before_tree, data, tmp_path)
    assert found["loaded"] and found["issues"] == []
