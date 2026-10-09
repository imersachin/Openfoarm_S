"""Resource preflight for machine meshing (as vawt.preflight).

Arithmetic on the configuration, the background grids and the body areas:
a heuristic risk classification with the engine's statuses and thresholds,
never a prediction. The domain and zone meshes are built one after another,
so RAM follows the largest of them; disk follows all cells.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import trimesh

from machines.config import (
    BoxDomain,
    CylinderDomain,
    CylinderZone,
    ImportedDomain,
    MachineProjectConfig,
    Motion,
)
from machines.domains import SurfaceInfo, box_grid, cylinder_grid, surface_grid
from machines.zones import zone_grid, zone_surface
from mesh.estimator import CellEstimate, ResourceAssessment, ResourceEstimator, SystemResources
from vawt.case_generator import Grid


def _cells(grid: Grid) -> int:
    return grid.cells[0] * grid.cells[1] * grid.cells[2]


@dataclass(frozen=True)
class MachineCellEstimate:
    """Heuristic cells per meshed case: background, surface bands, layers."""

    background: dict[str, int] = field(default_factory=dict)
    surface: dict[str, int] = field(default_factory=dict)
    layers: dict[str, int] = field(default_factory=dict)

    def case(self, name: str) -> int:
        return (self.background.get(name, 0) + self.surface.get(name, 0)
                + self.layers.get(name, 0))

    @property
    def per_case(self) -> dict[str, int]:
        names = {*self.background, *self.surface, *self.layers}
        return {name: self.case(name) for name in sorted(names)}

    @property
    def total(self) -> int:
        return sum(self.per_case.values())


def estimate_cells(config: MachineProjectConfig, meshes: Mapping[str, trimesh.Trimesh],
                   surfaces: Sequence[SurfaceInfo], band_cells: float = 4.0
                   ) -> MachineCellEstimate:
    estimate = MachineCellEstimate()

    def add(table: dict[str, int], case: str, cells: float) -> None:
        table[case] = table.get(case, 0) + int(cells)

    def band(area: float, size: float) -> float:
        return band_cells * area / size**2

    domain = config.domain
    domain_cell = None
    if isinstance(domain, BoxDomain):
        add(estimate.background, "domain", _cells(box_grid(domain)))
        domain_cell = domain.cell_size
    elif isinstance(domain, CylinderDomain):
        add(estimate.background, "domain", _cells(cylinder_grid(domain)))
        domain_cell = domain.cell_size
    elif isinstance(domain, ImportedDomain):
        for surface, part in zip([s for s in surfaces if s.owner.startswith("domain.parts.")],
                                 domain.parts, strict=False):
            grid = surface_grid(surface, part.cell_size)
            if grid is not None:
                add(estimate.background, f"domain_{part.name}", _cells(grid))
        domain_cell = min(p.cell_size for p in domain.parts)

    for zone in config.rotating_zones:
        if not isinstance(zone.shape, CylinderZone):
            continue
        case = f"zone_{zone.name}"
        add(estimate.background, case, _cells(zone_grid(zone)))
        if domain_cell is not None:
            area = sum(float(m.area) for _, m in zone_surface(zone))
            add(estimate.surface, "domain", band(area, domain_cell / 2**zone.interface_level))

    for body in config.bodies:
        mesh = meshes.get(body.name)
        if mesh is None:
            continue
        own = config.zone(body.zone or "")
        cell = own.cell_size if own is not None else domain_cell
        if cell is None:
            continue
        case = (f"zone_{own.name}" if own is not None and body.motion is not Motion.STATIONARY
                else "domain")
        finest = cell / 2**body.refinement.max_level
        area = float(mesh.area)
        add(estimate.surface, case, band(area, finest))
        if body.layers.enabled:
            add(estimate.layers, case, body.layers.count * area / finest**2)
    return estimate


def assess(config: MachineProjectConfig, meshes: Mapping[str, trimesh.Trimesh],
           surfaces: Sequence[SurfaceInfo], system: SystemResources,
           estimator: ResourceEstimator | None = None
           ) -> tuple[MachineCellEstimate, ResourceAssessment]:
    estimator = estimator or ResourceEstimator()
    estimate = estimate_cells(config, meshes, surfaces, estimator.model.surface_band_cells)
    cells = CellEstimate(background=sum(estimate.background.values()),
                         surface=sum(estimate.surface.values()), feature=0,
                         layers=sum(estimate.layers.values()))
    peak = max(estimate.per_case.values(), default=0)
    return estimate, estimator.assess_cells(cells, system, config.max_global_cells,
                                            peak_cells=peak)
