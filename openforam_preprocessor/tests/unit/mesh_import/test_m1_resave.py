"""M1-MSH: the gmsh re-save (machines/msh_import/resave.py), with the real gmsh package.

A binary Gmsh file becomes MSH 2.2 ASCII with its physical groups, which
gmshToFoam reads (E0). Skipped when the optional gmsh package is missing;
the import then stops binary files before any command (test_m1_detect.py).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from machines.msh_import import resave
from machines.msh_import.detect import MeshFormat, inspect_mesh_file
from tests.fixtures.msh.configs import M1

pytestmark = pytest.mark.skipif(importlib.util.find_spec("gmsh") is None,
                                reason="the optional gmsh package is not installed")


def test_a_binary_file_is_resaved_as_ascii_22_with_its_groups(tmp_path: Path) -> None:
    target = tmp_path / "resaved.msh"
    done = subprocess.run([sys.executable, resave.__file__, str(M1 / "box_22_bin.msh"),
                           str(target)], capture_output=True, text=True, check=False)
    info = inspect_mesh_file(target)

    assert done.returncode == 0, done.stderr
    assert (info.format, info.version, info.binary) == (MeshFormat.GMSH, "2.2", False)
    assert info.patches == ("inlet", "outlet", "walls") and info.cell_zones == ("fluid",)
    assert info.box == ((0.0, 0.0, 0.0), (1.0, 0.5, 0.5))
    assert inspect_mesh_file(M1 / "box_22.msh").nodes == info.nodes


def test_a_file_gmsh_cannot_read_fails_with_its_message(tmp_path: Path) -> None:
    broken = tmp_path / "broken.msh"
    broken.write_text("$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\nnot numbers\n",
                      encoding="utf-8")
    done = subprocess.run([sys.executable, resave.__file__, str(broken),
                           str(tmp_path / "out.msh")], capture_output=True, text=True,
                          check=False)
    assert done.returncode == 1 and "could not re-save" in done.stderr


def test_wrong_arguments_print_the_usage() -> None:
    done = subprocess.run([sys.executable, resave.__file__], capture_output=True, text=True,
                          check=False)
    assert done.returncode == 2 and "resave.py <in.msh> <out.msh>" in done.stderr
