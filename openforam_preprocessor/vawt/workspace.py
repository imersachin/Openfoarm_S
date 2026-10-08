"""Where VAWT projects live, and whether that place suits OpenFOAM.

The app runs inside WSL, where OpenFOAM is sourced. Projects belong in the WSL
file system (default ~/vawt_projects, or $VAWT_PROJECTS_DIR). A project on a
Windows drive mounted into WSL (/mnt/<drive>, file system 9p/drvfs) works but
is slow for OpenFOAM's many small files, so it gets a WARNING
(PROJECT_ON_WINDOWS_DRIVE). Windows reaches the WSL folder at
\\\\wsl.localhost\\<distro>\\...
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from vawt.project_store import CONFIG_PATH

PROJECTS_DIR_ENV = "VAWT_PROJECTS_DIR"
DEFAULT_PROJECTS_DIR = "~/vawt_projects"
MOUNTS_PATH = Path("/proc/mounts")
OSRELEASE_PATH = Path("/proc/sys/kernel/osrelease")
_DRIVE_PATH = re.compile(r"^/mnt/([A-Za-z])(/|$)")  # used when no mount table is readable
_NAME = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Mount:
    device: str
    point: str
    fstype: str
    options: str

    @property
    def is_windows_drive(self) -> bool:
        # WSL2 mounts drives as 9p with aname=drvfs (V4: C:\ and E:\ here);
        # WSL1 uses the drvfs file system type.
        return self.fstype == "drvfs" or "drvfs" in self.options


def projects_root(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get(PROJECTS_DIR_ENV) or DEFAULT_PROJECTS_DIR).expanduser().resolve()


def is_wsl(env: Mapping[str, str] | None = None, osrelease: Path = OSRELEASE_PATH) -> bool:
    env = os.environ if env is None else env
    if env.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in osrelease.read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _unescape(field: str) -> str:
    # /proc/mounts escapes space, tab, newline and backslash as octal (\040 ...).
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def read_mounts(text: str | None = None) -> list[Mount]:
    """The mount table (text of /proc/mounts; read when None). Empty if unreadable."""
    if text is None:
        try:
            text = MOUNTS_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            mounts.append(Mount(_unescape(parts[0]), _unescape(parts[1]), parts[2], parts[3]))
    return mounts


def windows_drive(path: Path, mounts: Sequence[Mount] | None = None) -> str | None:
    """The Windows-drive mount point the path lies on, or None."""
    target = PurePosixPath(Path(path).expanduser().resolve().as_posix())
    mounts = read_mounts() if mounts is None else mounts
    containing = [m for m in mounts
                  if target == PurePosixPath(m.point) or PurePosixPath(m.point) in target.parents]
    if containing:
        nearest = max(containing, key=lambda m: len(PurePosixPath(m.point).parts))
        return nearest.point if nearest.is_windows_drive else None
    match = _DRIVE_PATH.match(target.as_posix())
    return f"/mnt/{match.group(1)}" if match else None


def location_issues(path: Path, mounts: Sequence[Mount] | None = None,
                    env: Mapping[str, str] | None = None) -> tuple[Issue, ...]:
    drive = windows_drive(path, mounts)
    if drive is None:
        return ()
    env = os.environ if env is None else env
    distro = env.get("WSL_DISTRO_NAME") or "<distro>"
    suggested = projects_root(env)
    return (Issue(
        category=IssueCategory.EXECUTION,
        severity=IssueSeverity.WARNING,
        stage=IssueStage.CONFIGURATION,
        code="PROJECT_ON_WINDOWS_DRIVE",
        message=f"The project folder {path} is on a Windows drive ({drive}).",
        explanation="OpenFOAM reads and writes many small files. Across the WSL/Windows "
        "file-system boundary this is slower (a small AMI mesh took about 1.7 times as "
        "long on /mnt/c as in the WSL file system), and Windows tools (indexing, "
        "antivirus) can hold files open during a run.",
        suggested_action=f"Keep projects in the WSL file system, e.g. {suggested}. "
        f"Windows can open it at \\\\wsl.localhost\\{distro}"
        f"{suggested.as_posix().replace('/', chr(92))}.",
        artifact_reference=str(path),
        details={"drive": drive, "suggested_root": str(suggested)},
    ),)


def list_projects(root: Path) -> list[Path]:
    """Project folders under root with a saved VAWT configuration, by name."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / CONFIG_PATH).is_file())


def folder_name(name: str) -> str:
    """A folder name for a project name (letters, digits, '_' and '-')."""
    folder = _NAME.sub("_", name.strip()).strip("_")
    if not folder:
        raise ValueError("A project name needs at least one letter or digit.")
    return folder


def create_project(name: str, root: Path) -> Path:
    """A new, empty project folder under root. ValueError if it already exists."""
    path = root / folder_name(name)
    if path.exists():
        raise ValueError(f"A folder named {path.name} already exists in {root}.")
    path.mkdir(parents=True)
    return path
