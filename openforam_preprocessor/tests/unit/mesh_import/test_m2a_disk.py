"""M2a commit 3: mesh files on disk (machines.disk), the quick summary of a .msh
file (machines.msh_import.scan), unnamed Fluent zones and file types (detect,
inspection), and the picker in the Imported mesh section.

Roots are the WSL home and the Windows drives; a path is used only inside
one (links out refused). A file on a Windows drive is copied into the project,
hashed while copying and checked before it is kept; the copy shows progress
and can be cancelled.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from machines import disk
from machines.msh_import.detect import fluent_face_zones, inspect_mesh_file
from machines.msh_import.inspection import inspect
from machines.msh_import.scan import summarize
from tests.fixtures.msh.configs import M1

MOUNTS = ("C:\\134 /mnt/c 9p rw,noatime,aname=drvfs;path=C:\\ 0 0\n"
          "E:\\134 /mnt/e 9p rw,noatime,aname=drvfs;path=E:\\ 0 0\n"
          "none /mnt/wsl tmpfs rw 0 0\n/dev/sdd / ext4 rw 0 0\n")


# --- roots and paths ---------------------------------------------------------------------------

def test_the_roots_are_the_wsl_home_and_each_windows_drive(tmp_path: Path) -> None:
    found = disk.roots(MOUNTS, home=tmp_path)
    assert [(r.label, str(r.path)) for r in found] == [
        ("WSL home", str(tmp_path.resolve())), ("C:", "/mnt/c"), ("E:", "/mnt/e")]


@pytest.mark.parametrize("given, expected", [
    ("E:\\Meshes\\Francis", "/mnt/e/Meshes/Francis"),
    ("e:/Meshes/Francis/GV.msh", "/mnt/e/Meshes/Francis/GV.msh"),
    ('"C:\\Users\\me\\My Meshes"', "/mnt/c/Users/me/My Meshes"),
    ("\\\\wsl$\\Ubuntu-24.04\\root\\meshes", "/root/meshes"),
    ("\\\\wsl.localhost\\Ubuntu-24.04\\root\\meshes\\", "/root/meshes"),
    ("/root/meshes", "/root/meshes"),
])
def test_windows_and_wsl_share_paths_are_converted(given: str, expected: str) -> None:
    assert str(disk.to_wsl(given, distro="Ubuntu-24.04").path) == expected


def test_a_path_of_another_distribution_or_a_relative_one_is_refused() -> None:
    other = disk.to_wsl("\\\\wsl$\\Debian\\home\\me", distro="Ubuntu-24.04")
    assert other.path is None and "the app runs in Ubuntu-24.04" in other.message
    relative = disk.to_wsl("meshes\\GV.msh", distro="Ubuntu-24.04")
    assert relative.path is None and "not a full path" in relative.message


def test_a_listing_shows_folders_and_msh_files_inside_a_root_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "meshes" / "Francis").mkdir(parents=True)
    (home / "meshes" / "GV.msh").write_bytes(b"(0 x)")
    (home / "meshes" / "notes.txt").write_text("x", "utf-8")
    (home / "meshes" / ".hidden.msh").write_bytes(b"(0 x)")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.msh").write_bytes(b"(0 x)")
    (home / "meshes" / "link.msh").symlink_to(outside / "secret.msh")
    (home / "meshes" / "away").symlink_to(outside)
    roots = (disk.Root("WSL home", home.resolve()),)

    found = disk.listing(home / "meshes", roots)
    assert [f.name for f in found.folders] == ["Francis"]
    assert [f.name for f in found.files] == ["GV.msh"]  # links out of the root left out
    refused = disk.listing(outside, roots)
    assert refused.path is None and "is not a folder inside WSL home" in refused.message
    assert disk.listing(home / "meshes" / "away", roots).path is None  # a link out


def test_a_windows_drive_path_is_known(tmp_path: Path) -> None:
    assert disk.on_windows_drive(Path("/mnt/c/Users/x.msh"))
    assert not disk.on_windows_drive(tmp_path / "x.msh")


def test_the_mesh_folder_setting_is_remembered(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    disk.write_setting("mesh_folder", "/mnt/e/Meshes", path)
    disk.write_setting("python", "/root/venvs/ofpp/bin/python", path)
    assert disk.read_settings(path) == {"mesh_folder": "/mnt/e/Meshes",
                                        "python": "/root/venvs/ofpp/bin/python"}
    assert disk.read_settings(tmp_path / "none.json") == {}


# --- the copy ------------------------------------------------------------------------------------

def source_file(tmp_path: Path, size: int = 3 * disk.CHUNK + 5) -> Path:
    path = tmp_path / "src" / "big.msh"
    path.parent.mkdir(parents=True)
    path.write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
    return path


def test_a_copy_is_kept_only_after_its_size_and_hash_are_read_back(tmp_path: Path) -> None:
    source = source_file(tmp_path)
    target = tmp_path / "project" / "inputs" / "mesh" / "big.msh"
    status = disk.copy_file(source, target, every=0.0)

    assert status.state == "done" and target.read_bytes() == source.read_bytes()
    assert status.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert not target.with_name("big.msh.part").exists()
    assert json.loads(disk.status_path(target).read_text("utf-8"))["state"] == "done"


def test_a_copy_that_does_not_match_is_not_kept(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    source = source_file(tmp_path)
    target = tmp_path / "out" / "big.msh"
    monkeypatch.setattr(disk, "_hash_file", lambda _p, _c: "0" * 64)
    status = disk.copy_file(source, target)
    assert status.state == "failed" and "does not match the original" in status.message
    assert not target.exists() and not target.with_name("big.msh.part").exists()


def test_a_copy_needs_free_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = source_file(tmp_path)
    monkeypatch.setattr(disk.shutil, "disk_usage", lambda _p: type("U", (), {"free": 10})())
    status = disk.copy_file(source, tmp_path / "out" / "big.msh")
    assert status.state == "failed" and "Not enough free space" in status.message


def test_a_copy_in_the_background_can_be_cancelled(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    source = source_file(tmp_path, 20 * disk.CHUNK)
    target = tmp_path / "out" / "big.msh"
    gate = threading.Event()
    real = disk.hashlib.sha256

    class Slow:
        def __init__(self) -> None:
            self.inner = real()

        def update(self, block: bytes) -> None:
            gate.wait(5)
            self.inner.update(block)

        def hexdigest(self) -> str:
            return self.inner.hexdigest()

    monkeypatch.setattr(disk.hashlib, "sha256", Slow)
    jobs = disk.CopyJobs()
    assert jobs.start(source, target)
    assert not jobs.start(source, target)  # one copy per target
    assert jobs.running(target) and jobs.cancel(target)
    gate.set()
    result = jobs.wait(target, 10)
    assert result is not None and result.state == "cancelled" and not target.exists()


def test_a_copy_left_running_by_a_stopped_app_shows_as_interrupted(tmp_path: Path) -> None:
    target = tmp_path / "out" / "big.msh"
    target.parent.mkdir(parents=True)
    disk.write_status(disk.CopyStatus("/mnt/e/big.msh", str(target), 100, copied=40))
    view = disk.status_view(target, disk.CopyJobs())
    assert view is not None and view["state"] == "interrupted"


# --- the quick summary, unnamed zones, types from the file ------------------------------------

# Zone ids are hex in section headers, decimal in name sections (v2512 E0 files).
FLUENT_UNNAMED = b"""(0 "test")
(2 3)
(10 (0 1 8 0 3))
(12 (0 1 2 0))
(13 (0 1 c 0))
(10 (1 1 8 1 3)(
0 0 0
1 0 0
1 1 0
0 1 0
0 0 1
1 0 1
1 1 1
0 1 1
))
(12 (2 1 2 1 4))
(13 (9 1 1 2 4)(
1 2 3 4 1 2
))
(13 (d 2 3 24 4)(
1 2 3 4 1 0
5 6 7 8 2 0
))
(13 (e 4 c 3 4)(
1 5 6 2 1 0
))
(39 (2 fluid rotor_cells)())
(39 (14 wall SC-Inlet)())
"""


def test_face_zones_come_from_the_face_section_headers() -> None:
    assert fluent_face_zones(FLUENT_UNNAMED) == {9: "interior", 13: "outflow", 14: "wall"}


def test_an_unnamed_zone_is_named_as_the_converter_names_it(tmp_path: Path) -> None:
    path = tmp_path / "unnamed.msh"
    path.write_bytes(FLUENT_UNNAMED)
    info = inspect_mesh_file(path)
    assert info.patches == ("SC-Inlet", "FaceGroup13")  # interior zone 9 is no patch
    assert info.patch_kinds["FaceGroup13"] == "outflow"
    note = next(i for i in info.issues if i.code == "MSH_UNNAMED_ZONES")
    assert note.severity.value == "INFO" and "FaceGroup13" in note.message


def test_a_wall_named_like_an_inlet_gets_an_info(tmp_path: Path) -> None:
    path = tmp_path / "unnamed.msh"
    path.write_bytes(FLUENT_UNNAMED)
    found = inspect(path)
    note = next(i for i in found.issues if i.code == "MSH_TYPE_FROM_FILE")
    assert note.severity.value == "INFO"
    assert note.message == "SC-Inlet: the name says inlet or outlet, the file types it wall."
    assert "patch table" in note.suggested_action


def test_the_summary_reads_only_the_headers(tmp_path: Path) -> None:
    path = tmp_path / "unnamed.msh"
    path.write_bytes(FLUENT_UNNAMED)
    found = summarize(path)
    assert (found.format, found.binary, found.declared_cells, found.cell_zones,
            found.boundary_zones, found.unnamed) == ("Fluent", False, 2, 1, 2, 1)
    gmsh = summarize(M1 / "rotating_41.msh")
    assert gmsh.format == "Gmsh" and gmsh.cell_zones == 2 and gmsh.note == "MSH 4.1"
    (tmp_path / "x.msh").write_text("hello", "utf-8")
    assert summarize(tmp_path / "x.msh").format == ""


# --- the picker in the UI ----------------------------------------------------------------------

def test_the_service_lists_a_folder_and_copies_into_the_project(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from machines.service import MachineService

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(disk.SETTINGS_ENV, str(tmp_path / "settings.json"))
    folder = tmp_path / "meshes"
    folder.mkdir()
    (folder / "rotating_41.msh").write_bytes((M1 / "rotating_41.msh").read_bytes())
    service = MachineService(tmp_path / "project")

    listed = service.list_mesh_folder(str(folder))
    assert [f.name for f in listed.files] == ["rotating_41.msh"]
    assert service.mesh_summary(folder / "rotating_41.msh").format == "Gmsh"
    service.remember_mesh_folder(folder)
    assert service.mesh_folder() == str(folder)
    target, started = service.start_mesh_copy(folder / "rotating_41.msh")
    assert started and target == service.root / "inputs" / "mesh" / "rotating_41.msh"
    disk.JOBS.wait(target, 10)
    copies: list[dict[str, Any]] = service.mesh_copies()
    assert copies[0]["state"] == "done" and target.is_file()
    service.forget_mesh_copy(target)
    assert service.mesh_copies() == [] and target.is_file()
    deadline = time.monotonic() + 1
    assert time.monotonic() < deadline
