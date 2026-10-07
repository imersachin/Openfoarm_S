from pathlib import Path

from core.config.models import OpenFOAMProfile
from core.issues import IssueSeverity
from openfoam.environment import validate_environment
from tests.fakes import openfoam_env

TOOLS = ("blockMesh", "snappyHexMesh", "checkMesh")


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_complete_matching_environment_has_no_issues(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "v2312")

    assert validate_environment(OpenFOAMProfile.OPENCFD, TOOLS, env) == ()


def test_all_missing_executables_are_reported_together(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "v2312", tools=("blockMesh",))

    issues = validate_environment(OpenFOAMProfile.OPENCFD, TOOLS, env)

    issue = next(i for i in issues if i.code == "OPENFOAM_EXECUTABLES_NOT_FOUND")
    assert issue.severity is IssueSeverity.BLOCKING
    assert issue.details["missing"] == ["checkMesh", "snappyHexMesh"]


def test_unknown_version_is_informational(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path)
    del env["WM_PROJECT_VERSION"]

    issues = validate_environment(OpenFOAMProfile.OPENCFD, TOOLS, env)

    assert codes(issues) == {"OPENFOAM_VERSION_UNKNOWN"}
    assert issues[0].severity is IssueSeverity.INFO


def test_foundation_version_with_esi_profile_warns(tmp_path: Path) -> None:
    issues = validate_environment(OpenFOAMProfile.OPENCFD, TOOLS, openfoam_env(tmp_path, "11"))

    assert codes(issues) == {"OPENFOAM_PROFILE_MISMATCH"}
    assert issues[0].severity is IssueSeverity.WARNING


def test_esi_version_with_foundation_profile_warns(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "v2406")

    assert codes(validate_environment(OpenFOAMProfile.FOUNDATION, TOOLS, env)) == {
        "OPENFOAM_PROFILE_MISMATCH"
    }


def test_foundation_dev_version_matches_foundation_profile(tmp_path: Path) -> None:
    env = openfoam_env(tmp_path, "dev")

    assert validate_environment(OpenFOAMProfile.FOUNDATION, TOOLS, env) == ()
