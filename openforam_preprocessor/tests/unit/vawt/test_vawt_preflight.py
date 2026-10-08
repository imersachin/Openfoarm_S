"""V3: VAWT resource preflight (heuristic; arithmetic only)."""

from __future__ import annotations

import builtins
import copy
from pathlib import Path
from typing import Any

import pytest

from mesh.estimator import ResourceStatus, SystemResources
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import VawtProjectConfig
from vawt.preflight import assess, estimate_cells

AREA = 2.0  # m^2, roughly the fixture rotor's surface


def config(tmp_path: Path, **edits: Any) -> VawtProjectConfig:
    draft = preset_draft(tmp_path, include_domain=edits.pop("include_domain", True))
    for dotted, value in edits.items():
        *parents, leaf = dotted.split("__")
        target = draft
        for key in parents:
            target = target.setdefault(key, {})
        target[leaf] = value
    return VawtProjectConfig.model_validate(draft)


def ram(gb: float) -> SystemResources:
    return SystemResources(available_ram_bytes=int(gb * 1e9), available_disk_bytes=10**13,
                           cpu_count=8)


@pytest.mark.parametrize("edit,part", [
    ({"refinement__blade_max_level": 3}, "blade"),
    ({"refinement__wake__level": 2}, "wake"),
    ({"refinement__interface_level": 2}, "interface"),
    ({"layers__enabled": True, "layers__count": 6}, "layers"),
    ({"rotating_zone__cell_size": 0.03}, "rotor_background"),
    ({"domain__cell_size": 0.09}, "outer_background"),
])
def test_more_refinement_costs_more(tmp_path: Path, edit: dict[str, Any], part: str) -> None:
    base = estimate_cells(config(tmp_path), AREA)
    finer = estimate_cells(config(tmp_path, **edit), AREA)

    assert getattr(finer, part) > getattr(base, part)
    assert finer.total > base.total


def test_outer_settings_do_not_change_the_rotor_estimate(tmp_path: Path) -> None:
    base = estimate_cells(config(tmp_path), AREA)
    wake = estimate_cells(config(tmp_path, refinement__wake__level=3), AREA)

    assert wake.rotor == base.rotor and wake.outer > base.outer


def test_rotor_only_has_no_outer_mesh(tmp_path: Path) -> None:
    estimate = estimate_cells(config(tmp_path, include_domain=False), AREA)

    assert estimate.outer == 0 and estimate.rotor > 0


def test_ram_follows_the_larger_mesh_in_ami_and_the_total_in_a_single_mesh(
    tmp_path: Path
) -> None:
    ami = config(tmp_path)
    single = config(tmp_path, rotating_zone__interface="CELL_ZONE")

    estimate, result = assess(ami, AREA, ram(64))
    assert result.ram_bytes == max(estimate.outer, estimate.rotor) * 2000
    assert result.disk_bytes == estimate.total * 1500

    estimate, result = assess(single, AREA, ram(64))
    assert result.ram_bytes == estimate.total * 2000


def test_all_four_statuses(tmp_path: Path) -> None:
    c = config(tmp_path)
    estimate, _ = assess(c, AREA, ram(64))
    peak_gb = max(estimate.outer, estimate.rotor) * 2000 / 1e9

    assert assess(c, AREA, ram(peak_gb * 4))[1].status is ResourceStatus.SAFE
    assert assess(c, AREA, ram(peak_gb / 0.6))[1].status is ResourceStatus.WARNING
    assert assess(c, AREA, ram(peak_gb / 0.9))[1].status is ResourceStatus.HIGH_RESOURCE_RISK
    assert assess(c, AREA, ram(peak_gb / 1.5))[1].status is ResourceStatus.BLOCKED


def test_unknown_ram_is_a_warning_not_a_guess(tmp_path: Path) -> None:
    unknown = SystemResources(available_ram_bytes=None, available_disk_bytes=10**13,
                              cpu_count=None)

    _, result = assess(config(tmp_path), AREA, unknown)

    assert result.status is ResourceStatus.WARNING
    assert "RAM_UNKNOWN" in {i.code for i in result.issues}


def test_estimate_reads_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = config(tmp_path)

    def no_open(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the estimate must not read files")

    monkeypatch.setattr(builtins, "open", no_open)
    monkeypatch.setattr(Path, "read_text", no_open)
    monkeypatch.setattr(Path, "read_bytes", no_open)

    estimate_cells(copy.deepcopy(c), AREA)
