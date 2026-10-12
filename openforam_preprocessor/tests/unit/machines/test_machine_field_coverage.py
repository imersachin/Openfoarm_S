"""Every machine configuration field reaches a cached meshing input, or is
declared inactive (the machines equivalent of the VAWT field-coverage test).

The machine pipeline caches each operation on the files it consumes (G4, H2):
the case dictionaries and surfaces, the retype map, and the OpenFOAM tool
identity (from the profile). So a field affects the mesh only if changing it
changes one of those. For every leaf field of several fixture variants the
test changes it and compares a fingerprint of all of them:

- changed: the field reaches a cached input; it must not be declared inactive;
- unchanged: it must be declared INACTIVE here, with a reason and a condition;
- fields the test does not change (file paths, references, model-variant
  selectors) are declared in NOT_CHANGED with a reason.

Validation is not run: the question is what reaches the cache, and a
configuration that validation refuses never meshes anyway.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from machines.assembly import MachineCaseGenerator
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.validation import load_bodies
from tests.fixtures.machines import configs

Data = dict[str, Any]
Predicate = Callable[[Data, str], bool]


# --- fixture variants: every field active somewhere -------------------------------------

def _hawt_features_layers(directory: Path) -> Data:
    data = configs.hawt(directory)
    body = data["bodies"][0]
    body["refinement"] = {"extract_features": True, "feature_level": 2}
    body["layers"] = {"enabled": True}
    return data


def _hawt_absolute_layers(directory: Path) -> Data:
    data = configs.hawt(directory)
    data["bodies"][0]["layers"] = {"enabled": True, "sizing": "ABSOLUTE",
                                   "first_layer_thickness": 0.001, "min_thickness_m": 0.0005}
    return data


def _francis_refined(directory: Path) -> Data:
    data = configs.francis(directory)
    data["domain"]["parts"][1]["refinement"] = {"vanes": 2}  # the guide's vanes (K8)
    return data


VARIANTS: dict[str, Callable[[Path], Data]] = {
    "hawt": configs.hawt,
    "hawt_features_layers": _hawt_features_layers,
    "hawt_absolute_layers": _hawt_absolute_layers,
    "pole_split": configs.pole,
    "pole_annular": lambda d: configs.pole(d, motion="STATIONARY", hole_diameter=0.1),
    "francis_refined": _francis_refined,
    "duct": configs.duct,
}


# --- helpers -----------------------------------------------------------------------------

def _get(data: Data, path: str) -> Any:
    target: Any = data
    for key in path.split("."):
        target = target[int(key)] if isinstance(target, list) else target[key]
    return target


def _owner(data: Data, path: str, levels_up: int) -> Any:
    return _get(data, ".".join(path.split(".")[:-levels_up]))


def _with(data: Data, path: str, value: Any) -> Data:
    changed = copy.deepcopy(data)
    *parents, leaf = path.split(".")
    target = _get(changed, ".".join(parents)) if parents else changed
    if isinstance(target, list):
        target[int(leaf)] = value
    else:
        target[leaf] = value
    return changed


def pattern_of(path: str) -> str:
    pattern = re.sub(r"\.\d+(?=\.|$)", ".*", path)
    # Region keys of an imported surface's refinement map (K8).
    return re.sub(r"^(domain\.parts\.\*|rotating_zones\.\*\.shape)\.refinement\.\w+$",
                  r"\1.refinement.<region>", pattern)


def leaves(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(data, dict) and data:
        for key, value in data.items():
            yield from leaves(value, f"{prefix}{key}.")
    elif isinstance(data, list) and data:
        for index, value in enumerate(data):
            yield from leaves(value, f"{prefix}{index}.")
    elif not isinstance(data, dict | list):
        yield prefix.rstrip("."), data


def fingerprint(data: Data) -> str | None:
    """What the cached operations hash; None if the change is not a valid,
    renderable configuration."""
    try:
        config = MachineProjectConfig.model_validate(data)
        meshes, _ = load_bodies(config)
        cases = MachineCaseGenerator().render(config, read_imported(config), meshes,
                                              typed=False)
    except (ValidationError, ValueError, KeyError):
        return None
    digest = hashlib.sha256()
    for case in cases.all:
        for name in sorted(case.dictionaries):
            digest.update(f"{case.name}/{name}\0{case.dictionaries[name]}\0".encode())
        for name in sorted(case.surfaces):
            digest.update(f"{case.name}/{name}\0".encode() + case.surfaces[name])
    digest.update(repr(sorted(cases.retype.items())).encode())
    digest.update(config.openfoam_profile.value.encode())
    return digest.hexdigest()


# --- what the test changes ---------------------------------------------------------------

def _numbers(data: Data, path: str) -> list[Data]:
    value = _get(data, path)
    if isinstance(value, bool):
        return [_with(data, path, not value)]
    if isinstance(value, int):
        return [_with(data, path, value + 1), _with(data, path, value - 1)]
    if isinstance(value, float):
        return [_with(data, path, value * 1.07 + 0.0013), _with(data, path, value * 0.93 - 0.0011)]
    return []


def _choices(*options: str) -> Callable[[Data, str], list[Data]]:
    def change(data: Data, path: str) -> list[Data]:
        return [_with(data, path, o) for o in options if o != _get(data, path)]
    return change


def _rename(references: Callable[[Data, str, str], None]) -> Callable[[Data, str], list[Data]]:
    """A renamed object, with every reference to it renamed too."""
    def change(data: Data, path: str) -> list[Data]:
        old = _get(data, path)
        renamed = _with(data, path, f"{old}_renamed")
        references(renamed, old, f"{old}_renamed")
        return [renamed]
    return change


def _zone_references(data: Data, old: str, new: str) -> None:
    for body in data.get("bodies", []):
        if body.get("zone") == old:
            body["zone"] = new


def _body_references(data: Data, old: str, new: str) -> None:
    for patch in data.get("patches", []):
        if patch["source"] == {"kind": "BODY", "ref": old}:
            patch["source"]["ref"] = new


def _no_references(data: Data, old: str, new: str) -> None:
    return None


def _swap_ref(data: Data, path: str) -> list[Data]:
    """Point the patch at the source of another patch of the same kind (swap)."""
    index = int(path.split(".")[1])
    patches = data["patches"]
    kind = patches[index]["source"]["kind"]
    for other, patch in enumerate(patches):
        if other != index and patch["source"]["kind"] == kind:
            swapped = copy.deepcopy(data)
            a, b = swapped["patches"][index]["source"], swapped["patches"][other]["source"]
            a["ref"], b["ref"] = b["ref"], a["ref"]
            return [swapped]
    return []


TYPES = ("INLET", "OUTLET", "WALL", "ROTATING_WALL", "SLIP")
UNITS = ("cm", "mm", "in", "m")

CHANGES: dict[str, Callable[[Data, str], list[Data]]] = {
    "openfoam_profile": _choices("openfoam_foundation", "openfoam_com"),
    "bodies.*.name": _rename(_body_references),
    "bodies.*.motion": _choices("SPLIT", "ROTATING"),
    "bodies.*.layers.sizing": _choices("RELATIVE", "ABSOLUTE"),
    "bodies.*.source.source_units": _choices(*UNITS),
    "domain.parts.*.files.*.source_units": _choices(*UNITS),
    "rotating_zones.*.shape.files.*.source_units": _choices(*UNITS),
    "domain.parts.*.name": _rename(_no_references),
    "domain.parts.*.format": _choices("ONE_FILE_PER_PATCH", "NAMED_REGIONS"),
    "rotating_zones.*.shape.format": _choices("ONE_FILE_PER_PATCH", "NAMED_REGIONS"),
    "rotating_zones.*.name": _rename(_zone_references),
    "domain.axis": _choices("x", "y", "z"),
    "flow_axis": _choices("x", "y", "z"),
    "rotating_zones.*.axis": _choices("x", "y", "z"),
    "patches.*.name": _rename(_no_references),
    "patches.*.type": _choices(*TYPES),
    "patches.*.source.ref": _swap_ref,
}


def _layers(path: str, data: Data) -> Any:
    return _owner(data, path, 1)  # bodies.<i>.layers


def _layers_off(data: Data, path: str) -> bool:
    return not _layers(path, data)["enabled"]


def _relative(data: Data, path: str) -> bool:
    return _layers_off(data, path) or _layers(path, data)["sizing"] == "RELATIVE"


def _absolute(data: Data, path: str) -> bool:
    return _layers_off(data, path) or _layers(path, data)["sizing"] == "ABSOLUTE"


def _always(data: Data, path: str) -> bool:
    return True


INACTIVE: dict[str, tuple[str, Predicate]] = {
    "quality.max_non_orthogonality": ("acceptance limit: mesh validation always re-runs", _always),
    "quality.max_boundary_skewness": ("acceptance limit: mesh validation always re-runs", _always),
    "quality.max_internal_skewness": ("acceptance limit: mesh validation always re-runs", _always),
    "quality.min_volume": ("acceptance limit: mesh validation always re-runs", _always),
    "quality.min_determinant": ("acceptance limit: mesh validation always re-runs", _always),
    "bodies.*.refinement.feature_level": (
        "feature edges only when extract_features",
        lambda d, p: not _owner(d, p, 1)["extract_features"]),
    "bodies.*.layers.count": ("layers disabled", _layers_off),
    "bodies.*.layers.expansion_ratio": ("layers disabled", _layers_off),
    "bodies.*.layers.final_layer_thickness": ("RELATIVE sizing only", _absolute),
    "bodies.*.layers.min_thickness": ("RELATIVE sizing only", _absolute),
    "bodies.*.layers.first_layer_thickness": ("ABSOLUTE sizing only", _relative),
    "bodies.*.layers.min_thickness_m": ("ABSOLUTE sizing only", _relative),
    "bodies.*.layers.sizing": ("layers disabled", _layers_off),
    "rotating_zones.*.shape.hole_level": (
        "refines the hole of an annular zone only",
        lambda d, p: _owner(d, p, 1).get("hole_diameter") is None),
    "rotating_zones.*.shape.origin.x": ("rotation origin: case setup (G6), not meshing", _always),
    "rotating_zones.*.shape.origin.y": ("rotation origin: case setup (G6), not meshing", _always),
    "rotating_zones.*.shape.origin.z": ("rotation origin: case setup (G6), not meshing", _always),
    "rotating_zones.*.interface_level": (
        "an imported zone meets the parts at joints (INTERFACE_LEVEL_IGNORED)",
        lambda d, p: _owner(d, p, 1)["shape"]["kind"] == "IMPORTED"),
    "rotating_zones.*.interface": (
        "CELL_ZONE is refused for other machines (VAWT_ONLY_SETTING) and sends VAWT "
        "projects to the VAWT workflow (USE_VAWT_WORKFLOW)", _always),
    "flow_axis": ("chooses the preset's inlet and outlet faces and validation rules; the "
                  "patches name their faces explicitly", _always),
    "machine": ("selects validation rules and presets, not a meshing input", _always),
    "physics": ("case setup only, never a meshing input (invariant 14; G6): "
                "tests/unit/case_setup/test_g6_physics_coverage.py", _always),
    "export.fluent_msh": ("VAWT workflow only (VAWT_ONLY_SETTING otherwise)", _always),
    "wake": ("VAWT workflow only (VAWT_ONLY_SETTING otherwise)", _always),
    "rotating_zones.*.shape.hole_diameter": ("None: no hole (exercised by pole_annular)",
                                             lambda d, p: _get(d, p) is None),
    "bodies.*.name": (
        "the body's patch name is meshed (patches.*.name); a split body's name is meshed "
        "as <body>_rotating",
        lambda d, p: _owner(d, p, 1)["motion"] != "SPLIT" and any(
            q["source"] == {"kind": "BODY", "ref": _get(d, p)} for q in d["patches"])),
    "rotating_zones.*.axis": (
        "an imported zone's surface is its shape; the axis is the rotation axis for case "
        "setup (G6)", lambda d, p: _owner(d, p, 1)["shape"]["kind"] == "IMPORTED"),
}

NOT_CHANGED: dict[str, str] = {
    "schema_version": "the file format version; only the current one is valid",
    "project_name": "a label; reaches no case file",
    "bodies.*.source.source_path": "the file's content is the input, not its path "
                                   "(test_body_geometry_reruns_the_meshes_holding_it)",
    "domain.parts.*.files.*.source_path": "the file's content is the input, not its path",
    "rotating_zones.*.shape.files.*.source_path": "the file's content is the input, not "
                                                  "its path",
    "bodies.*.zone": "every fixture has one rotating zone; the zone selects the case the "
                     "body is meshed in (bodies.*.motion, rotating_zones.*.name)",
    "domain.kind": "selects the domain model; each kind is a fixture variant here",
    "rotating_zones.*.shape.kind": "selects the zone model; each kind is a fixture variant",
    "patches.*.source.kind": "changes only together with source.ref (patches.*.source.ref)",
    "joints.*.first": "a region reference; the pair is validated by the JOINT_* checks and "
                      "its level is covered by joints.*.level",
    "joints.*.second": "a region reference; see joints.*.first",
}


# --- the test ----------------------------------------------------------------------------

def coverage(mktemp: Callable[[str], Path]) -> dict[str, Any]:
    """Change every leaf field of every variant; classify each (see the module doc)."""
    active: set[str] = set()
    inactive: set[str] = set()
    seen: set[str] = set()
    failures: list[str] = []
    for name, build in VARIANTS.items():
        data = MachineProjectConfig.model_validate(build(mktemp(name))).model_dump(mode="json")
        reference = fingerprint(data)
        assert reference is not None, f"{name}: the fixture does not render"
        for path, value in leaves(data):
            pattern = pattern_of(path)
            seen.add(pattern)
            if pattern in NOT_CHANGED:
                continue
            declared = pattern in INACTIVE and INACTIVE[pattern][1](data, path)
            change = CHANGES.get(pattern, _numbers)
            results = [fingerprint(c) for c in change(data, path)]
            valid = [r for r in results if r is not None]
            if not valid:
                # Nothing valid to change here (one body, a fixed file count...): the
                # field must be exercised by another variant (test_every_field_is_
                # accounted_for), unless it is declared inactive here.
                if declared:
                    inactive.add(pattern)
                continue
            # A field reaches the cache if any valid change of it does: e.g. INLET,
            # OUTLET and SLIP all mesh as a plain patch, WALL does not.
            if any(r != reference for r in valid):
                active.add(pattern)
                if declared:
                    failures.append(f"{name}: {path} reaches a cached input but is declared "
                                    f"inactive ({INACTIVE[pattern][0]})")
            elif declared:
                inactive.add(pattern)
            else:
                failures.append(f"{name}: {path} = {value!r} reaches no cached meshing input")
    return {"active": active, "inactive": inactive, "seen": seen, "failures": failures}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return coverage(tmp_path_factory.mktemp)


def test_every_field_reaches_a_cached_input_or_is_declared_inactive(
        outcomes: dict[str, Any]) -> None:
    assert outcomes["failures"] == []


def test_every_field_is_accounted_for(outcomes: dict[str, Any]) -> None:
    covered = outcomes["active"] | outcomes["inactive"] | set(NOT_CHANGED)
    assert sorted(outcomes["seen"] - covered) == []


def test_conditionally_inactive_fields_are_active_when_enabled(
        outcomes: dict[str, Any]) -> None:
    # Each conditional declaration is exercised by a variant where the field is on.
    conditional = {p for p, (_, predicate) in INACTIVE.items() if predicate is not _always}
    unexercised = conditional - outcomes["active"]
    assert sorted(unexercised) == []


def test_declarations_name_real_fields(outcomes: dict[str, Any]) -> None:
    declared = set(INACTIVE) | set(NOT_CHANGED) | set(CHANGES)
    assert sorted(declared - outcomes["seen"]) == []
