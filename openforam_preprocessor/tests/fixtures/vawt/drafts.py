"""Valid VAWT drafts built from the fixture rotor via the presets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from geometry.importer import import_stl
from tests.fixtures.vawt.rotor import RotorSpec, write_rotor
from vawt.config import Axis
from vawt.presets import PresetKind, draft_from_preset
from vawt.rotor_metrics import compute_rotor_metrics


def preset_draft(
    directory: Path,
    *,
    axis: str = "z",
    flow_axis: str = "x",
    kind: PresetKind = PresetKind.SIMPLE,
    include_domain: bool = True,
    invert_body: int | None = None,
) -> dict[str, Any]:
    path = write_rotor(directory / f"rotor_{axis}.stl", RotorSpec(axis=axis),
                       invert_body=invert_body)
    mesh = import_stl(path).mesh
    assert mesh is not None
    metrics = compute_rotor_metrics(mesh.vertices, Axis(axis))
    base = {"project_name": "Fixture", "geometry": {"source_path": str(path),
                                                    "source_units": "m"}}
    return draft_from_preset(base, metrics, Axis(flow_axis), kind,
                             include_domain=include_domain)
