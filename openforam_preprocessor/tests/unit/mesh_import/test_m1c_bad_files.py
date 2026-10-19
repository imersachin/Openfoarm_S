"""M1c: the bad files of the browser checklist each give their one BLOCKING rule.

The committed files in tests/fixtures/msh/m1/bad/ are what make_bad_files.py
writes, so the checklist and the tests use the same files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from machines.config import MachineProjectConfig
from machines.msh_import.detect import inspect_mesh_file
from machines.validation import check_all
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1, mesh_file
from tests.fixtures.msh.m1.make_bad_files import BAD, BAD_DIR, OTHER, main


def blocking(path: Path) -> list[str]:
    return [i.code for i in inspect_mesh_file(path).issues if i.severity.value == "BLOCKING"]


def test_the_committed_bad_files_are_what_the_script_writes(tmp_path: Path) -> None:
    main(tmp_path)
    for name in BAD:
        assert (tmp_path / name).read_bytes() == (BAD_DIR / name).read_bytes(), name


@pytest.mark.parametrize("name", sorted(BAD))
def test_each_bad_file_has_its_one_blocking_rule(name: str) -> None:
    assert blocking(BAD_DIR / name) == [BAD[name][0]]


@pytest.mark.parametrize("name", ["box_v1.msh", "box_22_nogroups.msh"])
def test_the_committed_bad_meshes_are_blocked_before_converting(name: str) -> None:
    assert blocking(M1 / name) == [OTHER[name]]


def test_a_mesh_with_one_cell_zone_cannot_rotate(tmp_path: Path) -> None:
    data = configs.rotating(mesh_file("fluent_box.msh", tmp_path), cell_zone="fluid-1")
    found = {i.code for i in check_all(MachineProjectConfig.model_validate(data)).issues}
    assert OTHER["fluent_box.msh"] in found
