"""What the Python checks report for the E6 surfaces (compare with surfaceCheck).

    python -m tests.fixtures.vawt.v0.E6_surface_check.compare_python <dir> <out.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from geometry.importer import import_stl
from geometry.metrics import body_orientations
from geometry.validator import TrimeshGeometryValidator


def main() -> None:
    directory, out = Path(sys.argv[1]), Path(sys.argv[2])
    report = {}
    for name in ("rotor.stl", "rotor_open.stl", "rotor_inverted.stl"):
        mesh = import_stl(directory / name).mesh
        assert mesh is not None
        validation = TrimeshGeometryValidator().validate_mesh(mesh, Path(name))
        report[name] = {
            "issues": sorted(f"{i.severity.value} {i.code}" for i in validation.issues),
            "bodies": [
                {"index": b.index, "faces": b.faces, "closed": b.closed,
                 "inside_out": b.inside_out}
                for b in body_orientations(mesh)
            ],
        }
    out.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(out.read_text("utf-8"))


if __name__ == "__main__":
    main()
