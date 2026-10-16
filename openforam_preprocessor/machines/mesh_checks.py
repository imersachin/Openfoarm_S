"""Result checks on a finished domain mesh (docs/rotating_machinery.md section 11;
section 18, decision 2).

G0 R2 meshed two wrong cases that exited 0 and passed checkMesh: a binary
STL lost its inlet and outlet, and an open surface kept the background box.
These checks read the mesh's boundary file and catch both.

After assembly (G3) the merged mesh is also checked for its interface pairs,
cell zones, region count and AMI weights (section 11; decision 4 after G0),
and from G5 for the pairs of the joints too.

The checks themselves need only the mesh folder (machines.mesh_folder, G7):
here the generated cases give them what to expect.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from core.issues import Issue
from machines.assembly import MachineCases
from machines.case_generator import DomainCase
from machines.mesh_folder import (
    AMI_WEIGHTS_RANGE,
    AmiWeights,
    MeshExpectation,
    cell_zone_names,
    check_ami_weights,
    check_cell_zones,
    check_patches,
    check_regions,
    parse_ami_weights,
    read_patches,
)
from visualization.foam_reader import Patch

BOUNDARY = "constant/polyMesh/boundary"
CELL_ZONES = "constant/polyMesh/cellZones"

__all__ = ["AMI_WEIGHTS_RANGE", "BOUNDARY", "CELL_ZONES", "AmiWeights", "cell_zone_names",
           "check_assembly", "check_domain_case", "check_domain_mesh", "expectation_of",
           "parse_ami_weights"]


def expectation_of(case: DomainCase) -> MeshExpectation:
    """What the mesh of a generated case should have."""
    return MeshExpectation(case.name, dict(case.expected), tuple(case.unplaced),
                           tuple(case.other), case.cut)


def check_domain_mesh(case: DomainCase, patches: Sequence[Patch]) -> tuple[Issue, ...]:
    """The mesh of `case` against what it was generated to have."""
    return check_patches(patches, expectation_of(case))


def check_domain_case(case_dir: Path, case: DomainCase) -> tuple[Issue, ...]:
    """check_domain_mesh on the mesh written in case_dir."""
    patches, unreadable = read_patches(case_dir / "constant" / "polyMesh", case.name)
    if unreadable is not None:
        return (unreadable,)
    return check_domain_mesh(case, patches)


def check_assembly(cases: MachineCases, merged_dir: Path, check_mesh_log: str,
                   ami_log: str | None,
                   ami_range: tuple[float, float] = AMI_WEIGHTS_RANGE) -> tuple[Issue, ...]:
    """The merged mesh: patches and interface pairs (expected, with faces and
    their types), the background gone, one cell zone per rotating zone, one
    region per separately meshed part, and the AMI weights (None: not run)."""
    issues = list(check_domain_case(merged_dir, cases.merged))
    zones = [case.name.removeprefix("zone_") for case in cases.zones]
    issues += check_cell_zones(merged_dir, zones, "merged")
    issues += check_regions(check_mesh_log, len(cases.domain) + len(cases.zones), "merged")
    if ami_log is not None:  # zone interfaces and joints
        issues += check_ami_weights(ami_log, [(p.first, p.second) for p in cases.pairs],
                                    ami_range)
    return tuple(issues)
