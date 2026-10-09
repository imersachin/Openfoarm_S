"""Result checks on a finished domain mesh (docs/rotating_machinery.md section 11;
section 18, decision 2).

G0 R2 meshed two wrong cases that exited 0 and passed checkMesh: a binary
STL lost its inlet and outlet, and an open surface kept the background box.
These checks read the mesh's boundary file and catch both.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.case_generator import DomainCase
from machines.domains import BACKGROUND_PATCH
from visualization.foam_reader import FoamReadError, Patch, read_boundary

BOUNDARY = "constant/polyMesh/boundary"


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
