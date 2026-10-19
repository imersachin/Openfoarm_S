"""Checks that need only a finished polyMesh (G7, owner addition B).

They take a mesh folder (a case directory holding constant/polyMesh, or the
polyMesh directory itself), the text of logs when a check needs one, and a
plain MeshExpectation; never the machine configuration. So they also run on
meshes this application did not build. machines.mesh_checks derives the
expectation from the generated cases and calls these, with the same
findings as before.

- patch presence, faces and OpenFOAM types (check_patches)
- the background block emptied by snappyHexMesh (in check_patches, `cut`)
- rotating-zone integrity: the cell zones exist (check_cell_zones)
- region count from checkMesh (check_regions)
- AMI sum(weights) from postProcess (check_ami_weights)
- AMI pairing from the boundary file alone: each cyclicAMI patch names a
  neighbour that exists, is cyclicAMI and names it back (check_ami_pairing)
- quality against acceptance limits from checkMesh output (quality_report)
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from core.config.models import MeshQualityLimits
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.domains import BACKGROUND_PATCH
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityReport, MeshQualityValidator
from visualization.foam_reader import FoamReadError, Patch, read_boundary

AMI_WEIGHTS_RANGE = (0.85, 1.5)  # default sum(weights) range (section 18, decision 4)
CHECK_MESH_LOGS = ("log.checkMesh", "checkMesh.log", "logs/checkMesh.log")

_REGIONS = re.compile(r"Number of regions:\s*(\d+)")
_AMI_PAIR = re.compile(r"AMI: Creating AMI for source:(\S+) and target:(\S+)")
_AMI_SUM = re.compile(r"AMI: Patch (source|target) sum\(weights\) min:(\S+) max:(\S+)")
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           **details: object) -> Issue:
    return Issue(category=IssueCategory.MESHING, severity=severity,
                 stage=IssueStage.MESH_VALIDATION, code=code, message=message,
                 suggested_action=action, details=dict(details))


@dataclass(frozen=True)
class MeshExpectation:
    """What a mesh should have. name appears in the messages ("merged")."""

    name: str
    patches: Mapping[str, str] = field(default_factory=dict)  # name -> OpenFOAM type
    unplaced: tuple[str, ...] = ()  # expected patches whose region no surface has
    other: tuple[str, ...] = ()  # allowed patches that may be empty
    cut: bool = False  # the background block must have been cut away
    cell_zones: tuple[str, ...] = ()
    regions: int | None = None
    ami_pairs: tuple[tuple[str, str], ...] = ()


def poly_mesh_dir(folder: Path) -> Path:
    """constant/polyMesh of a case directory, or the folder itself."""
    nested = folder / "constant" / "polyMesh"
    return nested if nested.is_dir() else folder


# --- patches ------------------------------------------------------------------------------

def check_patches(patches: Sequence[Patch], expected: MeshExpectation) -> tuple[Issue, ...]:
    """The boundary against what the mesh should have."""
    found = {p.name: p for p in patches}
    name = expected.name
    issues: list[Issue] = []
    for patch_name, kind in expected.patches.items():
        patch = found.get(patch_name)
        if patch is None:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_MISSING",
                f"The {name} mesh has no patch '{patch_name}'.",
                "Check the surface's region names (binary STL stores none).",
                case=name, patch=patch_name, found=sorted(found)))
        elif patch.n_faces == 0:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_EMPTY",
                f"Patch '{patch_name}' of the {name} mesh has no faces.",
                "Check that the region is part of the closed surface around the mesh point.",
                case=name, patch=patch_name))
        elif patch.patch_type != kind:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_TYPE_WRONG",
                f"Patch '{patch_name}' of the {name} mesh is '{patch.patch_type}', "
                f"expected '{kind}'.", "Regenerate the case.",
                case=name, patch=patch_name, found_type=patch.patch_type, expected_type=kind))
    for patch_name in expected.unplaced:
        if patch_name not in found:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_MISSING",
                f"Patch '{patch_name}' is in no mesh: its region was not found in any imported "
                "surface.", "Check the region names (binary STL stores none).",
                case=name, patch=patch_name, found=sorted(found)))
    background = found.get(BACKGROUND_PATCH)
    if expected.cut and background is not None and background.n_faces > 0:
        issues.append(_issue(
            IssueSeverity.ERROR, "WRONG_REGION_KEPT",
            f"The {name} mesh still has {background.n_faces} faces on the background "
            "box: snappyHexMesh kept the region outside the surface, or meshed through a "
            "gap in it.", "Close the surface, and check the mesh point is inside it.",
            case=name, background_faces=background.n_faces))
    allowed = {*expected.patches, *expected.other, BACKGROUND_PATCH}
    unexpected = sorted(n for n, p in found.items() if n not in allowed and p.n_faces)
    if unexpected:
        issues.append(_issue(
            IssueSeverity.WARNING, "DOMAIN_PATCH_UNEXPECTED",
            f"The {name} mesh has patches that were not generated: "
            f"{', '.join(unexpected)}.", "Check the surface's region names.",
            case=name, patches=unexpected))
    return tuple(issues)


def read_patches(folder: Path, name: str) -> tuple[tuple[Patch, ...], Issue | None]:
    try:
        return read_boundary(poly_mesh_dir(folder) / "boundary"), None
    except FoamReadError as exc:
        return (), _issue(IssueSeverity.ERROR, "DOMAIN_MESH_UNREADABLE",
                          f"The {name} mesh cannot be read: {exc}",
                          "Run the meshing again.", case=name)


# --- zones and regions -----------------------------------------------------------------------

def cell_zone_names(folder: Path) -> set[str]:
    """Names in the polyMesh's cellZones (empty if absent)."""
    path = poly_mesh_dir(folder) / "cellZones"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*\n\s*\{", text, re.MULTILINE))
    return names - {"FoamFile", "meta"}  # v2512 headers carry a meta block


def check_cell_zones(folder: Path, zones: Sequence[str], name: str) -> tuple[Issue, ...]:
    present = cell_zone_names(folder)
    return tuple(_issue(
        IssueSeverity.ERROR, "CELL_ZONE_MISSING", f"The {name} mesh has no cell zone '{zone}'.",
        "Inspect the topoSet and mergeMeshes logs of this zone.", zone=zone)
        for zone in zones if zone not in present)


def check_regions(check_mesh_log: str, expected: int, name: str) -> tuple[Issue, ...]:
    found = _REGIONS.search(check_mesh_log)
    if found is None:
        return (_issue(IssueSeverity.WARNING, "REGION_COUNT_UNKNOWN",
                       "checkMesh did not report the number of regions.",
                       "Inspect the raw checkMesh log.", expected=expected),)
    if int(found[1]) != expected:
        return (_issue(
            IssueSeverity.ERROR, "REGION_COUNT_UNEXPECTED",
            f"The {name} mesh has {found[1]} disconnected region(s); its {expected} separately "
            "meshed parts, joined only by AMI, make one region each.",
            "Inspect the raw checkMesh log: part of the domain is cut off, or the meshes "
            "were not assembled as intended.",
            found=int(found[1]), expected=expected, validity="INVALID"),)
    return ()


def region_count(check_mesh_log: str) -> int | None:
    found = _REGIONS.search(check_mesh_log)
    return int(found[1]) if found else None


# --- AMI ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AmiWeights:
    source: str
    target: str
    minimum: float  # over source and target sums
    maximum: float


def parse_ami_weights(text: str, every: bool = False) -> tuple[AmiWeights, ...]:
    """The first sum(weights) report of every AMI pair in a log; with `every`,
    the range over all its reports (each step of a turning mesh)."""
    found: dict[tuple[str, str], list[float]] = {}
    current: tuple[str, str] | None = None
    for line in text.splitlines():
        pair = _AMI_PAIR.search(line)
        if pair:
            current = (pair[1], pair[2])
            if current in found:
                if not every:
                    current = None  # a later rebuild of a pair already read
            else:
                found[current] = []
            continue
        sums = _AMI_SUM.search(line)
        if sums and current is not None:
            found[current] += [float(sums[2]), float(sums[3])]
    return tuple(AmiWeights(s, t, min(v), max(v)) for (s, t), v in found.items() if v)


def check_ami_weights(ami_log: str, pairs: Sequence[tuple[str, str]],
                      ami_range: tuple[float, float] = AMI_WEIGHTS_RANGE,
                      when: str | None = None, every: bool = False) -> tuple[Issue, ...]:
    """`when`: the rotor position the weights are for (shown with them), e.g.
    "at rest (0 deg)". `every`: as parse_ami_weights."""
    weights = {(w.source, w.target): w for w in parse_ami_weights(ami_log, every)}
    at = f" {when}" if when else ""
    where = {"when": when} if when else {}
    low, high = ami_range
    issues: list[Issue] = []
    for first, second in pairs:
        w = weights.get((first, second)) or weights.get((second, first))
        if w is None:
            issues.append(_issue(
                IssueSeverity.WARNING, "AMI_WEIGHTS_UNKNOWN",
                f"No AMI weights were reported for {first} / {second}{at}.",
                "Inspect the postProcess log.", pair=[first, second], **where))
        elif w.minimum < low or w.maximum > high:
            issues.append(_issue(
                IssueSeverity.WARNING, "AMI_WEIGHTS_OUT_OF_RANGE",
                f"AMI sum(weights) of {first} / {second}{at} spans "
                f"{w.minimum:.3g}-{w.maximum:.3g}, outside {low:g}-{high:g}.",
                "Make the two sides of the interface similarly fine, and check no body "
                "crosses it.", pair=[first, second],
                minimum=w.minimum, maximum=w.maximum, range=[low, high], **where))
    return tuple(issues)


def ami_neighbours(folder: Path) -> dict[str, str | None]:
    """cyclicAMI patch -> its neighbourPatch (None if it names none), from the
    boundary file alone."""
    path = poly_mesh_dir(folder) / "boundary"
    text = _COMMENTS.sub(" ", path.read_text(encoding="utf-8", errors="replace"))
    found: dict[str, str | None] = {}
    for name, body in re.findall(r"(\S+)\s*\{([^{}]*)\}", text):
        values = dict(re.findall(r"(\w+)\s+([^;]+);", body))
        if values.get("type", "").strip() == "cyclicAMI":
            neighbour = values.get("neighbourPatch")
            found[name] = neighbour.strip() if neighbour else None
    return found


def check_ami_pairing(folder: Path) -> tuple[Issue, ...]:
    """Every cyclicAMI patch names an existing cyclicAMI neighbour that names it
    back. Needs no expectation: a mesh-only check."""
    try:
        neighbours = ami_neighbours(folder)
    except OSError as exc:
        return (_issue(IssueSeverity.ERROR, "DOMAIN_MESH_UNREADABLE",
                       f"The boundary file cannot be read: {exc}", "Check the mesh folder."),)
    issues = []
    for patch, neighbour in sorted(neighbours.items()):
        if neighbour is None:
            issues.append(_issue(
                IssueSeverity.ERROR, "AMI_NEIGHBOUR_MISSING",
                f"The cyclicAMI patch '{patch}' names no neighbourPatch.",
                "Recreate the interface pair (createPatch).", patch=patch))
        elif neighbour not in neighbours:
            issues.append(_issue(
                IssueSeverity.ERROR, "AMI_NEIGHBOUR_MISSING",
                f"The cyclicAMI patch '{patch}' names '{neighbour}', which is not a "
                "cyclicAMI patch of this mesh.", "Recreate the interface pair (createPatch).",
                patch=patch, neighbour=neighbour))
        elif neighbours[neighbour] != patch:
            issues.append(_issue(
                IssueSeverity.ERROR, "AMI_NOT_RECIPROCAL",
                f"The cyclicAMI patch '{patch}' names '{neighbour}', which names "
                f"'{neighbours[neighbour]}' back.", "Recreate the interface pair (createPatch).",
                patch=patch, neighbour=neighbour))
    return tuple(issues)


def ami_pairs(folder: Path) -> tuple[tuple[str, str], ...]:
    """The reciprocal AMI pairs of a mesh, each once."""
    neighbours = ami_neighbours(folder)
    pairs = {(min(a, b), max(a, b)) for a, b in neighbours.items()
             if b is not None and neighbours.get(b) == a}
    return tuple(sorted(pairs))


# --- quality ---------------------------------------------------------------------------------

def quality_report(check_mesh_log: str, limits: MeshQualityLimits,
                   log_path: Path | None = None) -> MeshQualityReport:
    """checkMesh output against acceptance limits (validity and quality kept apart)."""
    metrics = CheckMeshParser().parse_text(check_mesh_log, log_path)
    return MeshQualityValidator().assess(metrics, limits)


# --- a whole folder ----------------------------------------------------------------------------

@dataclass(frozen=True)
class MeshFolderReport:
    folder: Path
    patches: tuple[Patch, ...]
    cell_zones: tuple[str, ...]
    ami_pairs: tuple[tuple[str, str], ...]
    regions: int | None
    quality: MeshQualityReport | None
    issues: tuple[Issue, ...]


def find_check_mesh_log(folder: Path) -> Path | None:
    for relative in CHECK_MESH_LOGS:
        path = folder / relative
        if path.is_file():
            return path
    return None


def check_mesh_folder(folder: Path, *, limits: MeshQualityLimits | None = None,
                      expected: MeshExpectation | None = None,
                      check_mesh_log: str | None = None) -> MeshFolderReport:
    """Every mesh-only check on a folder; nothing is run. Quality and region
    count need checkMesh output: given, or found as log.checkMesh in the folder.
    With an expectation, patches, cell zones and regions are checked against it."""
    name = expected.name if expected is not None else folder.name
    patches, unreadable = read_patches(folder, name)
    issues: list[Issue] = []
    if unreadable is not None:
        return MeshFolderReport(folder, (), (), (), None, None, (unreadable,))
    if expected is not None:
        issues += check_patches(patches, expected)
        issues += check_cell_zones(folder, expected.cell_zones, name)
    issues += check_ami_pairing(folder)
    log_path = None
    if check_mesh_log is None:
        log_path = find_check_mesh_log(folder)
        if log_path is not None:
            check_mesh_log = log_path.read_text(encoding="utf-8", errors="replace")
    quality = None
    regions = None
    if check_mesh_log is None:
        issues.append(_issue(
            IssueSeverity.INFO, "CHECK_MESH_OUTPUT_MISSING",
            "No checkMesh output was found, so quality and the region count were not "
            "assessed.", "Run checkMesh in the folder (log.checkMesh) and check again."))
    else:
        regions = region_count(check_mesh_log)
        if expected is not None and expected.regions is not None:
            issues += check_regions(check_mesh_log, expected.regions, name)
        if limits is not None:
            quality = quality_report(check_mesh_log, limits, log_path)
            issues += quality.issues
    return MeshFolderReport(folder, patches, tuple(sorted(cell_zone_names(folder))),
                            ami_pairs(folder), regions, quality, tuple(issues))
