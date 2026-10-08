from __future__ import annotations

import re
import shutil
from collections.abc import Iterable, Mapping
from typing import Any

from core.config.models import OpenFOAMProfile
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage

# Release naming: openfoam.com uses vYYMM (e.g. v2312); openfoam.org uses
# plain numbers (e.g. 11) or "dev". Used only to warn about a likely mismatch.
_VERSION_PATTERNS = {
    OpenFOAMProfile.OPENCFD: re.compile(r"^v\d{4}"),
    OpenFOAMProfile.FOUNDATION: re.compile(r"^(\d+(\.\d+)*|dev)$"),
}


def validate_environment(
    profile: OpenFOAMProfile, executables: Iterable[str], env: Mapping[str, str],
    *, verified_versions: Iterable[str] | None = None,
) -> tuple[Issue, ...]:
    """Check the OpenFOAM environment before any command runs.

    verified_versions: when given, a version of the right profile that is not
    in it gets a WARNING (the workflow's commands were proven on those only).
    """
    issues: list[Issue] = []
    identity = tool_identity(profile, executables, env)
    missing = sorted(name for name, path in identity["executables"].items() if path is None)
    if missing:
        issues.append(Issue(
            category=IssueCategory.OPENFOAM_ENVIRONMENT,
            severity=IssueSeverity.BLOCKING,
            stage=IssueStage.ENVIRONMENT_CHECK,
            code="OPENFOAM_EXECUTABLES_NOT_FOUND",
            message=f"OpenFOAM executable(s) not found on PATH: {', '.join(missing)}.",
            explanation="The OpenFOAM environment is not loaded in this process, or the "
            "installation is incomplete.",
            suggested_action="Source the OpenFOAM bashrc (or start the app from an OpenFOAM "
            "shell) and retry.",
            details={"missing": missing},
        ))

    version = identity["WM_PROJECT_VERSION"]
    if version is None:
        issues.append(Issue(
            category=IssueCategory.OPENFOAM_ENVIRONMENT,
            severity=IssueSeverity.INFO,
            stage=IssueStage.ENVIRONMENT_CHECK,
            code="OPENFOAM_VERSION_UNKNOWN",
            message="WM_PROJECT_VERSION is not set; the OpenFOAM version cannot be verified "
            "against the selected profile.",
            suggested_action="Run from a sourced OpenFOAM environment to record the version.",
        ))
    elif not _VERSION_PATTERNS[profile].match(version):
        issues.append(Issue(
            category=IssueCategory.OPENFOAM_ENVIRONMENT,
            severity=IssueSeverity.WARNING,
            stage=IssueStage.ENVIRONMENT_CHECK,
            code="OPENFOAM_PROFILE_MISMATCH",
            message=f"OpenFOAM version '{version}' does not look like a "
            f"'{profile.value}' release.",
            explanation="Dictionaries and commands are generated for the selected profile.",
            suggested_action="Select the profile matching the installed OpenFOAM, or load "
            "the matching installation.",
            details={"WM_PROJECT_VERSION": version, "profile": profile.value},
        ))
    elif verified_versions is not None and version not in (verified := tuple(verified_versions)):
        issues.append(Issue(
            category=IssueCategory.OPENFOAM_ENVIRONMENT,
            severity=IssueSeverity.WARNING,
            stage=IssueStage.ENVIRONMENT_CHECK,
            code="OPENFOAM_VERSION_UNVERIFIED",
            message=f"OpenFOAM {version} has not been verified for this workflow "
            f"(verified: {', '.join(verified)}).",
            explanation="Command options, outputs and silent-failure behaviour were "
            "established on the verified versions only.",
            suggested_action=f"Use OpenFOAM {verified[0]} if anything fails or looks wrong.",
            details={"WM_PROJECT_VERSION": version, "verified": list(verified)},
        ))
    return tuple(issues)


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
