"""The rotating-zone cell size actually used is reported before any run.

In a single mesh (CELL_ZONE with a domain) the zone cells can only be the
domain cell size / 2^n (spec 9.1). The service returns the size used and, when
it differs from the one entered, an INFO issue naming both values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.issues import IssueSeverity
from tests.fixtures.vawt.drafts import preset_draft
from vawt.runtime import RunRegistry
from vawt.service import SectionState, VawtService, _GeometryCache


def service(tmp_path: Path) -> VawtService:
    return VawtService(tmp_path / "project", registry=RunRegistry(),
                       geometry_cache=_GeometryCache())


def single_mesh(tmp_path: Path, zone_cell_size: float | None = None) -> dict[str, Any]:
    draft = preset_draft(tmp_path)
    draft["rotating_zone"]["interface"] = "CELL_ZONE"
    if zone_cell_size is not None:
        draft["rotating_zone"]["cell_size"] = zone_cell_size
    return draft


def test_single_mesh_reports_the_size_used_and_both_values(tmp_path: Path) -> None:
    draft = single_mesh(tmp_path)
    domain = draft["domain"]["cell_size"]
    entered = domain / 5.3  # not a power-of-two ratio: snaps to domain / 8

    outcome = service(tmp_path).validate(single_mesh(tmp_path, entered))

    assert outcome.zone_cell_size == pytest.approx(domain / 8)
    issue = next(i for i in outcome.issues if i.code == "ZONE_CELL_SIZE_ADJUSTED")
    assert issue.severity is IssueSeverity.INFO
    assert issue.details["requested"] == pytest.approx(entered)
    assert issue.details["effective"] == pytest.approx(domain / 8)
    assert f"{entered:.6g} m" in issue.message and f"{domain / 8:.6g} m" in issue.message


def test_the_issue_shows_in_the_rotating_zone_section(tmp_path: Path) -> None:
    draft = single_mesh(tmp_path)
    draft["rotating_zone"]["cell_size"] = draft["domain"]["cell_size"] / 5.3

    status = service(tmp_path).section_status(draft)["rotating_zone"]

    assert "ZONE_CELL_SIZE_ADJUSTED" in {i.code for i in status.issues}
    assert status.state is not SectionState.ERROR  # INFO only


def test_no_issue_when_the_size_entered_is_used(tmp_path: Path) -> None:
    domain = single_mesh(tmp_path)["domain"]["cell_size"]

    exact = service(tmp_path).validate(single_mesh(tmp_path, domain / 4))
    ami = service(tmp_path).validate(preset_draft(tmp_path))

    assert exact.zone_cell_size == pytest.approx(domain / 4)
    assert "ZONE_CELL_SIZE_ADJUSTED" not in {i.code for i in exact.issues}
    assert ami.zone_cell_size == ami.config.rotating_zone.cell_size  # type: ignore[union-attr]
    assert "ZONE_CELL_SIZE_ADJUSTED" not in {i.code for i in ami.issues}
