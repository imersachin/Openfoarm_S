"""VAWT preset: the V1 preset values (vawt.presets), as a machine configuration.

The numbers stay in vawt.presets, so both workflows start from the same draft.
"""

from __future__ import annotations

from typing import Any

from machines.config import MachineProjectConfig
from machines.vawt_migration import from_vawt
from vawt.config import Axis, VawtProjectConfig
from vawt.presets import PresetKind, draft_from_preset
from vawt.rotor_metrics import RotorMetrics


def vawt_draft(
    base: dict[str, Any],
    metrics: RotorMetrics,
    flow_axis: Axis,
    kind: PresetKind = PresetKind.SIMPLE,
    *,
    include_domain: bool = True,
) -> MachineProjectConfig:
    """The VAWT preset draft for a confirmed axis, in the machine model.

    `base` is the VAWT base draft (project name, geometry, ...), as for
    vawt.presets.draft_from_preset. Raises pydantic.ValidationError when it is
    incomplete. Nothing is saved.
    """
    draft = draft_from_preset(base, metrics, flow_axis, kind, include_domain=include_domain)
    return from_vawt(VawtProjectConfig.model_validate(draft))
