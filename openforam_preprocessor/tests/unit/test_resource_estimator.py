from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.config.models import ProjectConfig
from core.issues import IssueCategory, IssueSeverity
from mesh.estimator import (
    EstimationModel,
    ResourceEstimator,
    ResourceStatus,
    ResourceThresholds,
    SystemResources,
)
from tests.helpers import build_config

GB = 10**9
AREA = 6.0  # unit cube surface, m^2


def changed(config: ProjectConfig, path: str, value: Any) -> ProjectConfig:
    data = config.model_dump(mode="json")
    *parents, leaf = path.split(".")
    target = data
    for key in parents:
        target = target[key]
    target[leaf] = value
    return ProjectConfig.model_validate(data)


def base() -> ProjectConfig:
    return build_config(Path("part.stl"))


def total(config: ProjectConfig, area: float = AREA) -> int:
    return ResourceEstimator().estimate_cells(config, area).total


def system(ram_gb: float = 64, disk_gb: float = 1000) -> SystemResources:
    return SystemResources(int(ram_gb * GB), int(disk_gb * GB), 8)


# --- relative cost behaviour (docs/testing_strategy.md section 8) ---------

def test_more_background_cells_cost_more() -> None:
    assert total(changed(base(), "mesh.background.base_cell_size", 0.25)) > total(base())


def test_more_surface_refinement_costs_more() -> None:
    assert total(changed(base(), "mesh.surface.maximum_level", 4)) > total(base())


def test_more_layers_cost_more() -> None:
    with_layers = changed(base(), "mesh.layers.enabled", True)
    more = changed(with_layers, "mesh.layers.number_of_layers", 8)

    assert total(with_layers) > total(base())
    assert total(more) > total(with_layers)


def test_larger_geometry_costs_more() -> None:
    assert total(base(), area=60.0) > total(base(), area=AREA)


def test_feature_refinement_counts_only_when_finer_than_surface() -> None:
    features = changed(base(), "mesh.surface.extract_features", True)
    finer = changed(features, "mesh.surface.feature_refinement_level", 5)

    assert ResourceEstimator().estimate_cells(features, AREA).feature == 0  # 3 <= max 3
    assert ResourceEstimator().estimate_cells(finer, AREA).feature > 0


def test_background_estimate_uses_effective_cells() -> None:
    cells = ResourceEstimator().estimate_cells(base(), AREA)
    assert cells.background == 8 * 8 * 8  # domain 4 m / 0.5 m


# --- status classification -------------------------------------------------

def assess(ram_gb: float, disk_gb: float = 1000, config: ProjectConfig | None = None,
           **model: Any):
    estimator = ResourceEstimator(EstimationModel(**model)) if model else ResourceEstimator()
    return estimator.assess(config or base(), AREA, system(ram_gb, disk_gb))


def ram_needed_gb() -> float:
    estimator = ResourceEstimator()
    return estimator.estimate_cells(base(), AREA).total * estimator.model.ram_bytes_per_cell / GB


@pytest.mark.parametrize(
    ("ram_factor", "expected"),
    [
        (10.0, ResourceStatus.SAFE),       # uses 10%
        (1.5, ResourceStatus.WARNING),     # uses ~67%
        (1.1, ResourceStatus.HIGH_RESOURCE_RISK),  # uses ~91%
        (0.5, ResourceStatus.BLOCKED),     # needs 2x available
    ],
)
def test_ram_statuses(ram_factor: float, expected: ResourceStatus) -> None:
    result = assess(ram_needed_gb() * ram_factor)

    assert result.status is expected
    if expected is not ResourceStatus.SAFE:
        issue = next(i for i in result.issues if i.code == "RAM_RISK")
        assert issue.category is IssueCategory.RESOURCE_RISK
        assert "heuristic" in issue.message


def test_status_severity_mapping() -> None:
    assert assess(ram_needed_gb() * 0.5).issues[0].severity is IssueSeverity.BLOCKING
    assert assess(ram_needed_gb() * 1.1).issues[0].severity is IssueSeverity.ERROR
    assert assess(ram_needed_gb() * 1.5).issues[0].severity is IssueSeverity.WARNING


def test_low_disk_is_classified() -> None:
    estimator = ResourceEstimator()
    disk_needed = estimator.estimate_cells(base(), AREA).total * 1500 / GB

    result = estimator.assess(base(), AREA, system(ram_gb=1000, disk_gb=disk_needed / 2))

    assert result.status is ResourceStatus.BLOCKED
    assert [i.code for i in result.issues] == ["DISK_RISK"]


def test_unknown_resources_are_a_warning_not_a_guess() -> None:
    result = ResourceEstimator().assess(base(), AREA, SystemResources(None, None, None))

    assert result.status is ResourceStatus.WARNING
    assert {i.code for i in result.issues} == {"RAM_UNKNOWN", "DISK_UNKNOWN"}


def test_high_refinement_exceeding_max_global_cells_warns() -> None:
    config = changed(base(), "mesh.surface.maximum_level", 8)

    result = ResourceEstimator().assess(config, AREA, system(ram_gb=10**6, disk_gb=10**7))

    assert "MAX_GLOBAL_CELLS_EXCEEDED" in {i.code for i in result.issues}
    assert "LONG_SERIAL_RUN" in {i.code for i in result.issues}
    assert result.status is ResourceStatus.WARNING


def test_thresholds_are_configurable() -> None:
    lenient = ResourceEstimator(thresholds=ResourceThresholds(warning_fraction=0.95,
                                                              high_risk_fraction=0.99))
    result = lenient.assess(base(), AREA, system(ram_gb=ram_needed_gb() * 1.5))

    assert result.status is ResourceStatus.SAFE


def test_report_includes_disclaimer_and_breakdown() -> None:
    data = assess(64).as_dict()

    assert data["status"] == "SAFE"
    assert "not" in data["disclaimer"] or "only" in data["disclaimer"]
    assert set(data["estimated_cells"]) == {"background", "surface", "feature", "layers", "total"}
    assert data["system"]["available_ram_bytes"] == 64 * GB


def test_detect_reads_this_machine(tmp_path: Path) -> None:
    detected = SystemResources.detect(tmp_path / "not" / "yet" / "created")

    assert detected.available_ram_bytes is None or detected.available_ram_bytes > 0
    assert detected.available_disk_bytes is None or detected.available_disk_bytes > 0
