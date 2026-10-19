"""Fingerprints of STL projects as main wrote and meshed them before M1-MSH.

    python tests/fixtures/msh/m1/stl_baseline.py <work_dir> > stl_baseline.json

Run on main (cb07c48, the M1-MSH branch point) to write stl_baseline.json;
test_m1_config_validation.py recomputes the same on the branch. For each fixture
project: the saved file (model_dump_json, with the STL directory replaced by
<dir>) and every meshing input the pipeline caches (each case's dictionaries
and surfaces, the patch types the assembly sets, the profile), as in
test_machine_field_coverage.py. Equal fingerprints: an existing project is
saved and meshed byte-identically after the imported_mesh section was added
(owner answer 3).
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from machines.assembly import MachineCaseGenerator
from machines.config import MachineProjectConfig
from machines.domains import read_imported
from machines.validation import load_bodies
from tests.fixtures.machines import configs

BUILDERS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "hawt": configs.hawt,
    "pole_split": configs.pole,
    "pole_annular": lambda d: configs.pole(d, motion="STATIONARY", hole_diameter=0.1),
    "francis": configs.francis,
    "duct": configs.duct,
}


def fingerprints(work: Path) -> dict[str, dict[str, str]]:
    found = {}
    for name, build in BUILDERS.items():
        directory = work / name
        config = MachineProjectConfig.model_validate(build(directory))
        saved = config.model_dump_json(indent=2).replace(str(directory), "<dir>")
        meshes, _ = load_bodies(config)
        cases = MachineCaseGenerator().render(config, read_imported(config), meshes,
                                              typed=False)
        digest = hashlib.sha256()
        for case in cases.all:
            for relative in sorted(case.dictionaries):
                digest.update(f"{case.name}/{relative}\0{case.dictionaries[relative]}\0".encode())
            for relative in sorted(case.surfaces):
                digest.update(f"{case.name}/{relative}\0".encode() + case.surfaces[relative])
        digest.update(repr(sorted(cases.retype.items())).encode())
        digest.update(config.openfoam_profile.value.encode())
        found[name] = {"saved": hashlib.sha256(saved.encode()).hexdigest(),
                       "meshing_inputs": digest.hexdigest()}
    return found


if __name__ == "__main__":
    print(json.dumps(fingerprints(Path(sys.argv[1])), indent=2, sort_keys=True))
