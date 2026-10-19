"""M1-MSH: what a .msh file is, read from its content (machines.msh_import.detect).

The files are the committed M1 test meshes (tests/fixtures/msh/m1), and
hand-edited copies of them for the cases the converters crash on or get
wrong (E0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from machines.msh_import import detect
from machines.msh_import.detect import MeshFormat, file_sha256, inspect_mesh_file
from tests.fixtures.msh.configs import M1

BOX = ((0.0, 0.0, 0.0), (1.0, 0.5, 0.5))
ROTATING_PATCHES = {"inlet", "outlet", "y_min", "y_max", "z_min", "z_max", "ami_stat",
                    "ami_rot", "rotor"}


def codes(path: Path) -> list[str]:
    return [i.code for i in inspect_mesh_file(path).issues]


@pytest.mark.parametrize("name, version", [("box_22.msh", "2.2"), ("box_41.msh", "4.1")])
def test_ascii_gmsh_gives_names_zones_and_the_box(name: str, version: str) -> None:
    info = inspect_mesh_file(M1 / name)

    assert (info.format, info.version, info.binary) == (MeshFormat.GMSH, version, False)
    assert info.patches == ("inlet", "outlet", "walls") and info.cell_zones == ("fluid",)
    assert info.box == BOX and info.nodes and info.readable and info.issues == ()


def test_the_extension_is_never_used(tmp_path: Path) -> None:
    renamed = tmp_path / "mesh.cas"
    renamed.write_bytes((M1 / "fluent_box.msh").read_bytes())
    gmsh_as_txt = tmp_path / "mesh.txt"
    gmsh_as_txt.write_bytes((M1 / "box_22.msh").read_bytes())

    assert inspect_mesh_file(renamed).format is MeshFormat.FLUENT
    assert inspect_mesh_file(gmsh_as_txt).format is MeshFormat.GMSH


def test_binary_gmsh_is_read_but_has_no_raw_box() -> None:
    info = inspect_mesh_file(M1 / "box_22_bin.msh")

    assert info.format is MeshFormat.GMSH and info.binary and info.version == "2.2"
    assert info.patches == ("inlet", "outlet", "walls") and info.box is None
    assert "converted mesh" in info.box_note


def test_binary_gmsh_without_the_gmsh_package_is_blocking(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner answer 1 and decision 1: without gmsh only a binary file stops,
    with the fix (save as MSH 2.2 or 4.1 ASCII)."""
    monkeypatch.setattr(detect, "gmsh_available", lambda: False)
    info = inspect_mesh_file(M1 / "box_22_bin.msh")
    (issue,) = info.issues

    assert issue.code == "GMSH_BINARY_NEEDS_GMSH" and issue.severity.value == "BLOCKING"
    assert "MSH 2.2" in issue.suggested_action and "ASCII" in issue.suggested_action
    assert not info.readable


@pytest.mark.parametrize("name", ["box_22.msh", "box_41.msh", "rotating_41.msh"])
def test_ascii_22_and_41_need_no_gmsh_package(monkeypatch: pytest.MonkeyPatch,
                                              name: str) -> None:
    monkeypatch.setattr(detect, "gmsh_available", lambda: False)
    assert codes(M1 / name) == []


