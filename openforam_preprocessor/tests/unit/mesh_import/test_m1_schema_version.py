"""M1-MSH: schema version 3 (owner approved after M1b).

A version 2 project (as main wrote it) opens and meshes unchanged: the same
meshing inputs as main, no section stale, every stage of a mesh made before
the upgrade reused. A version 3 project on older code (main, cb07c48, from
git) is refused as written by a newer version, with or without an imported
mesh, and never as an unknown field.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from machines.config import MACHINE_SCHEMA_VERSION, MachineProjectConfig
from machines.operations import MACHINE_EXECUTABLES
from machines.pipeline import MachinePipeline
from machines.profiles import default_registry
from machines.project_store import CONFIG_PATH, LAST_MESHED_PATH
from machines.service import MachineService, _CheckCache
from machines.validation import parse_config
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs as stl_configs
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1, mesh_file
from tests.fixtures.msh.m1.stl_baseline import MAIN_SCHEMA_VERSION, fingerprints

MAIN = "cb07c48"  # the M1-MSH branch point, schema version 2
PROJECT = Path(__file__).resolve().parents[3]


def test_the_version_is_3() -> None:
    assert MACHINE_SCHEMA_VERSION == 4 and MAIN_SCHEMA_VERSION == 2  # 4: M2b


# --- a version 2 project under version 3 ------------------------------------------------------

def test_a_version_2_project_has_main_s_meshing_inputs(tmp_path: Path) -> None:
    baseline = json.loads((M1 / "stl_baseline.json").read_text("utf-8"))
    assert fingerprints(tmp_path, schema_version=2) == baseline


def test_a_version_2_project_loads_as_version_3(tmp_path: Path) -> None:
    data = {**stl_configs.hawt(tmp_path), "schema_version": 2}
    config, issues = parse_config(data)

    assert config is not None and issues == ()
    assert config.schema_version == 4
    assert config == MachineProjectConfig.model_validate({**data, "schema_version": 4})


def as_version_2(path: Path, key: str) -> None:
    """Rewrite a saved record as version 2 code wrote it."""
    record = json.loads(path.read_text("utf-8"))
    record[key]["schema_version"] = 2
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def test_a_version_2_project_meshed_before_the_upgrade_is_reused(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "v2512", MACHINE_EXECUTABLES)
    runner = FakeMachineRunner()
    service = MachineService(tmp_path / "project", env=env, system_probe=lambda _: PLENTY,
                             pipeline=MachinePipeline(runner, environment=env,
                                                      system_probe=lambda _: PLENTY),
                             check_cache=_CheckCache())
    hawt = stl_configs.hawt(tmp_path / "geometry")
    service.save(hawt)
    first = service.saved_config()
    assert first is not None
    assert service.pipeline.run_sync(service.root, first).succeeded
    control = service.pipeline.run_sync(service.root, first)  # a rerun, no upgrade
    as_version_2(service.root / CONFIG_PATH, "config")
    as_version_2(service.root / LAST_MESHED_PATH, "config")

    again = service.saved_config()  # opened by the version 3 code
    assert again is not None and again.schema_version == 4
    sections = service.analyse({**hawt, "schema_version": 2},
                               default_registry().get("hawt")).sections  # type: ignore[arg-type]
    assert all(not s.stale_because for s in sections.values())
    ran = len(runner.calls)
    result = service.pipeline.run_sync(service.root, again)

    # As a rerun without the upgrade: only the checks run, no OpenFOAM command.
    assert result.succeeded and result.executed == control.executed
    assert result.reused == control.reused and len(runner.calls) == ran


# --- a version 3 project on older code -------------------------------------------------------

def _main_tree(target: Path) -> Path | None:
    """main's application code (no tests), or None without git or the commit."""
    git = shutil.which("git")
    if git is None:
        return None
    # Run in the project folder, git archive writes that folder only, as its root.
    archive = subprocess.run([git, "archive", MAIN], cwd=PROJECT,
                             capture_output=True, check=False)
    if archive.returncode != 0:
        return None
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
        tar.extractall(target, members=[m for m in tar.getmembers()
                                        if not m.name.startswith("tests/")],
                       filter="data")
    return target


@pytest.fixture(scope="module")
def main_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tree = _main_tree(tmp_path_factory.mktemp("main"))
    if tree is None:
        pytest.skip(f"git or commit {MAIN} not available")
    return tree


OPEN_ON_MAIN = """
import json, sys
sys.path.insert(0, sys.argv[1])
from machines.config import MACHINE_SCHEMA_VERSION
from machines.validation import parse_config
config, issues = parse_config(json.load(open(sys.argv[2], encoding="utf-8")))
print(json.dumps({"supported": MACHINE_SCHEMA_VERSION, "loaded": config is not None,
                  "issues": [[i.code, i.message, i.suggested_action] for i in issues]}))
"""


def open_on_main(tree: Path, data: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    done = subprocess.run([sys.executable, "-c", OPEN_ON_MAIN, str(tree), str(path)],
                          cwd=tree, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    result: dict[str, Any] = json.loads(done.stdout)
    return result


@pytest.mark.parametrize("kind", ["imported_mesh", "stl"])
def test_older_code_says_a_version_3_project_is_from_a_newer_version(
        main_tree: Path, tmp_path: Path, kind: str) -> None:
    if kind == "stl":
        data = stl_configs.hawt(tmp_path / "geometry")
    else:
        data = configs.rotating(mesh_file("rotating_41.msh", tmp_path))
    saved = MachineProjectConfig.model_validate(data).model_dump(mode="json")
    assert saved["schema_version"] == 4
    found = open_on_main(main_tree, saved, tmp_path)

    assert found["supported"] == 2 and not found["loaded"]
    assert [i[0] for i in found["issues"]] == ["SCHEMA_VERSION_NEWER"]
    code, message, suggestion = found["issues"][0]
    assert "written by a newer version" in message and "imported_mesh" not in message
    assert "newer application version" in suggestion


def test_older_code_still_opens_a_version_2_project(main_tree: Path, tmp_path: Path) -> None:
    """The check above is the version, not the old code failing to load at all."""
    data = {**stl_configs.hawt(tmp_path / "geometry"), "schema_version": 2}
    found = open_on_main(main_tree, data, tmp_path)
    assert found["loaded"] and found["issues"] == []
