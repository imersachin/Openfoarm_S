"""Resource preflight for VAWT meshing (spec section 11).

Arithmetic on the configuration and the rotor surface area only: no file
access (spec 14.1 item 9). Heuristic risk classification with the engine's
statuses and thresholds; never a prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mesh.estimator import (
    CellEstimate,
    ResourceAssessment,
    ResourceEstimator,
    SystemResources,
)
from vawt.case_generator import (
    case_layout,
    domain_grid,
    rotor_grid,
    single_mesh_level_offset,
    zone_cell_size,
)
from vawt.config import VawtProjectConfig


@dataclass(frozen=True)
class VawtCellEstimate:
    """Heuristic cell counts per meshing step."""

    outer_background: int = 0
    wake: int = 0
    interface: int = 0
    rotor_background: int = 0  # rotor block, or zone refinement in a single mesh
    blade: int = 0
    feature: int = 0
    layers: int = 0

    @property
    def outer(self) -> int:
        return self.outer_background + self.wake + self.interface

    @property
    def rotor(self) -> int:
        return self.rotor_background + self.blade + self.feature + self.layers

    @property
    def total(self) -> int:
        return self.outer + self.rotor


def _cells(grid_cells: tuple[int, int, int]) -> int:
    return grid_cells[0] * grid_cells[1] * grid_cells[2]


def _refined(volume: float, cell: float, level: int) -> int:
    """Extra cells from refining a region of this volume by `level` levels."""
    return int(volume / cell**3 * (8**level - 1)) if level > 0 else 0


def estimate_cells(config: VawtProjectConfig, rotor_area_m2: float,
                   band_cells: float = 4.0, feature_area_fraction: float = 0.05
                   ) -> VawtCellEstimate:
    """Background meshes, wake region, interface band, blade band, features, layers."""
    layout = case_layout(config)
    zone, refinement = config.rotating_zone, config.refinement
    radius, height = zone.diameter / 2.0, zone.axis_max - zone.axis_min
    cylinder_area = 2 * math.pi * radius * height + 2 * math.pi * radius**2
    cylinder_volume = math.pi * radius**2 * height

    def band(area: float, size: float) -> int:
        return int(band_cells * area / size**2)

    outer_background = wake = interface = rotor_background = 0
    if config.domain is not None:
        domain_cell = config.domain.cell_size
        outer_background = _cells(domain_grid(config).cells)
        if refinement.wake is not None:
            lo, hi = refinement.wake.box.minimum, refinement.wake.box.maximum
            volume = (hi.x - lo.x) * (hi.y - lo.y) * (hi.z - lo.z)
            wake = _refined(volume, domain_cell, refinement.wake.level)
        if layout.single_mesh:
            rotor_background = _refined(cylinder_volume, domain_cell,
                                        single_mesh_level_offset(config))
        else:
            size = domain_cell / 2**refinement.interface_level
            interface = band(cylinder_area, size)
    if not layout.single_mesh:
        rotor_background = _cells(rotor_grid(config).cells)

    finest = zone_cell_size(config) / 2**refinement.blade_max_level
    blade = band(rotor_area_m2, finest)
    feature = 0
    if refinement.extract_features and refinement.feature_level > refinement.blade_max_level:
        feature = band(rotor_area_m2 * feature_area_fraction,
                       zone_cell_size(config) / 2**refinement.feature_level)
    layers = int(config.layers.count * rotor_area_m2 / finest**2) if config.layers.enabled else 0
    return VawtCellEstimate(outer_background, wake, interface, rotor_background, blade,
                            feature, layers)


def assess(config: VawtProjectConfig, rotor_area_m2: float, system: SystemResources,
           estimator: ResourceEstimator | None = None) -> tuple[VawtCellEstimate,
                                                                ResourceAssessment]:
    """Classify the estimate. In AMI mode the outer and rotor meshes are built
    one after the other, so RAM follows the larger of the two; disk follows
    all cells. A single mesh is one step."""
    estimator = estimator or ResourceEstimator()
    estimate = estimate_cells(config, rotor_area_m2, estimator.model.surface_band_cells,
                              estimator.model.feature_area_fraction)
    cells = CellEstimate(
        background=estimate.outer_background + estimate.rotor_background,
        surface=estimate.wake + estimate.interface + estimate.blade,
        feature=estimate.feature, layers=estimate.layers,
    )
    peak = estimate.total if case_layout(config).single_mesh else max(
        estimate.outer, estimate.rotor)
    return estimate, estimator.assess_cells(cells, system, config.max_global_cells,
                                            peak_cells=peak)
