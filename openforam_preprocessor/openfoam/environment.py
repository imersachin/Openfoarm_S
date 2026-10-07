from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from typing import Any

from core.config.models import OpenFOAMProfile


def tool_identity(
    profile: OpenFOAMProfile,
    executables: Iterable[str],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """What identifies the OpenFOAM tools an operation runs with.

    Recorded with cached artifacts: a different profile, OpenFOAM
    project/version, or executable location invalidates reuse. Values that
    cannot be determined are recorded as None and compared as such.
    """
    search_path = env.get("PATH")
    return {
        "profile": profile.value,
        "WM_PROJECT": env.get("WM_PROJECT"),
        "WM_PROJECT_VERSION": env.get("WM_PROJECT_VERSION"),
        "executables": {
            name: shutil.which(name, path=search_path) for name in sorted(set(executables))
        },
    }
