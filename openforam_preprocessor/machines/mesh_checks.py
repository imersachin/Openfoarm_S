"""Result checks on a finished domain mesh (docs/rotating_machinery.md section 11;
section 18, decision 2).

G0 R2 meshed two wrong cases that exited 0 and passed checkMesh: a binary
STL lost its inlet and outlet, and an open surface kept the background box.
These checks read the mesh's boundary file and catch both.

After assembly (G3) the merged mesh is also checked for its interface pairs,
cell zones, region count and AMI weights (section 11; decision 4 after G0).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.assembly import MachineCases
from machines.case_generator import DomainCase
from machines.domains import BACKGROUND_PATCH
from visualization.foam_reader import FoamReadError, Patch, read_boundary

BOUNDARY = "constant/polyMesh/boundary"
CELL_ZONES = "constant/polyMesh/cellZones"
# Default AMI sum(weights) range (section 18, decision 4): consistency flags.
AMI_WEIGHTS_RANGE = (0.85, 1.5)


def _issue(severity: IssueSeverity, code: str, message: str, action: str,
           **details: object) -> Issue:
    return Issue(category=IssueCategory.MESHING, severity=severity,
                 stage=IssueStage.MESH_VALIDATION, code=code, message=message,
                 suggested_action=action, details=dict(details))


def check_domain_mesh(case: DomainCase, patches: Sequence[Patch]) -> tuple[Issue, ...]:
    """The mesh of `case` against what it was generated to have."""
    found = {p.name: p for p in patches}
    issues: list[Issue] = []
    for name, kind in case.expected.items():
        patch = found.get(name)
        if patch is None:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_MISSING",
                f"The {case.name} mesh has no patch '{name}'.",
                "Check the surface's region names (binary STL stores none).",
                case=case.name, patch=name, found=sorted(found)))
        elif patch.n_faces == 0:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_EMPTY",
                f"Patch '{name}' of the {case.name} mesh has no faces.",
                "Check that the region is part of the closed surface around the mesh point.",
                case=case.name, patch=name))
        elif patch.patch_type != kind:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_TYPE_WRONG",
                f"Patch '{name}' of the {case.name} mesh is '{patch.patch_type}', "
                f"expected '{kind}'.", "Regenerate the case.",
                case=case.name, patch=name, found_type=patch.patch_type, expected_type=kind))
    for name in case.unplaced:
        if name not in found:
            issues.append(_issue(
                IssueSeverity.ERROR, "DOMAIN_PATCH_MISSING",
                f"Patch '{name}' is in no mesh: its region was not found in any imported "
                "surface.", "Check the region names (binary STL stores none).",
                case=case.name, patch=name, found=sorted(found)))
    background = found.get(BACKGROUND_PATCH)
    if case.cut and background is not None and background.n_faces > 0:
        issues.append(_issue(
            IssueSeverity.ERROR, "WRONG_REGION_KEPT",
            f"The {case.name} mesh still has {background.n_faces} faces on the background "
            "box: snappyHexMesh kept the region outside the surface, or meshed through a "
            "gap in it.", "Close the surface, and check the mesh point is inside it.",
            case=case.name, background_faces=background.n_faces))
    allowed = {*case.expected, *case.other, BACKGROUND_PATCH}
    unexpected = sorted(name for name, p in found.items() if name not in allowed and p.n_faces)
    if unexpected:
        issues.append(_issue(
            IssueSeverity.WARNING, "DOMAIN_PATCH_UNEXPECTED",
            f"The {case.name} mesh has patches that were not generated: "
            f"{', '.join(unexpected)}.", "Check the surface's region names.",
            case=case.name, patches=unexpected))
    return tuple(issues)


def check_domain_case(case_dir: Path, case: DomainCase) -> tuple[Issue, ...]:
    """check_domain_mesh on the mesh written in case_dir."""
    try:
        patches = read_boundary(case_dir / BOUNDARY)
    except FoamReadError as exc:
        return (_issue(IssueSeverity.ERROR, "DOMAIN_MESH_UNREADABLE",
                       f"The {case.name} mesh cannot be read: {exc}",
                       "Run the meshing again.", case=case.name),)
    return check_domain_mesh(case, patches)


# --- the assembled mesh (G3) ---------------------------------------------------------------

_REGIONS = re.compile(r"Number of regions:\s*(\d+)")
_AMI_PAIR = re.compile(r"AMI: Creating AMI for source:(\S+) and target:(\S+)")
_AMI_SUM = re.compile(r"AMI: Patch (source|target) sum\(weights\) min:(\S+) max:(\S+)")


@dataclass(frozen=True)
class AmiWeights:
    source: str
    target: str
    minimum: float  # over source and target sums
    maximum: float


def parse_ami_weights(text: str) -> tuple[AmiWeights, ...]:
    """The first sum(weights) report of every AMI pair in a log."""
    found: dict[tuple[str, str], list[float]] = {}
    current: tuple[str, str] | None = None
    for line in text.splitlines():
        pair = _AMI_PAIR.search(line)
        if pair:
            current = (pair[1], pair[2])
            if current in found:
                current = None  # a later rebuild of a pair already read
            else:
                found[current] = []
            continue
        sums = _AMI_SUM.search(line)
        if sums and current is not None:
            found[current] += [float(sums[2]), float(sums[3])]
    return tuple(AmiWeights(s, t, min(v), max(v)) for (s, t), v in found.items() if v)


def cell_zone_names(case_dir: Path) -> set[str]:
    """Names in constant/polyMesh/cellZones (empty if absent)."""
    path = case_dir / CELL_ZONES
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*\n\s*\{", text, re.MULTILINE))
    return names - {"FoamFile", "meta"}  # v2512 headers carry a meta block


def check_assembly(cases: MachineCases, merged_dir: Path, check_mesh_log: str,
                   ami_log: str | None,
                   ami_range: tuple[float, float] = AMI_WEIGHTS_RANGE) -> tuple[Issue, ...]:
    """The merged mesh: patches and interface pairs (expected, with faces and
    their types), the background gone, one cell zone per rotating zone, one
    region per separately meshed part, and the AMI weights (None: not run)."""
    merged = cases.merged
    issues = list(check_domain_case(merged_dir, merged))
    zones = cell_zone_names(merged_dir)
    for case in cases.zones:
        zone = case.name.removeprefix("zone_")
        if zone not in zones:
            issues.append(_issue(
                IssueSeverity.ERROR, "CELL_ZONE_MISSING",
                f"The merged mesh has no cell zone '{zone}'.",
                "Inspect the topoSet and mergeMeshes logs of this zone.", zone=zone))
    expected = len(cases.domain) + len(cases.zones)
    found = _REGIONS.search(check_mesh_log)
    if found is None:
        issues.append(_issue(
            IssueSeverity.WARNING, "REGION_COUNT_UNKNOWN",
            "checkMesh did not report the number of regions.",
            "Inspect the raw checkMesh log.", expected=expected))
    elif int(found[1]) != expected:
        issues.append(_issue(
            IssueSeverity.ERROR, "REGION_COUNT_UNEXPECTED",
            f"The merged mesh has {found[1]} disconnected region(s); its {expected} separately "
            "meshed parts, joined only by AMI, make one region each.",
            "Inspect the raw checkMesh log: part of the domain is cut off, or the meshes "
            "were not assembled as intended.",
            found=int(found[1]), expected=expected, validity="INVALID"))
    if ami_log is not None:
        weights = {(w.source, w.target): w for w in parse_ami_weights(ami_log)}
        low, high = ami_range
        for face in cases.interfaces:
            w = (weights.get((face.stationary, face.rotating))
                 or weights.get((face.rotating, face.stationary)))
            if w is None:
                issues.append(_issue(
                    IssueSeverity.WARNING, "AMI_WEIGHTS_UNKNOWN",
                    f"No AMI weights were reported for {face.stationary} / {face.rotating}.",
                    "Inspect the postProcess log.", pair=[face.stationary, face.rotating]))
            elif w.minimum < low or w.maximum > high:
                issues.append(_issue(
                    IssueSeverity.WARNING, "AMI_WEIGHTS_OUT_OF_RANGE",
                    f"AMI sum(weights) of {face.stationary} / {face.rotating} spans "
                    f"{w.minimum:.3g}-{w.maximum:.3g}, outside {low:g}-{high:g}.",
                    "Make the two sides of the interface similarly fine, and check no body "
                    "crosses it.", pair=[face.stationary, face.rotating],
                    minimum=w.minimum, maximum=w.maximum, range=[low, high]))
    return tuple(issues)