"""Checks on an imported-mesh project before any conversion (M1-MSH).

Everything the file and the configuration can tell without OpenFOAM: the
detection findings (machines.msh_import.detect), a file changed since it was
chosen, the rotating cell zones and AMI pairs against the zones and patches
the file names, a type for every patch, and the units against the box. An
imported mesh replaces the STL bodies, zones and domain: both at once is
BLOCKING (owner answer 3).
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from core.artifacts.hashing import sha256_json
from core.config.models import LengthUnit
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.config import (
    ImportedMesh,
    MachineParts,
    MachineProjectConfig,
    MachineType,
    MeshPart,
    PatchType,
    SourceKind,
)
from machines.msh_import.checks import no_cell_zone
from machines.msh_import.detect import MeshFileInfo, file_sha256, inspect_mesh_file
from machines.msh_import.parts import prefixed

_MESH_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:+-]*$")
# The VAWT workflow's rules (flow axis, presets) need its own geometry.
NOT_FOR_IMPORT = frozenset({MachineType.VAWT, MachineType.VAWT_POLE})


@dataclass(frozen=True)
class MshThresholds:
    # A domain whose largest extent, in metres, is outside this range is
    # probably in the wrong units (WARNING MSH_UNITS_SUSPECT). Provisional.
    min_extent_m: float = 0.01
    max_extent_m: float = 10_000.0


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           explanation: str = "", category: IssueCategory = IssueCategory.CONFIGURATION,
           **details: Any) -> Issue:
    return Issue(category=category, severity=severity, stage=IssueStage.CONFIGURATION,
                 code=code, message=message, explanation=explanation,
                 suggested_action=action, details=details)


def _blocking(code: str, message: str, action: str, explanation: str = "",
              **details: Any) -> Issue:
    return _issue(IssueSeverity.BLOCKING, code, message, action, explanation, **details)


@functools.lru_cache(maxsize=8)
def _read(path: str, size: int, modified: int) -> tuple[MeshFileInfo, str | None]:
    info = inspect_mesh_file(Path(path))
    return info, (file_sha256(Path(path)) if info.size else None)


def read_mesh_file(path: Path) -> tuple[MeshFileInfo, str | None]:
    """The file's detection and content hash, read once per file version
    (path, size, modification time)."""
    try:
        stat = path.stat()
    except OSError:
        return inspect_mesh_file(path), None
    return _read(str(path), stat.st_size, stat.st_mtime_ns)


def _stl_issues(config: MachineProjectConfig) -> list[Issue]:
    found = [name for name, present in (
        ("bodies", config.bodies), ("rotating_zones", config.rotating_zones),
        ("domain", config.domain), ("joints", config.joints), ("wake", config.wake)) if present]
    if not found:
        return []
    return [_blocking(
        "MSH_WITH_STL", "The project has an imported mesh and STL settings "
        f"({', '.join(found)}); an imported mesh replaces them.",
        "Remove the STL settings, or remove the imported mesh.", fields=found)]


def _zone_issues(config: MachineProjectConfig, info: MeshFileInfo) -> list[Issue]:
    mesh = config.imported_mesh
    assert mesh is not None
    if not mesh.zones:
        return [no_cell_zone(info, None, info.cell_zones)]
    found: list[Issue] = []
    for name in sorted(n for n, c in Counter(z.name for z in mesh.zones).items() if c > 1):
        found.append(_blocking("DUPLICATE_NAME", f"'{name}' is used twice in "
                               "imported_mesh.zones.", "Give each zone its own name.",
                               section="imported_mesh.zones", name=name))
    for i, zone in enumerate(mesh.zones):
        if zone.cell_zone not in info.cell_zones:
            issue = no_cell_zone(info, zone.cell_zone, info.cell_zones)
            found.append(replace(issue, details={
                **issue.details, "field": f"imported_mesh.zones.{i}.cell_zone"}))
        elif len(info.cell_zones) == 1:
            found.append(_blocking(
                "MSH_NO_CELL_ZONE", f"'{zone.cell_zone}' is the only cell zone of the mesh: "
                "it is the whole mesh, so nothing would stay stationary.",
                no_cell_zone(info, None, ()).suggested_action,
                field=f"imported_mesh.zones.{i}.cell_zone", zone=zone.cell_zone))
    return found


def _pair_issues(config: MachineProjectConfig, info: MeshFileInfo) -> list[Issue]:
    mesh = config.imported_mesh
    assert mesh is not None
    found: list[Issue] = []
    if mesh.zones and not mesh.ami_pairs:
        found.append(_blocking(
            "MSH_NO_INTERFACE", "The rotating zone has no interface: no AMI pair is chosen.",
            "Choose the two patches on either side of the rotating zone's boundary as an AMI "
            "pair.", "The rotating cells must meet the stationary ones at a cyclicAMI pair; "
            "a pair is never made up, only confirmed.", field="imported_mesh.ami_pairs"))
    used = Counter(n for p in mesh.ami_pairs for n in (p.first, p.second))
    typed = {p.source.ref for p in config.patches}
    for i, pair in enumerate(mesh.ami_pairs):
        field = f"imported_mesh.ami_pairs.{i}"
        if pair.first == pair.second:
            found.append(_blocking("MSH_AMI_PAIR_INVALID", f"AMI pair {i + 1} names "
                                   f"'{pair.first}' twice.", "Choose two different patches.",
                                   field=field))
        for name in (pair.first, pair.second):
            if name not in info.patches:
                found.append(_blocking(
                    "MSH_AMI_PATCH_UNKNOWN", f"AMI pair {i + 1} names '{name}', which is not "
                    "a patch of the mesh file.", "Choose patches of the mesh.",
                    field=field, patch=name, found=list(info.patches)))
            elif used[name] > 1:
                found.append(_blocking("MSH_AMI_PAIR_INVALID", f"'{name}' is in more than one "
                                       "AMI pair.", "Use each patch in one pair.",
                                       field=field, patch=name))
            if name in typed:
                found.append(_blocking(
                    "MSH_AMI_PATCH_TYPED", f"'{name}' is in an AMI pair and also has a patch "
                    "type.", "Remove its row from the patch table: an interface is coupled, "
                    "not typed.", field=field, patch=name))
    return found


def _patch_issues(config: MachineProjectConfig, info: MeshFileInfo) -> list[Issue]:
    mesh = config.imported_mesh
    assert mesh is not None
    found: list[Issue] = []
    known = set(info.patches)
    interfaces = {n for p in mesh.ami_pairs for n in (p.first, p.second)}
    for i, patch in enumerate(config.patches):
        field = f"patches.{i}"
        if patch.source.kind is not SourceKind.MESH_PATCH:
            found.append(_blocking(
                "PATCH_SOURCE_UNKNOWN", f"Patch '{patch.name}' comes from "
                f"{patch.source.kind.value} '{patch.source.ref}'; an imported mesh's patches "
                "come from the mesh (MESH_PATCH).", "Choose a patch of the mesh.",
                field=f"{field}.source", patch=patch.name))
        elif patch.source.ref not in known:
            found.append(_blocking(
                "PATCH_SOURCE_UNKNOWN", f"Patch '{patch.name}' comes from mesh patch "
                f"'{patch.source.ref}', which the file does not have.",
                "Choose one of: " + (", ".join(info.patches) or "(none)") + ".",
                field=f"{field}.source", patch=patch.name))
        if patch.name in known and patch.name != patch.source.ref:
            # A rename onto another patch of the mesh (an AMI patch keeps its name).
            found.append(_blocking(
                "RESERVED_PATCH_NAME", f"'{patch.name}' ({field}.name) is the name of another "
                "patch of the mesh.", "Choose another name.",
                field=f"{field}.name", name=patch.name))
    sources = Counter(p.source.ref for p in config.patches
                      if p.source.kind is SourceKind.MESH_PATCH)
    for ref in sorted(r for r, c in sources.items() if c > 1):
        found.append(_blocking(
            "PATCH_SOURCE_DUPLICATE", f"Mesh patch '{ref}' is the source of two patches.",
            "Give each mesh patch one row, so it has exactly one type.",
            source=f"MESH_PATCH:{ref}"))
    for name in info.patches:
        if name not in sources and name not in interfaces:
            found.append(_blocking(
                "MESH_PATCH_WITHOUT_TYPE", f"Mesh patch '{name}' has no type and is not in "
                "an AMI pair.", "Add it to the patch table and choose its type, or pair it.",
                patch=name, kind=info.patch_kinds.get(name)))
        if not _MESH_NAME.match(name):
            found.append(_blocking(
                "MSH_NAME_INVALID", f"The mesh patch name '{name}' cannot be used in OpenFOAM "
                "dictionaries.", "Rename it in the program that wrote the mesh (letters, "
                "digits, _ . : + -), then import again.", patch=name))
    for name in info.cell_zones:
        if not _MESH_NAME.match(name):
            found.append(_blocking(
                "MSH_NAME_INVALID", f"The cell zone name '{name}' cannot be used in OpenFOAM "
                "dictionaries.", "Rename it in the program that wrote the mesh, then import "
                "again.", zone=name))
    types = {p.type for p in config.patches}
    for wanted, code in ((PatchType.INLET, "NO_INLET"), (PatchType.OUTLET, "NO_OUTLET")):
        if wanted not in types:
            found.append(_blocking(code, f"No patch is of type {wanted.value}.",
                                   f"Choose the {wanted.value.lower()} patch in the patch "
                                   "table."))
    return found


def _units_issues(units: LengthUnit, info: MeshFileInfo, limits: MshThresholds,
                  field: str = "imported_mesh.source_units", what: str = "the mesh",
                  ) -> list[Issue]:
    if info.box is None:
        return []
    extent = max(h - lo for lo, h in zip(*info.box, strict=True)) * units.to_metres
    if limits.min_extent_m <= extent <= limits.max_extent_m:
        return []
    return [_issue(
        IssueSeverity.WARNING, "MSH_UNITS_SUSPECT",
        f"In {units.value} {what} is {extent:.4g} m across, outside "
        f"{limits.min_extent_m:g}-{limits.max_extent_m:g} m.",
        "Check the units against the bounding box.", category=IssueCategory.UNITS,
        field=field, extent_m=extent)]


# --- a machine of several files (M2b) ------------------------------------------------------------

def machine_info(mesh: MachineParts) -> MeshFileInfo:
    """The files read as the one mesh they make: each part's patches and cell
    zones under its prefix, its zone <prefix>, the periodic patches left out
    (stitched away), the declared cells times the passages. The zone, pair and
    patch checks of a single file run on it unchanged."""
    return machine_info_of([(p.prefix, p.source_path,
                             {n for q in p.periodic for n in (q.first, q.second)},
                             p.passages or 1) for p in mesh.parts])


def machine_info_of(parts: Sequence[tuple[str, Path, set[str], int]]) -> MeshFileInfo:
    """machine_info of (prefix, file, periodic patch names, passages) per part."""
    first, _ = read_mesh_file(parts[0][1])
    patches: list[str] = []
    kinds: dict[str, str] = {}
    zones: list[str] = []
    cells: int | None = 0
    size = 0
    for prefix, path, periodic, passages in parts:
        info, _ = read_mesh_file(path)
        size += info.size
        for name in info.patches:
            if name in periodic:
                continue
            used = prefixed(prefix, name)
            patches.append(used)
            if name in info.patch_kinds:
                kinds[used] = info.patch_kinds[name]
        zones += [prefix, *(prefixed(prefix, z) for z in info.cell_zones)]
        cells = (None if cells is None or info.declared_cells is None
                 else cells + info.declared_cells * passages)
    return MeshFileInfo(path=first.path, size=size, format=first.format, patches=tuple(patches),
                        patch_kinds=kinds, cell_zones=tuple(zones), declared_cells=cells)


def mesh_digest(mesh: ImportedMesh | MachineParts) -> str | None:
    """What the import report's sha256 is of: the file's content, or for a
    machine of several files the files with their prefixes, units and passages."""
    if isinstance(mesh, ImportedMesh):
        return mesh.sha256
    return sha256_json([[p.prefix, read_mesh_file(p.source_path)[1], p.source_units.value,
                         p.passages] for p in mesh.parts])


def mesh_paths(config: MachineProjectConfig) -> list[Path]:
    """The .msh files a project reads."""
    mesh = config.imported_mesh
    if mesh is None:
        return []
    return [p.source_path for p in mesh.parts] if isinstance(mesh, MachineParts) else [
        mesh.source_path]


def _part_issues(index: int, part: MeshPart, limits: MshThresholds,
                 ) -> tuple[MeshFileInfo, list[Issue]]:
    field = f"imported_mesh.parts.{index}"
    info, digest = read_mesh_file(part.source_path)
    found = [replace(i, message=f"{part.name}: {i.message}") for i in info.issues]
    if info.format is None or any(i.is_stopping for i in info.issues):
        return info, found
    if digest != part.sha256:
        found.append(_blocking(
            "MSH_SOURCE_CHANGED", f"{part.name}: {part.source_path.name} has changed since it "
            "was chosen.", "Choose the file again and confirm its units against the new "
            "bounding box.", field=f"{field}.sha256", expected=part.sha256, found=digest))
    found += _units_issues(part.source_units, info, limits, f"{field}.source_units",
                           f"{part.name} ({part.source_path.name})")
    if part.passages is None:
        found.append(_blocking(
            "PART_PASSAGES_NOT_CONFIRMED", f"{part.name}: the number of passages has not been "
            "confirmed.", "Read the parts, then confirm the passage count found for it (or "
            "that it is a whole ring).", "A part meshed as one passage is copied around the "
            "axis; the count is detected from its periodic patches and confirmed, never "
            "assumed.", field=f"{field}.passages", part=part.prefix))
    elif (part.passages > 1) != bool(part.periodic):
        found.append(_blocking(
            "PART_PASSAGES_INVALID", f"{part.name}: {part.passages} passage(s) with "
            f"{len(part.periodic)} periodic pair(s).", "Read the parts again and confirm what "
            "is found.", "Copies are stitched at the periodic pairs; a whole ring has none.",
            field=f"{field}.passages", part=part.prefix))
    for pair in part.periodic:
        for name in (pair.first, pair.second):
            if name not in info.patches:
                found.append(_blocking(
                    "PART_PERIODIC_UNKNOWN", f"{part.name}: periodic patch '{name}' is not a "
                    "patch of the file.", "Read the parts again and confirm what is found.",
                    field=f"{field}.periodic", part=part.prefix, patch=name))
    return info, found


def _parts_issues(config: MachineProjectConfig, mesh: MachineParts,
                  limits: MshThresholds) -> list[Issue]:
    found: list[Issue] = []
    readable = True
    for index, part in enumerate(mesh.parts):
        info, issues = _part_issues(index, part, limits)
        found += issues
        readable = readable and info.readable
    counts = Counter(p.prefix for p in mesh.parts)
    for prefix in sorted(p for p, c in counts.items() if c > 1):
        found.append(_blocking(
            "PART_PREFIX_DUPLICATE", f"Two parts have the prefix '{prefix}'.",
            "Give each part its own short name: it keeps their patches and zones apart.",
            field="imported_mesh.parts", prefix=prefix))
    paths = Counter(str(p.source_path) for p in mesh.parts)
    for path in sorted(p for p, c in paths.items() if c > 1):
        found.append(_blocking(
            "PART_FILE_DUPLICATE", f"{Path(path).name} is used by two parts.",
            "Remove one of them.", field="imported_mesh.parts", path=path))
    if not readable or any(i.code in ("PART_PREFIX_DUPLICATE", "PART_PERIODIC_UNKNOWN")
                           for i in found):
        return found
    info = machine_info(mesh)
    found += _zone_issues(config, info)
    found += _pair_issues(config, info)
    found += _patch_issues(config, info)
    return found


def check_imported_mesh(config: MachineProjectConfig,
                        thresholds: MshThresholds | None = None) -> tuple[Issue, ...]:
    """Every check of an imported-mesh project that needs no OpenFOAM."""
    mesh = config.imported_mesh
    assert mesh is not None
    limits = thresholds or MshThresholds()
    found = _stl_issues(config)
    if config.machine in NOT_FOR_IMPORT:
        found.append(_blocking(
            "MSH_MACHINE_NOT_SUPPORTED", f"A {config.machine.value} project cannot use an "
            "imported mesh.", "To use an imported VAWT mesh, create a Custom project.",
            field="machine"))
    if isinstance(mesh, MachineParts):
        return (*found, *_parts_issues(config, mesh, limits))
    info, digest = read_mesh_file(mesh.source_path)
    found.extend(info.issues)
    if info.format is None or any(i.is_stopping for i in info.issues):
        return tuple(found)
    if digest != mesh.sha256:
        found.append(_blocking(
            "MSH_SOURCE_CHANGED", f"{mesh.source_path.name} has changed since it was chosen.",
            "Choose the file again and confirm its units against the new bounding box.",
            "The units were confirmed against the box of the file as it was; the converted "
            "mesh is never reused for a changed file.", field="imported_mesh.sha256",
            expected=mesh.sha256, found=digest))
    found += _zone_issues(config, info)
    found += _pair_issues(config, info)
    found += _patch_issues(config, info)
    found += _units_issues(mesh.source_units, info, limits)
    return tuple(found)
