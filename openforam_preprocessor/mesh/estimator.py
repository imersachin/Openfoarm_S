"""Heuristic resource preflight. Estimates are risk classifications, not predictions."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import psutil

from core.config.models import ProjectConfig
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from mesh.generator import OpenFOAMMeshCaseGenerator

DISCLAIMER = (
    "Heuristic estimate for risk classification only. Actual cell count, memory, "
    "disk use and runtime depend on geometry detail, snapping and the OpenFOAM version."
)


class ResourceStatus(StrEnum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    HIGH_RESOURCE_RISK = "HIGH RESOURCE RISK"
    BLOCKED = "BLOCKED"


_ORDER = list(ResourceStatus)
_SEVERITY = {
    ResourceStatus.WARNING: IssueSeverity.WARNING,
    ResourceStatus.HIGH_RESOURCE_RISK: IssueSeverity.ERROR,
    ResourceStatus.BLOCKED: IssueSeverity.BLOCKING,
}


def worst(*statuses: ResourceStatus) -> ResourceStatus:
    return max(statuses, key=_ORDER.index, default=ResourceStatus.SAFE)


@dataclass(frozen=True)
class EstimationModel:
    """Heuristic coefficients. Configurable; defaults are deliberately rough."""

    surface_band_cells: float = 4.0  # refined cells across the band around the surface
    feature_area_fraction: float = 0.05  # share of the surface treated as feature region
    ram_bytes_per_cell: float = 2000.0  # snappyHexMesh peak, ~2 GB per million cells
    disk_bytes_per_cell: float = 1500.0  # ASCII polyMesh, logs and intermediates


@dataclass(frozen=True)
class ResourceThresholds:
    """Fractions of *available* RAM/disk at which risk escalates; above 1.0 is BLOCKED."""

    warning_fraction: float = 0.5
    high_risk_fraction: float = 0.8
    serial_cells_warning: int = 5_000_000  # snappyHexMesh runs serially here


@dataclass(frozen=True)
class SystemResources:
    available_ram_bytes: int | None
    available_disk_bytes: int | None
    cpu_count: int | None

    @classmethod
    def detect(cls, path: Path) -> SystemResources:
        """Measure this machine now; unknown values are None, never guessed."""
        try:
            ram: int | None = int(psutil.virtual_memory().available)
        except Exception:  # pragma: no cover - platform-specific failure
            ram = None
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            disk: int | None = shutil.disk_usage(probe).free
        except OSError:
            disk = None
        return cls(available_ram_bytes=ram, available_disk_bytes=disk, cpu_count=os.cpu_count())


@dataclass(frozen=True)
class CellEstimate:
    background: int
    surface: int
    feature: int
    layers: int

    @property
    def total(self) -> int:
        return self.background + self.surface + self.feature + self.layers


@dataclass(frozen=True)
class ResourceAssessment:
    status: ResourceStatus
    cells: CellEstimate
    ram_bytes: int
    disk_bytes: int
    system: SystemResources
    issues: tuple[Issue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "disclaimer": DISCLAIMER,
            "estimated_cells": {**asdict(self.cells), "total": self.cells.total},
            "estimated_ram_bytes": self.ram_bytes,
            "estimated_disk_bytes": self.disk_bytes,
            "system": asdict(self.system),
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _gb(value: float) -> str:
    return f"{value / 1e9:.1f} GB"


class ResourceEstimator:
    def __init__(
        self,
        model: EstimationModel | None = None,
        thresholds: ResourceThresholds | None = None,
    ) -> None:
        self.model = model or EstimationModel()
        self.thresholds = thresholds or ResourceThresholds()

    def estimate_cells(self, config: ProjectConfig, surface_area_m2: float) -> CellEstimate:
        mesh = config.mesh
        nx, ny, nz = OpenFOAMMeshCaseGenerator.background_cells(config).effective
        background = nx * ny * nz
        lengths = mesh.background.domain.lengths
        base_size = (lengths.x * lengths.y * lengths.z / background) ** (1.0 / 3.0)

        def band_cells(area: float, level: int) -> int:
            size = base_size / 2**level
            return int(self.model.surface_band_cells * area / size**2)

        surface = band_cells(surface_area_m2, mesh.surface.maximum_level)
        feature = 0
        if mesh.surface.extract_features and (
            mesh.surface.feature_refinement_level > mesh.surface.maximum_level
        ):
            feature = band_cells(
                surface_area_m2 * self.model.feature_area_fraction,
                mesh.surface.feature_refinement_level,
            )
        layers = 0
        if mesh.layers.enabled:
            face_size = base_size / 2**mesh.surface.maximum_level
            layers = int(mesh.layers.number_of_layers * surface_area_m2 / face_size**2)
        return CellEstimate(background=background, surface=surface, feature=feature,
                            layers=layers)

    def assess(
        self, config: ProjectConfig, surface_area_m2: float, system: SystemResources
    ) -> ResourceAssessment:
        cells = self.estimate_cells(config, surface_area_m2)
        ram = int(cells.total * self.model.ram_bytes_per_cell)
        disk = int(cells.total * self.model.disk_bytes_per_cell)
        issues: list[Issue] = []
        statuses = [ResourceStatus.SAFE]

        def add(status: ResourceStatus, code: str, message: str, action: str, **details: Any):
            statuses.append(status)
            issues.append(Issue(
                category=IssueCategory.RESOURCE_RISK,
                severity=_SEVERITY[status],
                stage=IssueStage.RESOURCE_PREFLIGHT,
                code=code,
                message=f"{message} ({status.value}; heuristic estimate).",
                explanation=DISCLAIMER,
                suggested_action=action,
                details={"estimated_cells": cells.total, **details},
            ))

        reduce = ("Reduce surface/feature refinement levels, boundary layers, or "
                  "background resolution (larger base_cell_size).")
        for resource, needed, available, code in (
            ("RAM", ram, system.available_ram_bytes, "RAM"),
            ("disk", disk, system.available_disk_bytes, "DISK"),
        ):
            if available is None or available <= 0:
                add(ResourceStatus.WARNING, f"{code}_UNKNOWN",
                    f"Available {resource} could not be determined", "Verify resources manually.")
                continue
            fraction = needed / available
            status = (
                ResourceStatus.BLOCKED if fraction > 1.0
                else ResourceStatus.HIGH_RESOURCE_RISK
                if fraction > self.thresholds.high_risk_fraction
                else ResourceStatus.WARNING if fraction > self.thresholds.warning_fraction
                else ResourceStatus.SAFE
            )
            if status is not ResourceStatus.SAFE:
                add(status, f"{code}_RISK",
                    f"Estimated {resource} use ~{_gb(needed)} is {fraction:.0%} of the "
                    f"{_gb(available)} available", reduce,
                    estimated_bytes=needed, available_bytes=available, fraction=fraction)

        if cells.total > config.mesh.max_global_cells:
            add(ResourceStatus.WARNING, "MAX_GLOBAL_CELLS_EXCEEDED",
                f"Estimated ~{cells.total:,} cells exceed max_global_cells "
                f"({config.mesh.max_global_cells:,}); snappyHexMesh will stop refining early",
                "Expect less refinement than configured, or raise max_global_cells.")

        if cells.total > self.thresholds.serial_cells_warning:
            add(ResourceStatus.WARNING, "LONG_SERIAL_RUN",
                f"Estimated ~{cells.total:,} cells; meshing runs serially and may take a "
                "long time", "Consider coarser settings for a first iteration.",
                cpu_count=system.cpu_count)

        return ResourceAssessment(
            status=worst(*statuses),
            cells=cells,
            ram_bytes=ram,
            disk_bytes=disk,
            system=system,
            issues=tuple(issues),
        )
