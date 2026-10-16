"""G7, owner addition A: machine profiles as data, in a registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from machines.config import MachineType
from machines.profiles import (
    PROFILE_DATA,
    MachineProfile,
    PresetKind,
    ProfileRegistry,
    SectionGroup,
    default_registry,
)


def profile(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "kaplan", "label": "Kaplan turbine", "description": "Adjustable blades.",
        "machine": "CUSTOM", "preset": "NONE",
        "sections": [{"key": "project"}, {"key": "bodies", "label": "Runner blades"},
                     {"key": "domain"}, {"key": "zones"}, {"key": "patches"},
                     {"key": "fluid"}, {"key": "rotation"}, {"key": "run"}]}
    base.update(changes)
    return base


def test_the_shipped_profiles() -> None:
    registry = default_registry()
    assert [(p.id, p.machine, p.preset) for p in registry.all()] == [
        ("vawt", MachineType.VAWT, PresetKind.VAWT),
        ("hawt", MachineType.HAWT, PresetKind.HAWT),
        ("francis", MachineType.FRANCIS, PresetKind.FRANCIS),
        ("custom", MachineType.CUSTOM, PresetKind.NONE)]
    assert registry.for_machine(MachineType.VAWT_POLE) is None  # G8, not on the chooser
    assert sorted(p.name for p in PROFILE_DATA.glob("*.json")) == [
        "custom.json", "francis.json", "hawt.json", "vawt.json"]


def test_francis_has_five_named_slots_with_optional_stay_vanes() -> None:
    francis = default_registry().get("francis")
    assert francis is not None
    assert [(s.key, s.role, s.required) for s in francis.slots] == [
        ("spiral_casing", "part", True), ("stay_vanes", "part", False),
        ("guide_vanes", "part", True), ("runner", "runner", True),
        ("draft_tube", "part", True)]
    assert francis.speed_inputs == ("RPM",)
    assert "operating_point" in francis.section_keys()
    assert "boundary" not in francis.section_keys() and "rotation" not in francis.section_keys()


def test_each_machine_uses_its_own_terms() -> None:
    registry = default_registry()
    titles = {p.id: {s.key: s.title for s in p.sections} for p in registry.all()}
    assert titles["hawt"]["bodies"] == "Rotor"
    assert titles["hawt"]["rotation"] == "Rotor speed"
    assert titles["francis"]["parts"] == "Water passage"
    assert titles["francis"]["patches"] == "Patch types"
    assert titles["custom"]["bodies"] == "Bodies"
    francis = registry.get("francis")
    assert francis is not None and francis.term("zone", "zone") == "runner"


@pytest.mark.parametrize("profile_id, path, section", [
    ("hawt", "bodies.0.source.scale", "bodies"),
    ("hawt", "bodies.0.layers.count", "layers"),  # the longer prefix wins
    ("hawt", "bodies.0.refinement.max_level", "refinement"),
    ("hawt", "physics.inlets.inlet", "boundary"),
    ("hawt", "physics.time.max_courant", "time"),
    ("hawt", "rotating_zones.0.interface_level", "refinement"),
    ("hawt", "rotating_zones.0.cell_size", "zones"),
    ("francis", "domain.parts.1.cell_size", "parts"),
    ("francis", "domain.parts.1.refinement.vanes", "refinement"),
    ("francis", "physics.inlets.inlet", "operating_point"),
    ("francis", "physics.rotation.runner.direction", "operating_point"),
    ("francis", "joints.0.first", "parts"),
    ("custom", "joints.0.first", "domain"),
    ("custom", "quality.max_non_orthogonality", "mesh"),
    ("hawt", "nothing.here", None),
])
def test_a_field_belongs_to_the_present_section_owning_its_longest_prefix(
        profile_id: str, path: str, section: str | None) -> None:
    found = default_registry().get(profile_id)
    assert found is not None
    assert found.section_of_field(path) == section


def test_groups_follow_the_catalogue() -> None:
    hawt = default_registry().get("hawt")
    assert hawt is not None
    assert hawt.section_keys(SectionGroup.PHYSICS) == ("fluid", "boundary", "rotation", "time")
    assert hawt.section_keys(SectionGroup.RESULTS) == ("mesh", "case", "export")


@pytest.mark.parametrize("changes, message", [
    ({"sections": [{"key": "project"}, {"key": "warp_drive"}]}, "Unknown section"),
    ({"sections": [{"key": "project"}, {"key": "bodies"}, {"key": "bodies"}]}, "twice"),
    ({"sections": [{"key": "bodies"}]}, "project section is required"),
    ({"preset": "FRANCIS", "slots": []}, "exactly one required runner"),
    ({"slots": [{"key": "a", "label": "A", "role": "part"}]}, "need the FRANCIS preset"),
    ({"speed_inputs": []}, "speed input"),
    ({"id": "Kaplan Turbine"}, "id"),
])
def test_malformed_profiles_are_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        MachineProfile.model_validate(profile(**changes))


def test_a_new_machine_type_is_a_new_data_file(tmp_path: Path) -> None:
    (tmp_path / "kaplan.json").write_text(json.dumps(profile()), encoding="utf-8")
    registry = ProfileRegistry.from_directory(tmp_path)
    kaplan = registry.get("kaplan")
    assert kaplan is not None
    assert kaplan.machine is MachineType.CUSTOM  # the model is unchanged
    assert [s.title for s in kaplan.sections][:2] == ["Project", "Runner blades"]
    assert registry.for_machine(MachineType.CUSTOM) is kaplan


def test_a_malformed_data_file_is_named(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.json"):
        ProfileRegistry.from_directory(tmp_path)


def test_two_profiles_with_one_id_are_refused() -> None:
    with pytest.raises(ValueError, match="kaplan"):
        ProfileRegistry.from_data([profile(), profile(label="Again")])
