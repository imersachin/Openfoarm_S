"""Machine presets as data (spec sections 5.4 and 8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from geometry.importer import import_stl
from machines.config import DomainKind, MachineType
from machines.presets import DOMAIN_CHOICES, PRESET_NOTE
from machines.presets.vawt import vawt_draft
from machines.vawt_migration import from_vawt, to_vawt
from tests.fixtures.vawt.drafts import preset_draft
from tests.fixtures.vawt.rotor import write_rotor
from vawt.config import Axis, VawtProjectConfig
from vawt.presets import PresetKind
from vawt.rotor_metrics import compute_rotor_metrics


def test_domain_choices_follow_section_5_4() -> None:
    assert set(DOMAIN_CHOICES) == set(MachineType)
    assert {m: c.default for m, c in DOMAIN_CHOICES.items()} == {
        MachineType.HAWT: DomainKind.CYLINDER, MachineType.VAWT: DomainKind.BOX,
        MachineType.VAWT_POLE: DomainKind.BOX, MachineType.FRANCIS: DomainKind.IMPORTED,
        MachineType.CUSTOM: DomainKind.BOX}
    assert DOMAIN_CHOICES[MachineType.HAWT].allowed == {DomainKind.CYLINDER, DomainKind.BOX}
    assert DOMAIN_CHOICES[MachineType.FRANCIS].allowed == {DomainKind.IMPORTED}
    assert all(c.default in c.allowed for c in DOMAIN_CHOICES.values())
    assert [m for m, c in DOMAIN_CHOICES.items() if c.rotating_zones_only] == [MachineType.VAWT]


def test_preset_note_disclaims_recommendation() -> None:
    assert "not engineering recommendations" in PRESET_NOTE


@pytest.mark.parametrize("include_domain", [True, False])
@pytest.mark.parametrize("kind", list(PresetKind))
def test_vawt_preset_is_the_v1_preset_migrated(tmp_path: Path, kind: PresetKind,
                                               include_domain: bool) -> None:
    path = write_rotor(tmp_path / "rotor_z.stl")
    mesh = import_stl(path).mesh
    assert mesh is not None
    metrics = compute_rotor_metrics(mesh.vertices, Axis.Z)
    base = {"project_name": "Fixture",
            "geometry": {"source_path": str(path), "source_units": "m"}}

    draft = vawt_draft(base, metrics, Axis.X, kind, include_domain=include_domain)

    expected = VawtProjectConfig.model_validate(
        preset_draft(tmp_path, kind=kind, include_domain=include_domain))
    assert draft == from_vawt(expected)
    assert to_vawt(draft) == expected


def test_vawt_preset_needs_a_complete_base(tmp_path: Path) -> None:
    path = write_rotor(tmp_path / "rotor.stl")
    mesh = import_stl(path).mesh
    assert mesh is not None
    metrics = compute_rotor_metrics(mesh.vertices, Axis.Z)

    with pytest.raises(ValidationError):
        vawt_draft({"project_name": "P", "geometry": {"source_path": str(path)}},
                   metrics, Axis.X)
