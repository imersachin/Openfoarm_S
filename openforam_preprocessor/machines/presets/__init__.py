"""Machine presets as data (docs/rotating_machinery.md sections 5.4 and 8).

Values are starting points, never recommendations, and a preset only fills a
draft. Each machine's numbers live in its own module (vawt.py; hawt.py,
francis.py and vawt_pole.py come with G4, G5 and G8); the choices every
machine shares are recorded here.
"""

from __future__ import annotations

from dataclasses import dataclass

from machines.config import DomainKind, MachineType

PRESET_NOTE = (
    "Preset values are starting points, not engineering recommendations. "
    "Review every value for your case."
)


@dataclass(frozen=True)
class DomainChoice:
    """Section 5.4: the domain a machine starts with, and the ones it allows."""

    default: DomainKind
    allowed: frozenset[DomainKind]
    rotating_zones_only: bool = False  # no domain at all (VAWT preset only)


DOMAIN_CHOICES: dict[MachineType, DomainChoice] = {
    MachineType.HAWT: DomainChoice(DomainKind.CYLINDER,
                                   frozenset({DomainKind.CYLINDER, DomainKind.BOX})),
    MachineType.VAWT: DomainChoice(DomainKind.BOX,
                                   frozenset({DomainKind.BOX, DomainKind.CYLINDER}),
                                   rotating_zones_only=True),
    MachineType.VAWT_POLE: DomainChoice(DomainKind.BOX,
                                        frozenset({DomainKind.BOX, DomainKind.CYLINDER})),
    MachineType.FRANCIS: DomainChoice(DomainKind.IMPORTED, frozenset({DomainKind.IMPORTED})),
    MachineType.CUSTOM: DomainChoice(DomainKind.BOX, frozenset(DomainKind)),
}
