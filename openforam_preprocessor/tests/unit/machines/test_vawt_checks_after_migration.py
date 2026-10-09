"""G1 review findings: a migrated VAWT project keeps every VAWT check, and to_vawt
refuses what it would change (decision E6)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from core.issues import Issue
from machines.config import MachineProjectConfig
from machines.validation import validate_machine
from machines.vawt_migration import NotVawtConvertible, from_vawt, to_vawt
from tests.fixtures.vawt.drafts import preset_draft
from vawt.case_generator import domain_grid
from vawt.config import VawtProjectConfig
from vawt.validation import validate_vawt


def set_(data: dict[str, Any], **values: Any) -> dict[str, Any]:
    data = copy.deepcopy(data)
    for path, value in values.items():
        *parents, leaf = path.split("__")
        target = data
        for key in parents:
            target = target.setdefault(key, {})
        target[leaf] = value
    return data


def stopping(issues: tuple[Issue, ...] | list[Issue]) -> dict[str, str]:
    return {i.code: i.severity.value for i in issues if i.is_stopping}


def both(raw: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    vawt = validate_vawt(raw)
    machine = from_vawt(VawtProjectConfig.model_validate(raw))
    return stopping(vawt.issues), stopping(validate_machine(
        machine.model_dump(mode="json")).issues)


def _point_on_face(draft: dict[str, Any]) -> dict[str, Any]:
    grid = domain_grid(VawtProjectConfig.model_validate(draft))
    x = grid.minimum[0] + 10 * grid.spacing(0)
    return set_(draft, domain__location_in_mesh={"x": x, "y": 0.0131, "z": 0.0173})


def _thick_layers(draft: dict[str, Any]) -> dict[str, Any]:
    finest = draft["rotating_zone"]["cell_size"] / 2 ** 2
    return set_(draft, layers__enabled=True, layers__sizing="ABSOLUTE",
                layers__min_thickness_m=1e-6, layers__first_layer_thickness=finest * 2)


CASES = {
    "ami_without_domain": ("AMI_REQUIRES_DOMAIN", "BLOCKING"),
    "wake_outside_domain": ("WAKE_OUTSIDE_DOMAIN", "ERROR"),
    "outer_point_on_cell_face": ("MESH_POINT_ON_CELL_FACE", "ERROR"),
    "first_layer_too_thick": ("ABSOLUTE_LAYER_TOO_THICK", "ERROR"),
}


def raw_case(tmp_path: Path, name: str) -> dict[str, Any]:
    if name == "ami_without_domain":
        return set_(preset_draft(tmp_path, include_domain=False),
                    rotating_zone__interface="AMI")
    draft = preset_draft(tmp_path)
    if name == "wake_outside_domain":
        return set_(draft, refinement__wake__box__maximum__x=100.0)
    if name == "outer_point_on_cell_face":
        return _point_on_face(draft)
    return _thick_layers(draft)


@pytest.mark.parametrize("name", list(CASES))
def test_vawt_stops_stay_stops_after_migration(tmp_path: Path, name: str) -> None:
    code, severity = CASES[name]

    vawt, machine = both(raw_case(tmp_path, name))

    assert vawt.get(code) == severity
    assert machine.get(code) == severity


def test_vawt_checks_add_nothing_to_valid_presets(tmp_path: Path) -> None:
    for include_domain in (True, False):
        vawt, machine = both(preset_draft(tmp_path, include_domain=include_domain))
        assert vawt == machine == {}


def test_vawt_checks_only_for_convertible_vawt_projects(tmp_path: Path) -> None:
    raw = raw_case(tmp_path, "wake_outside_domain")
    machine = from_vawt(VawtProjectConfig.model_validate(raw)).model_dump(mode="json")
    machine["rotating_zones"][0]["name"] = "turbine"  # no longer convertible
    machine["bodies"][0]["zone"] = "turbine"

    assert "WAKE_OUTSIDE_DOMAIN" not in stopping(validate_machine(machine).issues)


def _migrated(tmp_path: Path) -> MachineProjectConfig:
    return from_vawt(VawtProjectConfig.model_validate(preset_draft(tmp_path)))


def test_to_vawt_refuses_a_renamed_zone_or_body(tmp_path: Path) -> None:
    machine = _migrated(tmp_path)
    zone, body = machine.rotating_zones[0], machine.bodies[0]
    renamed_zone = machine.model_copy(update={
        "rotating_zones": (zone.model_copy(update={"name": "turbine"}),),
        "bodies": (body.model_copy(update={"zone": "turbine"}),)})
    renamed_body = machine.model_copy(update={
        "bodies": (body.model_copy(update={"name": "blade_set"}),),
        "patches": tuple(p.model_copy(update={"source": p.source.model_copy(
            update={"ref": "blade_set"})}) if p.source.ref == body.name else p
            for p in machine.patches)})

    for config in (renamed_zone, renamed_body):
        with pytest.raises(NotVawtConvertible):
            to_vawt(config)


def test_to_vawt_reports_model_errors_as_not_convertible(tmp_path: Path) -> None:
    # Rotor patch named like the inlet: the VAWT model rejects the names.
    machine = _migrated(tmp_path)
    patches = tuple(p.model_copy(update={"name": "inlet"}) if p.name == "rotor" else p
                    for p in machine.patches)
    body = machine.bodies[0].model_copy(update={"name": "inlet"})
    patches = tuple(p.model_copy(update={"source": p.source.model_copy(update={"ref": "inlet"})})
                    if p.source.kind.value == "BODY" else p for p in patches)

    with pytest.raises(NotVawtConvertible) as caught:
        to_vawt(machine.model_copy(update={"patches": patches, "bodies": (body,)}))
    assert "VAWT model rejects" in str(caught.value)
