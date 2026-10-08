"""V4: projects folder, Windows-drive detection, project files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import VawtProjectConfig
from vawt.project_store import (
    CONFIG_PATH,
    HISTORY_DIR,
    LAST_MESHED_PATH,
    VawtProjectStore,
    read_last_meshed,
    write_last_meshed,
)
from vawt.workspace import (
    DEFAULT_PROJECTS_DIR,
    create_project,
    folder_name,
    is_wsl,
    list_projects,
    location_issues,
    projects_root,
    read_mounts,
    windows_drive,
)

# /proc/mounts of the development machine (WSL2, Ubuntu 24.04), trimmed.
WSL_MOUNTS = read_mounts(
    "none /mnt/wsl tmpfs rw,relatime 0 0\n"
    "/dev/sdd / ext4 rw,relatime,discard,errors=remount-ro,data=ordered 0 0\n"
    "/dev/sdd /mnt/wslg/distro ext4 ro,relatime 0 0\n"
    "C:\\134 /mnt/c 9p rw,noatime,aname=drvfs;path=C:\\;uid=0;gid=0 0 0\n"
    "E:\\134 /mnt/e 9p rw,noatime,aname=drvfs;path=E:\\;uid=0;gid=0 0 0\n"
    "D:\\134 /mnt/my\\040drive drvfs rw,noatime 0 0\n"
)


def test_default_projects_folder_is_in_the_home_directory() -> None:
    assert projects_root({}) == Path(DEFAULT_PROJECTS_DIR).expanduser().resolve()
    assert DEFAULT_PROJECTS_DIR == "~/vawt_projects"


def test_projects_folder_can_be_overridden(tmp_path: Path) -> None:
    assert projects_root({"VAWT_PROJECTS_DIR": str(tmp_path)}) == tmp_path.resolve()


@pytest.mark.parametrize("path,drive", [
    ("/mnt/e/Udit/project", "/mnt/e"),
    ("/mnt/c", "/mnt/c"),
    ("/mnt/my drive/p", "/mnt/my drive"),  # escaped mount point, drvfs type
    ("/root/vawt_projects/p", None),
    ("/mnt/wslg/distro/x", None),  # ext4 under /mnt is not a Windows drive
    ("/mnt/wsl/x", None),
])
def test_windows_drive_from_the_mount_table(path: str, drive: str | None) -> None:
    assert windows_drive(Path(path), WSL_MOUNTS) == drive


@pytest.mark.parametrize("path,drive", [("/mnt/d/x", "/mnt/d"), ("/home/u/x", None),
                                        ("/mnt/data/x", None)])
def test_windows_drive_without_a_mount_table(path: str, drive: str | None) -> None:
    assert windows_drive(Path(path), []) == drive


def test_project_on_a_windows_drive_is_a_warning() -> None:
    env = {"WSL_DISTRO_NAME": "Ubuntu-24.04", "VAWT_PROJECTS_DIR": "/root/vawt_projects"}

    issues = location_issues(Path("/mnt/e/projects/rotor"), WSL_MOUNTS, env)

    assert [(i.code, i.severity.value) for i in issues] == [
        ("PROJECT_ON_WINDOWS_DRIVE", "WARNING")]
    assert "/root/vawt_projects" in issues[0].suggested_action
    assert "\\\\wsl.localhost\\Ubuntu-24.04\\root\\vawt_projects" in issues[0].suggested_action
    assert location_issues(Path("/root/vawt_projects/rotor"), WSL_MOUNTS, env) == ()


def test_wsl_detection(tmp_path: Path) -> None:
    release = tmp_path / "osrelease"
    release.write_text("6.18.40.1-microsoft-standard-WSL2\n", encoding="utf-8")
    native = tmp_path / "native"
    native.write_text("6.8.0-45-generic\n", encoding="utf-8")

    assert is_wsl({"WSL_DISTRO_NAME": "Ubuntu-24.04"}, native)
    assert is_wsl({}, release)
    assert not is_wsl({}, native)
    assert not is_wsl({}, tmp_path / "missing")


def test_create_and_list_projects(tmp_path: Path) -> None:
    created = create_project("Rotor A / test", tmp_path)
    assert created == tmp_path / "Rotor_A_test" and created.is_dir()
    assert list_projects(tmp_path) == []  # nothing saved yet
    (created / CONFIG_PATH).parent.mkdir(parents=True)
    (created / CONFIG_PATH).write_text("{}", encoding="utf-8")
    assert list_projects(tmp_path) == [created]
    with pytest.raises(ValueError):
        create_project("Rotor A / test", tmp_path)
    with pytest.raises(ValueError):
        folder_name("  //  ")


# --- project files ------------------------------------------------------------------

def test_saved_revisions_and_history(tmp_path: Path) -> None:
    config = VawtProjectConfig.model_validate(preset_draft(tmp_path))
    store = VawtProjectStore(tmp_path / "p")
    assert store.load() is None

    first = store.save(config)
    second = store.save(config.model_copy(update={"project_name": "Renamed"}))

    assert (first.revision, second.revision) == (1, 2)
    loaded = store.load()
    assert loaded is not None and loaded.raw["project_name"] == "Renamed"
    history = sorted((tmp_path / "p" / HISTORY_DIR).glob("*.json"))
    assert [h.name for h in history] == ["000001.json", "000002.json"]


def test_unreadable_project_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError):
        VawtProjectStore(tmp_path).load()


def test_last_meshed_record(tmp_path: Path) -> None:
    config = VawtProjectConfig.model_validate(preset_draft(tmp_path))
    assert read_last_meshed(tmp_path) is None

    write_last_meshed(tmp_path, "abc", config, "passed")

    last = read_last_meshed(tmp_path)
    assert last is not None and (last.run_id, last.mesh_status) == ("abc", "passed")
    assert last.raw == config.model_dump(mode="json")
    (tmp_path / LAST_MESHED_PATH).write_text(json.dumps({"run_id": 1}), encoding="utf-8")
    assert read_last_meshed(tmp_path) is None
