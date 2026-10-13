"""Write the ready-to-run case (docs/rotating_machinery.md section 11; G6).

The case is its own directory, `case/`, next to the meshing cases:

    case/0/                fields (case_setup.fields)
    case/constant/         polyMesh (a copy of the assembled mesh), physics
    case/system/           controlDict, fvSchemes, fvSolution
    case/.case_setup_files.json   the files the last setup generated
    case/CASE_STALE.txt    only while case/ no longer matches the project

Case setup reads the assembled mesh and copies it; it never writes into
cases/ (invariant 14), so the meshing cases and their checks stay reusable.
Identical inputs give identical files. A new setup removes only files an
earlier setup generated; files the user added (decomposeParDict, fvOptions)
and solver output are never touched.
"""

from __future__ import annotations

import json
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
GENERATED = ".case_setup_files.json"  # relative paths the last setup wrote
STALE_MARKER = "CASE_STALE.txt"
_MESH_EXCLUDED = ("sets",)  # checkMesh diagnostics, not part of the mesh
_GENERATED_DIRS = ("0/", "constant/", "system/")


def mesh_boundary(poly_mesh: Path) -> tuple[tuple[str, str], ...]:
    """(name, type) of every patch of a mesh, in boundary order."""
    return tuple((p.name, p.patch_type) for p in read_boundary(poly_mesh / "boundary"))


def render(config: MachineProjectConfig, surfaces: Sequence[SurfaceInfo],
           poly_mesh: Path) -> tuple[CaseSetup, dict[str, str]]:
    """The resolved setup and every generated file (relative path -> text)."""
    setup = resolve(config, surfaces, mesh_boundary(poly_mesh))
    return setup, {**fields(setup), **dictionaries(setup)}


def generated_before(case: Path) -> set[str]:
    """The files the last setup generated. Without a readable list (none yet)
    nothing counts as generated, so nothing is removed."""
    try:
        listed = json.loads((case / GENERATED).read_text("utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    return {p for p in listed if isinstance(p, str) and p.startswith(_GENERATED_DIRS)
            and ".." not in Path(p).parts and not p.startswith(POLY_MESH + "/")}


def write_case(project_root: Path, files: dict[str, str], poly_mesh: Path) -> Path:
    """Write the case: the files (write-if-changed), a fresh copy of the mesh,
    and the list of generated files. A file the last setup generated and this
    one does not (a k-omega field after a switch to laminar) is removed; any
    other file is the user's and is kept."""
    case = project_root / CASE_DIR
    case.mkdir(parents=True, exist_ok=True)
    for relative in sorted(generated_before(case) - set(files)):
        (case / relative).unlink(missing_ok=True)
    for relative, body in sorted(files.items()):
        OpenFOAMFileWriter.write_if_changed(case / relative, body)
    target = case / POLY_MESH
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(poly_mesh, target, ignore=shutil.ignore_patterns(*_MESH_EXCLUDED))
    listing = json.dumps({"files": sorted(files)}, indent=2) + "\n"
    if not (case / GENERATED).is_file() or (case / GENERATED).read_text("utf-8") != listing:
        (case / GENERATED).write_text(listing, encoding="utf-8", newline="\n")
    return case


def case_files(project_root: Path, files: dict[str, str]) -> list[Path]:
    """The generated files, their list and the mesh copy: the case setup
    operation's outputs (solver output is not one)."""
    case = project_root / CASE_DIR
    mesh = case / POLY_MESH
    copied = sorted(p for p in mesh.rglob("*") if p.is_file()) if mesh.is_dir() else []
    return [*(case / relative for relative in sorted(files)), case / GENERATED, *copied]


def mark_stale(project_root: Path, reason: str) -> bool:
    """Mark an existing case/ as not matching the project (it may hold solver
    results, so it is kept). False when there is no case/."""
    case = project_root / CASE_DIR
    if not case.is_dir():
        return False
    (case / STALE_MARKER).write_text(
        "This case does not match the current project; it was kept because it may hold "
        f"solver results.\nReason: {reason}\nSet up the case again before running the "
        "solver.\n", encoding="utf-8", newline="\n")
    return True


def clear_stale(project_root: Path) -> None:
    (project_root / CASE_DIR / STALE_MARKER).unlink(missing_ok=True)
