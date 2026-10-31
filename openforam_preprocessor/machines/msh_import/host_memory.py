"""What WSL could have with .wslconfig raised (C1; docs/c1_startup_notes.md 2).

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

_SCRIPT = ("$o=Get-CimInstance Win32_OperatingSystem; "
           "$v=(Get-Process -Name vmmem,vmmemWSL -ErrorAction SilentlyContinue | "
           "Measure-Object WorkingSet64 -Sum).Sum; "
           "\"$($o.TotalVisibleMemorySize) $($o.FreePhysicalMemory) $v\"")
COMMAND = ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT)
CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class HostMemory:
    total_bytes: int
    windows_used_bytes: int  # used, less WSL's VM

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
    return HostMemory(total, used)


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
    except (OSError, subprocess.SubprocessError):
        found = None
    _cache["host"] = (clock(), found)
    return found
