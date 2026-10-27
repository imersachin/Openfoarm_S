"""Mesh files on disk (owner, M2a): where the app may look, Windows paths in WSL,
folder listings, and copying a file into WSL with its hash checked.

The app runs in WSL. Its roots are the WSL home and each Windows drive WSL
mounts (/mnt/c, /mnt/e, ...: the drvfs/9p mounts in /proc/mounts). A path is
used only when it resolves (symlinks followed) inside a root, so a link out
of a root is refused. A Windows path (E:\\Meshes\\Francis) or a WSL share path
(\\\\wsl$\\Ubuntu-24.04\\root\\meshes, \\\\wsl.localhost\\...) is converted.

Reading in place on a Windows drive is about 6.5 times slower than in WSL
(E0: the 637 MB file's inspection 14.4 s against 2.2 s), so a file on one is
copied into the project first: 8 MB chunks, sha256 while copying, a .part
file renamed only after its size and hash are read back and match (E0
evidence 4: 586 MB cold in 2.25 s, re-hashed in 0.26 s), after a free-space
check. A copy reports its progress to a status file, so a refreshed page sees
the same copy, and stops when asked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

CHUNK = 8 << 20
FREE_MARGIN = 1.05  # the copy needs its size and 5 % on the target's disk
MESH_SUFFIXES = (".msh",)
COPY_STATUS = ".copy.json"  # beside the copy: <name>.copy.json
_DRIVE_MOUNT = re.compile(r"^(\S+)\s+(/mnt/[a-z])\s+(9p|drvfs)\s+", re.MULTILINE)
_WINDOWS_DRIVE = re.compile(r"^([A-Za-z]):(?:[\\/]|$)")
_WSL_SHARE = re.compile(r"^[\\/]{2}wsl(?:\$|\.localhost)[\\/]([^\\/]+)(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Root:
    label: str  # "C:", "E:", "WSL home"
    path: Path


def roots(mounts: str | None = None, home: Path | None = None) -> tuple[Root, ...]:
    """The WSL home, then each Windows drive mounted under /mnt."""
    if mounts is None:
        try:
            mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
        except OSError:
            mounts = ""
    found = [Root("WSL home", (home or Path.home()).resolve())]
    for _, mount, _ in sorted(set(_DRIVE_MOUNT.findall(mounts)), key=lambda m: m[1]):
        found.append(Root(f"{mount[-1].upper()}:", Path(mount)))
    return tuple(found)


@dataclass(frozen=True)
class Resolved:
    path: Path | None  # inside a root; None when refused
    message: str = ""  # why it was refused, or what it was converted from


def to_wsl(text: str, distro: str | None = None) -> Resolved:
    """A Windows, WSL-share or Linux path as a path in this WSL distribution."""
    raw = text.strip().strip('"').strip("'")
    if not raw:
        return Resolved(None, "No path was given.")
    distro = distro if distro is not None else os.environ.get("WSL_DISTRO_NAME", "")
    share = _WSL_SHARE.match(raw)
    if share:
        name, rest = share[1], share[2]
        if distro and name.lower() != distro.lower():
            return Resolved(None, f"{raw} is in the WSL distribution {name}; the app runs in "
                                  f"{distro}. Copy the file to this distribution or a Windows "
                                  "drive.")
        parts = [p for p in re.split(r"[\\/]+", rest) if p]
        return Resolved(Path("/", *parts), f"converted from {raw}")
    drive = _WINDOWS_DRIVE.match(raw)
    if drive:
        pieces = PureWindowsPath(raw).parts[1:]
        return Resolved(Path("/mnt", drive[1].lower(), *pieces), f"converted from {raw}")
    if raw.startswith("~"):
        return Resolved(Path(raw).expanduser())
    if raw.startswith("/"):
        return Resolved(Path(raw))
    return Resolved(None, f"{raw} is not a full path: give it from a drive letter (E:\\...), "
                          "a \\\\wsl$ share or /.")


def to_windows(path: Path, distro: str | None) -> str | None:
    """A path in this WSL distribution as Windows sees it: a drive path for
    /mnt/<drive>/..., else the \\wsl.localhost share (M2b-3: ParaView on
    Windows opens the case there; E0: 364 MB/s). None without a distribution
    name for a path inside WSL."""
    parts = Path(path).parts
    if len(parts) > 2 and parts[1] == "mnt" and re.fullmatch(r"[a-z]", parts[2]):
        return "\\".join([f"{parts[2].upper()}:", *parts[3:]]) if len(parts) > 3 else (
            f"{parts[2].upper()}:\\")
    if not distro:
        return None
    return "\\\\wsl.localhost\\" + "\\".join([distro, *parts[1:]])


def inside(path: Path, places: Sequence[Root]) -> Root | None:
    """The root the path resolves into (symlinks followed), if any."""
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    for root in places:
        base = root.path.resolve()
        if real == base or real.is_relative_to(base):
            return root
    return None


@dataclass(frozen=True)
class Entry:
    name: str
    path: Path
    folder: bool
    size: int = 0
    modified: float = 0.0  # seconds since the epoch


@dataclass(frozen=True)
class Listing:
    path: Path | None
    root: Root | None
    folders: tuple[Entry, ...] = ()
    files: tuple[Entry, ...] = ()  # .msh files
    message: str = ""


def listing(text_or_path: str | Path, places: Sequence[Root] | None = None) -> Listing:
    """A folder's sub-folders and .msh files, when it resolves inside a root.
    Entries that are links out of the roots are left out."""
    places = tuple(places if places is not None else roots())
    resolved = (Resolved(text_or_path) if isinstance(text_or_path, Path)
                else to_wsl(text_or_path))
    if resolved.path is None:
        return Listing(None, None, message=resolved.message)
    path = resolved.path
    root = inside(path, places)
    if root is None:
        return Listing(None, None, message=f"{path} is not a folder inside "
                       + ", ".join(f"{r.label} ({r.path})" for r in places) + ".")
    if not path.is_dir():
        return Listing(None, root, message=f"{path} is not a folder.")
    folders, files = [], []
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError as error:
        return Listing(path, root, message=f"{path} cannot be read: {error.strerror}.")
    for child in children:
        if child.name.startswith(".") or inside(child, places) is None:
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        if child.is_dir():
            folders.append(Entry(child.name, child, True, 0, stat.st_mtime))
        elif child.suffix.lower() in MESH_SUFFIXES:
            files.append(Entry(child.name, child, False, stat.st_size, stat.st_mtime))
    return Listing(path, root, tuple(folders), tuple(files), resolved.message)


def on_windows_drive(path: Path) -> bool:
    """Whether the file is on a Windows drive (read in place slowly: copy it)."""
    real = path.resolve()
    return len(real.parts) > 2 and real.parts[1] == "mnt" and len(real.parts[2]) == 1


# --- the copy -------------------------------------------------------------------------------

@dataclass
class CopyStatus:
    source: str
    target: str
    size: int
    state: str = "copying"  # copying, done, failed, cancelled
    copied: int = 0
    started: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    sha256: str = ""
    message: str = ""
    pid: int = field(default_factory=os.getpid)

    @property
    def rate(self) -> float:
        """Bytes a second so far."""
        spent = max(self.updated - self.started, 1e-6)
        return self.copied / spent

    @property
    def remaining_s(self) -> float | None:
        rate = self.rate
        return (self.size - self.copied) / rate if rate > 0 and self.state == "copying" else None


def status_path(target: Path) -> Path:
    return target.with_name(target.name + COPY_STATUS)


def write_status(status: CopyStatus) -> None:
    path = status_path(Path(status.target))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(asdict(status), indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_status(target: Path) -> CopyStatus | None:
    try:
        data = json.loads(status_path(target).read_text("utf-8"))
        return CopyStatus(**data)
    except (OSError, ValueError, TypeError):
        return None


def _hash_file(path: Path, cancelled: Callable[[], bool]) -> str | None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            if cancelled():
                return None
            digest.update(block)
    return digest.hexdigest()


def copy_file(source: Path, target: Path, cancel: threading.Event | None = None,
              every: float = 0.5) -> CopyStatus:
    """Copy source to target through target.part, hashing while copying; keep
    it only when the copy read back has the same size and sha256. The status
    file beside the target follows (at most every `every` seconds)."""
    size = source.stat().st_size
    status = CopyStatus(str(source), str(target), size)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    def finish(state: str, message: str) -> CopyStatus:
        part.unlink(missing_ok=True)
        status.state, status.message, status.updated = state, message, time.time()
        write_status(status)
        return status

    free = shutil.disk_usage(target.parent).free
    if free < size * FREE_MARGIN:
        return finish("failed", f"Not enough free space for {source.name}: {size / 1e9:.2f} GB "
                                f"needed with a 5 % margin, {free / 1e9:.2f} GB free.")
    write_status(status)
    digest = hashlib.sha256()
    last = 0.0
    try:
        with source.open("rb") as reading, part.open("wb") as writing:
            while block := reading.read(CHUNK):
                if cancelled():
                    return finish("cancelled", "The copy was cancelled; nothing was kept.")
                digest.update(block)
                writing.write(block)
                status.copied += len(block)
                if time.time() - last >= every:
                    status.updated = last = time.time()
                    write_status(status)
            writing.flush()
            os.fsync(writing.fileno())
    except OSError as error:
        return finish("failed", f"The copy of {source.name} failed: {error.strerror or error}.")
    expected = digest.hexdigest()
    status.message = "checking the copy"
    write_status(status)
    copied = _hash_file(part, cancelled)
    if copied is None:
        return finish("cancelled", "The copy was cancelled; nothing was kept.")
    if part.stat().st_size != size or copied != expected:
        return finish("failed", f"The copy of {source.name} does not match the original "
                                "(size or sha256); nothing was kept. Try again.")
    part.replace(target)
    status.sha256, status.copied = expected, size
    status.state, status.message, status.updated = "done", "copied and checked", time.time()
    write_status(status)
    return status


# --- copies in the background (one per target) --------------------------------------------

@dataclass
class _Job:
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    result: CopyStatus | None = None

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class CopyJobs:
    """Copies running on threads of this process, by target file. A page shown
    again finds its copy here (and its status file says how far it got)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[Path, _Job] = {}

    def start(self, source: Path, target: Path) -> bool:
        """False when a copy to this target is already running."""
        key = target.resolve()
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.alive:
                return False
            entry = _Job()

            def work() -> None:
                entry.result = copy_file(source, target, entry.cancel)

            entry.thread = threading.Thread(target=work, daemon=True, name=f"copy-{target.name}")
            self._jobs[key] = entry
            entry.thread.start()
        return True

    def _job(self, target: Path) -> _Job | None:
        with self._lock:
            return self._jobs.get(target.resolve())

    def running(self, target: Path) -> bool:
        job = self._job(target)
        return job is not None and job.alive

    def cancel(self, target: Path) -> bool:
        job = self._job(target)
        if job is None or not job.alive:
            return False
        job.cancel.set()
        return True

    def wait(self, target: Path, timeout: float | None = None) -> CopyStatus | None:
        job = self._job(target)
        if job is None or job.thread is None:
            return None
        job.thread.join(timeout)
        return job.result


JOBS = CopyJobs()


def status_view(target: Path, jobs: CopyJobs = JOBS) -> dict[str, Any] | None:
    """What a page shows of a copy to `target`: its status file, and whether
    it is still running in this process (a copy left "copying" by a stopped
    app is reported as interrupted)."""
    status = read_status(target)
    if status is None:
        return None
    found = asdict(status)
    found["rate"], found["remaining_s"] = status.rate, status.remaining_s
    if status.state == "copying" and not jobs.running(target):
        found["state"] = "interrupted"
        found["message"] = "The copy stopped when the app stopped; start it again."
    return found


# --- the app's own settings (not a project's) -----------------------------------------------

SETTINGS_ENV = "MACHINES_SETTINGS"  # a settings file other than ~/.machines/settings.json


def settings_path(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    return Path(env.get(SETTINGS_ENV) or Path.home() / ".machines" / "settings.json")


def read_settings(path: Path | None = None) -> dict[str, Any]:
    try:
        found = json.loads((path or settings_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def write_setting(key: str, value: Any, path: Path | None = None) -> None:
    """Set one app setting (the mesh folder listed when Imported mesh opens)."""
    path = path or settings_path()
    found = read_settings(path)
    found[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
