"""Windows' memory, for the memory warnings (C1; docs/c1_startup_notes.md 2).

What WSL could have with .wslconfig raised, and (owner, 2026-10-31: a solve
was stopped when ANSYS held the memory) Windows' free memory before any
heavy run: WSL's VM grows into it, so a run that needs more than Windows has
free, plus a margin, gets a WARNING to close other heavy programs.

The Windows host's RAM less what Windows itself uses now (its used memory
without WSL's VM, vmmem/vmmemWSL), read through WSL's Windows interop
(powershell.exe, about 2 s). A heuristic for the memory warning, never a
guarantee: Windows' use changes. None when it cannot be read (not WSL,
interop off, the VM's process not visible); nothing is guessed.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage

_SCRIPT = ("$o=Get-CimInstance Win32_OperatingSystem; "
           "$v=(Get-Process -Name vmmem,vmmemWSL -ErrorAction SilentlyContinue | "
           "Measure-Object WorkingSet64 -Sum).Sum; "
           "\"$($o.TotalVisibleMemorySize) $($o.FreePhysicalMemory) $v\"")
COMMAND = ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT)
CACHE_SECONDS = 60.0
# Windows keeps at least this free besides the run (C1's run watchdog stops a
# solve below it): 6 GB.
WINDOWS_MARGIN_BYTES = 6_000_000_000


@dataclass(frozen=True)
class HostMemory:
    total_bytes: int
    windows_used_bytes: int  # used, less WSL's VM
    free_bytes: int = 0  # Windows' free (available) memory now

    @property
    def wsl_ceiling_bytes(self) -> int:
        """The most WSL could have at Windows' use now."""
        return max(0, self.total_bytes - self.windows_used_bytes)


def parse(text: str) -> HostMemory | None:
    """'<total KB> <free KB> <VM bytes>' as the script prints it."""
    parts = text.split()
    if len(parts) != 3:
        return None
    try:
        total, free, vm = int(parts[0]) * 1024, int(parts[1]) * 1024, int(parts[2])
    except ValueError:
        return None
    used = total - free - vm
    if total <= 0 or free < 0 or vm <= 0 or used < 0:
        return None
    return HostMemory(total, used, free)


def low_windows_memory(host: HostMemory | None, needed_bytes: float | None,
                       stage: IssueStage, what: str) -> Issue | None:
    """A WARNING when Windows has less free than the run needs plus the margin;
    None when it has enough, or when Windows cannot be read (nothing guessed)."""
    if host is None:
        return None
    needed = max(0.0, needed_bytes or 0.0)
    if host.free_bytes >= needed + WINDOWS_MARGIN_BYTES:
        return None
    return Issue(
        category=IssueCategory.RESOURCE_RISK, severity=IssueSeverity.WARNING, stage=stage,
        code="WINDOWS_MEMORY_LOW",
        message=f"Windows has {host.free_bytes / 1e9:.1f} GB free; {what} needs about "
                f"{needed / 1e9:.1f} GB, and Windows should keep about "
                f"{WINDOWS_MARGIN_BYTES / 1e9:.0f} GB besides.",
        suggested_action="Close other heavy programs (e.g. ANSYS) before this run.",
        explanation="WSL's memory comes out of Windows' free memory as the run grows; when "
                    "Windows runs short the run is stopped or killed (C1: a solve was "
                    "stopped this way).",
        details={"windows_free_bytes": host.free_bytes, "needed_bytes": needed,
                 "margin_bytes": WINDOWS_MARGIN_BYTES})


_cache: dict[str, tuple[float, HostMemory | None]] = {}


def host_memory(run: Callable[..., Any] = subprocess.run,
                clock: Callable[[], float] = time.monotonic) -> HostMemory | None:
    """The host's memory now (cached CACHE_SECONDS); None when unreadable."""
    cached = _cache.get("host")
    if cached is not None and clock() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        done = run(COMMAND, capture_output=True, text=True, timeout=15, check=False)
        found = parse(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):  # ValueError: undecodable
        found = None
    _cache["host"] = (clock(), found)
    return found
