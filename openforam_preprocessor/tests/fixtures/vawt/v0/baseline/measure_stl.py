"""V0 baseline: geometry artifact cost (spec section 3.3, integration point 7).

Times, for a binary source STL of ~100k and ~1M triangles: import of the
source, transformation, ASCII artifact encoding and writing, re-reading the
artifact (the pipeline re-validates from disk) and hashing both files.
Median of REPEATS runs. Run from the repository root:

    python -m tests.fixtures.vawt.v0.baseline.measure_stl <out.json>
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from core.artifacts.hashing import sha256_file
from core.config.models import GeometryConfig
from geometry.importer import import_stl
from geometry.transformer import GeometryTransform, stl_ascii_bytes, write_stl_artifact
from tests.fixtures.vawt.v0.baseline.dense_rotor import dense_rotor

TARGETS = (100_000, 1_000_000)
REPEATS = 3


def timed(fn, *args):  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    value = fn(*args)
    return time.perf_counter() - start, value


def measure(target: int, directory: Path) -> dict[str, object]:
    source = directory / f"rotor_{target}.stl"
    mesh = dense_rotor(target)
    source.write_bytes(mesh.export(file_type="stl"))  # binary, as a user upload
    artifact = directory / f"artifact_{target}.stl"
    transform = GeometryTransform.from_config(
        GeometryConfig(source_path=source, source_units="mm", patch_name="rotor")
    )

    samples: dict[str, list[float]] = {}
    for _ in range(REPEATS):
        artifact.unlink(missing_ok=True)
        steps = {}
        steps["import_source_binary"], imported = timed(import_stl, source)
        assert imported.mesh is not None
        steps["transform"], moved = timed(transform.apply_to_mesh, imported.mesh)
        steps["encode_ascii"], _ = timed(stl_ascii_bytes, moved, "rotor")
        steps["encode_and_write_artifact"], _ = timed(
            write_stl_artifact, moved, artifact, "rotor"
        )
        steps["write_if_unchanged_check"], _ = timed(
            write_stl_artifact, moved, artifact, "rotor"
        )
        steps["reimport_artifact_ascii"], again = timed(import_stl, artifact)
        assert again.mesh is not None
        steps["hash_source"], _ = timed(sha256_file, source)
        steps["hash_artifact"], _ = timed(sha256_file, artifact)
        for key, value in steps.items():
            samples.setdefault(key, []).append(value)

    return {
        "target_faces": target,
        "faces": int(len(mesh.faces)),
        "source_binary_bytes": source.stat().st_size,
        "artifact_ascii_bytes": artifact.stat().st_size,
        "seconds_median": {k: round(statistics.median(v), 4) for k, v in samples.items()},
        "seconds_all": {k: [round(x, 4) for x in v] for k, v in samples.items()},
    }


def main() -> None:
    out = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        results = [measure(target, Path(tmp)) for target in TARGETS]
    out.write_text(json.dumps({
        "python": platform.python_version(),
        "machine": platform.platform(),
        "repeats": REPEATS,
        "results": results,
    }, indent=2) + "\n", "utf-8")
    print(out.read_text("utf-8"))


if __name__ == "__main__":
    main()