def test_another_gmsh_version_needs_the_package_when_ascii(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "v40.msh"
    path.write_bytes((M1 / "box_41.msh").read_bytes().replace(b"4.1 0 8", b"4.0 0 8", 1))
    monkeypatch.setattr(detect, "gmsh_available", lambda: False)
    assert codes(path) == ["GMSH_VERSION_NEEDS_GMSH"]
    monkeypatch.setattr(detect, "gmsh_available", lambda: True)
    assert codes(path) == []


def test_gmsh_without_physical_groups_is_blocking_with_the_fix() -> None:
    info = inspect_mesh_file(M1 / "box_22_nogroups.msh")
    (issue,) = info.issues

    assert issue.code == "GMSH_NO_PHYSICAL_GROUPS" and issue.severity.value == "BLOCKING"
    assert "Physical Surface" in issue.suggested_action
    assert "Physical Volume" in issue.suggested_action
    assert "patch0" in issue.explanation


def test_gmsh_without_a_physical_volume_is_blocking(tmp_path: Path) -> None:
    text = (M1 / "box_22.msh").read_text("utf-8")
    path = tmp_path / "surfaces_only.msh"
    path.write_text(text.replace('4\n2 1 "inlet"', '3\n2 1 "inlet"', 1)
                    .replace('3 4 "fluid"\n', "", 1), encoding="utf-8")
    assert codes(path) == ["GMSH_NO_PHYSICAL_VOLUME"]


def test_msh_1_is_blocking() -> None:
    info = inspect_mesh_file(M1 / "box_v1.msh")
    assert info.version == "1" and codes(M1 / "box_v1.msh") == ["MSH_FORMAT_UNSUPPORTED"]
    assert "patch0" in info.issues[0].explanation


def test_ungrouped_faces_are_not_seen_before_converting() -> None:
    # Faces in no group are found after conversion (defaultFaces, test_m1_pipeline).
    info = inspect_mesh_file(M1 / "box_22_nowalls.msh")
    assert info.patches == ("inlet", "outlet") and info.issues == ()


def test_fluent_gives_patches_types_zones_cells_and_the_box() -> None:
    info = inspect_mesh_file(M1 / "fluent_box.msh")

    assert (info.format, info.dimension, info.binary) == (MeshFormat.FLUENT, 3, False)
    assert info.patches == ("inlet", "outlet", "walls")
    assert info.patch_kinds == {"inlet": "pressure-outlet", "outlet": "pressure-outlet",
                                "walls": "wall"}
    assert info.cell_zones == ("fluid-1",) and info.declared_cells == 16
    assert info.box == BOX and info.issues == ()


def test_fluent_with_two_cell_zones() -> None:
    info = inspect_mesh_file(M1 / "fluent_rotating.msh")

    assert info.cell_zones == ("stationary", "rotating")
    assert set(info.patches) == ROTATING_PATCHES
    assert info.box == ((-1.0, -1.0, -1.0), (3.0, 1.0, 1.0))


def test_a_2d_fluent_mesh_is_blocking(tmp_path: Path) -> None:
    """Owner decision 2: fluent3DMeshToFoam only; a 2D mesh stops."""
    path = tmp_path / "flat.msh"
    path.write_bytes((M1 / "fluent_box.msh").read_bytes().replace(b"(2 3)", b"(2 2)", 1))
    assert codes(path) == ["FLUENT_2D"]


def test_a_fluent_file_cut_short_is_blocking_before_converting(tmp_path: Path) -> None:
    """E0: fluent3DMeshToFoam converts a truncated file with exit 0 to a
    broken mesh. Its zone names, written at the end, are missing."""
    data = (M1 / "fluent_rotating.msh").read_bytes()
    path = tmp_path / "cut.msh"
    path.write_bytes(data[: len(data) // 2])
    info = inspect_mesh_file(path)
    assert [i.code for i in info.issues] in (["FLUENT_NO_ZONES"], ["MSH_FILE_UNREADABLE"])
    assert not info.readable


def test_a_fluent_file_without_its_header_is_blocking(tmp_path: Path) -> None:
    text = (M1 / "fluent_box.msh").read_text("latin-1")
    path = tmp_path / "noheader.msh"
    path.write_text(text.replace("(12 (0 ", "(0 (0 ", 1), encoding="latin-1")
    assert codes(path) == ["MSH_HEADER_INVALID"]


def test_binary_fluent_sections_are_a_warning_and_give_no_raw_box(tmp_path: Path) -> None:
    text = (M1 / "fluent_box.msh").read_text("latin-1")
    start = text.index("(10 (3 ")
    end = text.index("))", start) + 2
    path = tmp_path / "binary_nodes.msh"
    path.write_text(text[:start] + "(3010 (3 1 2d 1 3)\n(\x00\x01\x02)End of Binary Section"
                    " 3010)\n" + text[end:], encoding="latin-1")
    info = inspect_mesh_file(path)

    assert info.binary and info.box is None and "converted mesh" in info.box_note
    assert [i.code for i in info.issues] == ["FLUENT_BINARY_UNTESTED"]
    assert info.issues[0].severity.value == "WARNING" and info.readable


@pytest.mark.parametrize("content, code", [
    (b"", "MSH_FILE_EMPTY"),
    (b"\x89HDF\r\n\x1a\n" + bytes(64), "MSH_FORMAT_UNKNOWN"),
    (b"solid part\nendsolid part\n", "MSH_FORMAT_UNKNOWN"),
    (b"$MeshFormat\n4.1 0 8\n", "MSH_HEADER_INVALID"),
])
def test_files_that_would_crash_or_confuse_a_converter_stop_here(
        tmp_path: Path, content: bytes, code: str) -> None:
    """Owner review: an empty file crashed gmshToFoam (exit 134); never in front
    of the user."""
    path = tmp_path / "input.msh"
    path.write_bytes(content)
    info = inspect_mesh_file(path)
    assert [i.code for i in info.issues] == [code] and not info.readable


def test_a_missing_file_is_blocking(tmp_path: Path) -> None:
    assert codes(tmp_path / "missing.msh") == ["MSH_FILE_MISSING"]


def test_the_hash_is_the_content_hash(tmp_path: Path) -> None:
    import hashlib

    data = (M1 / "box_22.msh").read_bytes()
    assert file_sha256(M1 / "box_22.msh", chunk=4096) == hashlib.sha256(data).hexdigest()
