"""Write the bad .msh files of the M1c browser checklist (tests/fixtures/msh/m1/bad/).

    python tests/fixtures/msh/m1/make_bad_files.py

Each is cut or edited from a committed M1 mesh so that exactly one BLOCKING
rule applies before any conversion; BAD maps each file to that rule's code
(checked in tests/unit/mesh_import/test_m1c_bad_files.py). The rules found
only on other files are listed in OTHER.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

M1 = Path(__file__).parent
BAD_DIR = M1 / "bad"


def _text(name: str) -> str:
    return (M1 / name).read_text("latin-1")


def _cut(name: str, marker: str, keep: int) -> str:
    """The file up to `keep` lines after the first line starting with `marker`."""
    lines = _text(name).split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(marker))
    return "\n".join(lines[: start + keep]) + "\n"


def _no_volume() -> str:
    text = _text("box_22.msh")
    return text.replace('$PhysicalNames\n4\n', '$PhysicalNames\n3\n').replace(
        '3 4 "fluid"\n', "")


def _no_zones() -> str:
    lines = _text("fluent_box.msh").split("\n")
    return "\n".join(line for line in lines if not line.startswith(("(39 ", "(45 ")))


BAD: dict[str, tuple[str, Callable[[], str]]] = {
    "empty.msh": ("MSH_FILE_EMPTY", lambda: ""),
    "not_a_mesh.msh": ("MSH_FORMAT_UNKNOWN", lambda: "solid cube\nendsolid cube\n"),
    "gmsh_bad_header.msh": ("MSH_HEADER_INVALID", lambda: "$MeshFormat\n4.1\n$EndMeshFormat\n"),
    "gmsh_no_volume.msh": ("GMSH_NO_PHYSICAL_VOLUME", _no_volume),
    "gmsh_no_nodes.msh": ("MSH_FILE_TRUNCATED", lambda: _cut("box_22.msh", "$Nodes", 0)),
    "gmsh_cut_short.msh": ("MSH_FILE_UNREADABLE", lambda: _cut("box_41.msh", "$Nodes", 6)),
    "fluent_2d.msh": ("FLUENT_2D", lambda: _text("fluent_box.msh").replace("(2 3)", "(2 2)", 1)),
    "fluent_no_zones.msh": ("FLUENT_NO_ZONES", _no_zones),
    "fluent_bad_header.msh": ("MSH_HEADER_INVALID",
                              lambda: _text("fluent_box.msh").replace("(2 3)\n", "", 1)),
}
# BLOCKING rules shown with committed files or a project setting instead.
OTHER = {
    "box_v1.msh": "MSH_FORMAT_UNSUPPORTED",  # Gmsh MSH 1
    "box_22_nogroups.msh": "GMSH_NO_PHYSICAL_GROUPS",
    "box_22_nowalls.msh": "GMSH_UNGROUPED_FACES",  # after converting (box or run)
    "fluent_box.msh": "MSH_NO_CELL_ZONE",  # as a rotating mesh: one cell zone only
}


def main(target: Path = BAD_DIR) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, (_, make) in BAD.items():
        (target / name).write_bytes(make().encode("latin-1"))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else BAD_DIR)
