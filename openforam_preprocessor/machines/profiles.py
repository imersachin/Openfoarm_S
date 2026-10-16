"""Machine profiles: how the UI presents each kind of machine (G7;
docs/rotating_machinery.md section 27).

A profile names the sections a machine shows and their labels, the words the
UI uses for it ("rotor", "runner"), the preset that starts a draft and, for a
Francis turbine, the named file slots. Profiles are data: JSON files in
machines/profile_data/, loaded into a ProfileRegistry. A new machine type is a
new profile file built from the known sections and presets, not new UI code.

The configuration model is unchanged. A profile names the MachineType its
projects are saved as; a profile for a machine the model does not name (a
Kaplan turbine, say) uses CUSTOM.

Each section owns configuration fields (dotted prefixes, `*` for any one
segment). A validation finding goes to the present section owning the
longest prefix of its field; a field no present section owns is shown in
Review only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from machines.config import MachineType

PROFILE_DATA = Path(__file__).with_name("profile_data")
_STRICT = ConfigDict(frozen=True, extra="forbid")


class SectionGroup(StrEnum):
    SETUP = "Setup"
    PHYSICS = "Physics"
    RUN = "Run"
    RESULTS = "Results"
    TOOLS = "Tools"


@dataclass(frozen=True)
class SectionDefaults:
    group: SectionGroup
    label: str
    fields: tuple[str, ...] = ()


# The sections the UI can draw (one renderer each), with the fields they own.
SECTION_CATALOGUE: dict[str, SectionDefaults] = {
    "project": SectionDefaults(SectionGroup.SETUP, "Project",
                               ("schema_version", "project_name", "openfoam_profile", "machine")),
    "bodies": SectionDefaults(SectionGroup.SETUP, "Bodies", ("bodies",)),
    "parts": SectionDefaults(SectionGroup.SETUP, "Parts",
                             ("domain", "rotating_zones", "joints")),
    "domain": SectionDefaults(SectionGroup.SETUP, "Domain", ("domain", "joints", "wake")),
    "zones": SectionDefaults(SectionGroup.SETUP, "Rotating zones", ("rotating_zones",)),
    "patches": SectionDefaults(SectionGroup.SETUP, "Patches", ("patches",)),
    "refinement": SectionDefaults(SectionGroup.SETUP, "Refinement", (
        "bodies.*.refinement", "domain.parts.*.refinement",
        "rotating_zones.*.shape.refinement", "rotating_zones.*.interface_level",
        "rotating_zones.*.shape.hole_level", "snappy_quality", "max_global_cells")),
    "layers": SectionDefaults(SectionGroup.SETUP, "Boundary layers", ("bodies.*.layers",)),
    "fluid": SectionDefaults(SectionGroup.PHYSICS, "Fluid and turbulence",
                             ("physics", "physics.fluid", "physics.turbulence")),
    "boundary": SectionDefaults(SectionGroup.PHYSICS, "Boundary values",
                                ("physics.inlets", "physics.outlets")),
    "rotation": SectionDefaults(SectionGroup.PHYSICS, "Rotation", ("physics.rotation",)),
    "operating_point": SectionDefaults(SectionGroup.PHYSICS, "Operating point",
                                       ("physics.inlets", "physics.outlets",
                                        "physics.rotation")),
    "time": SectionDefaults(SectionGroup.PHYSICS, "Time", ("physics.time",)),
    "review": SectionDefaults(SectionGroup.RUN, "Review"),
    "run": SectionDefaults(SectionGroup.RUN, "Run"),
    "mesh": SectionDefaults(SectionGroup.RESULTS, "Mesh", ("quality",)),
    "case": SectionDefaults(SectionGroup.RESULTS, "Case setup"),
    "export": SectionDefaults(SectionGroup.RESULTS, "Export", ("export",)),
    "history": SectionDefaults(SectionGroup.TOOLS, "History"),
    "logs": SectionDefaults(SectionGroup.TOOLS, "Logs"),
}
GROUP_ORDER = tuple(SectionGroup)


class PresetKind(StrEnum):
    """The preset that starts a draft (machines/presets)."""

    VAWT = "VAWT"  # rotor STL, rotor axis, flow axis
    HAWT = "HAWT"  # rotor STL, rotor axis
    FRANCIS = "FRANCIS"  # named file slots, rotation axis, a point on it, runner D
    NONE = "NONE"  # no preset: the general workflow


class SectionSpec(BaseModel):
    model_config = _STRICT

    key: str
    label: str | None = None  # default: the catalogue label
    help: str = ""
    fields: tuple[str, ...] | None = None  # default: the catalogue fields

    @model_validator(mode="after")
    def validate_key(self) -> SectionSpec:
        if self.key not in SECTION_CATALOGUE:
            raise ValueError(f"Unknown section '{self.key}'; known: "
                             f"{', '.join(SECTION_CATALOGUE)}.")
        return self

    @property
    def group(self) -> SectionGroup:
        return SECTION_CATALOGUE[self.key].group

    @property
    def title(self) -> str:
        return self.label or SECTION_CATALOGUE[self.key].label

    @property
    def owned(self) -> tuple[str, ...]:
        return self.fields if self.fields is not None else SECTION_CATALOGUE[self.key].fields


class FileSlot(BaseModel):
    """One named surface of a Francis-type machine: one STL, its patches as
    named regions inside it (decision after G6, Q1)."""

    model_config = _STRICT

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)  # the part's name
    label: str
    role: Literal["part", "runner"]  # a stationary domain part, or the rotating runner
    required: bool = True
    help: str = ""


class MachineProfile(BaseModel):
    model_config = _STRICT

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    label: str
    description: str
    machine: MachineType  # what its projects are saved as
    preset: PresetKind
    sections: tuple[SectionSpec, ...]
    terms: dict[str, str] = Field(default_factory=dict)
    slots: tuple[FileSlot, ...] = ()
    speed_inputs: tuple[Literal["RPM", "TIP_SPEED_RATIO"], ...] = ("RPM", "TIP_SPEED_RATIO")
    order: int = 100  # position on the machine chooser

    @model_validator(mode="after")
    def validate_profile(self) -> MachineProfile:
        keys = [s.key for s in self.sections]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Profile {self.id}: a section is listed twice.")
        if "project" not in keys:
            raise ValueError(f"Profile {self.id}: the project section is required.")
        if not self.speed_inputs:
            raise ValueError(f"Profile {self.id}: at least one speed input is needed.")
        if self.preset is PresetKind.FRANCIS:
            runners = [s for s in self.slots if s.role == "runner"]
            if len(runners) != 1 or not runners[0].required:
                raise ValueError(f"Profile {self.id}: a Francis preset needs exactly one "
                                 "required runner slot.")
            if not any(s.role == "part" and s.required for s in self.slots):
                raise ValueError(f"Profile {self.id}: at least one required part slot.")
            slot_keys = [s.key for s in self.slots]
            if len(set(slot_keys)) != len(slot_keys):
                raise ValueError(f"Profile {self.id}: slot keys must be unique.")
        elif self.slots:
            raise ValueError(f"Profile {self.id}: file slots need the FRANCIS preset.")
        return self

    def term(self, key: str, default: str) -> str:
        return self.terms.get(key, default)

    def section(self, key: str) -> SectionSpec | None:
        return next((s for s in self.sections if s.key == key), None)

    def section_keys(self, group: SectionGroup | None = None) -> tuple[str, ...]:
        return tuple(s.key for s in self.sections if group is None or s.group is group)

    def section_of_field(self, path: str) -> str | None:
        """The present section owning the longest prefix of `path`, or None."""
        parts = path.split(".")
        best: tuple[int, str] | None = None
        for spec in self.sections:
            for owned in spec.owned:
                pattern = owned.split(".")
                matches = len(pattern) <= len(parts) and all(
                    p in ("*", q) for p, q in zip(pattern, parts, strict=False))
                if matches and (best is None or len(pattern) > best[0]):
                    best = (len(pattern), spec.key)
        return best[1] if best else None


class ProfileRegistry:
    """Profiles by id, in chooser order."""

    def __init__(self, profiles: Iterable[MachineProfile]) -> None:
        self._profiles: dict[str, MachineProfile] = {}
        for profile in sorted(profiles, key=lambda p: (p.order, p.id)):
            if profile.id in self._profiles:
                raise ValueError(f"Two profiles have the id '{profile.id}'.")
            self._profiles[profile.id] = profile

    @classmethod
    def from_directory(cls, directory: Path) -> ProfileRegistry:
        """Every *.json file of `directory`; ValueError names a malformed file."""
        profiles = []
        for path in sorted(directory.glob("*.json")):
            try:
                profiles.append(MachineProfile.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))))
            except ValueError as exc:  # JSON or validation
                raise ValueError(f"Machine profile {path.name} is not valid: {exc}") from exc
        return cls(profiles)

    @classmethod
    def from_data(cls, items: Iterable[Mapping[str, object]]) -> ProfileRegistry:
        return cls(MachineProfile.model_validate(item) for item in items)

    def get(self, profile_id: str) -> MachineProfile | None:
        return self._profiles.get(profile_id)

    def all(self) -> tuple[MachineProfile, ...]:
        return tuple(self._profiles.values())

    def for_machine(self, machine: MachineType) -> MachineProfile | None:
        """The first profile whose projects are saved as `machine` (a saved
        project with no recorded profile)."""
        return next((p for p in self._profiles.values() if p.machine is machine), None)


_DEFAULT: ProfileRegistry | None = None


def default_registry() -> ProfileRegistry:
    """The profiles shipped in machines/profile_data/ (loaded once)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ProfileRegistry.from_directory(PROFILE_DATA)
    return _DEFAULT
