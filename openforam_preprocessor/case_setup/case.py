"""Write the ready-to-run case (docs/rotating_machinery.md section 11; G6).

The case is its own directory, `case/`, next to the meshing cases:

    case/0/                fields (case_setup.fields)
    case/constant/         polyMesh (a copy of the assembled mesh), physics
    case/system/           controlDict, fvSchemes, fvSolution

Case setup reads the assembled mesh and copies it; it never writes into
cases/ (invariant 14), so the meshing cases and their checks stay reusable.
Identical inputs give identical files.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from case_setup.dictionaries import dictionaries
from case_setup.fields import fields
from case_setup.setup import CaseSetup, resolve
from machines.config import MachineProjectConfig
from machines.domains import SurfaceInfo
from openfoam.dictionary import OpenFOAMFileWriter
from visualization.foam_reader import read_boundary

CASE_DIR = "case"
POLY_MESH = "constant/polyMesh"
_MESH_EXCLUDED = ("sets",)  # checkMesh diagnostics, not part of the mesh


def mesh_boundary(poly_mesh: Path) -> tuple[tuple[str, str], ...]:
    """(name, type) of every patch of a mesh, in boundary order."""
    return tuple((p.name, p.patch_type) for p in read_boundary(poly_mesh / "boundary"))


def render(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
           poly_mesh: Path) -> tuple[CaseSetup, dict[str, str]]:
    """The resolved setup and every generated file (relative path -> text)."""
    setup = resolve(config, surfaces, mesh_boundary(poly_mesh))
    return setup, {**fields(setup), **dictionaries(setup)}


def write_case(project_root: Path, files: dict[str, str], poly_mesh: Path) -> Path:
    """Write the case: the files (write-if-changed), a fresh copy of the mesh,
    and no generated file left over from an earlier setup (a k-omega field
    after a switch to laminar). Solver output (time directories) is the
    user's and is never touched."""
    case = project_root / CASE_DIR
    case.mkdir(parents=True, exist_ok=True)
    for directory in ("0", "constant", "system"):
        for path in sorted((case / directory).rglob("*")) if (case / directory).is_dir() else ():
            relative = path.relative_to(case).as_posix()
            if (path.is_file() and relative not in files
                    and not relative.startswith(POLY_MESH + "/")):
                path.unlink()
    for relative, body in sorted(files.items()):
        OpenFOAMFileWriter.write_if_changed(case / relative, body)
    target = case / POLY_MESH
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(poly_mesh, target, ignore=shutil.ignore_patterns(*_MESH_EXCLUDED))
    return case


def case_files(project_root: Path, files: dict[str, str]) -> list[Path]:
    """The generated files and the mesh copy: the case setup operation's
    outputs (solver output is not one)."""
    case = project_root / CASE_DIR
    mesh = case / POLY_MESH
    copied = sorted(p for p in mesh.rglob("*") if p.is_file()) if mesh.is_dir() else []
    return [*(case / relative for relative in sorted(files)), *copied]
