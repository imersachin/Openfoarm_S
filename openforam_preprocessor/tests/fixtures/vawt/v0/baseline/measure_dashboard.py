"""V0 baseline: server time of the current dashboard (spec section 14.2).

Drives app/dashboard.py headlessly with streamlit's AppTest and a saved
project whose geometry is a ~100k-triangle rotor. For each interaction it
records the wall time of the script rerun and how many STL parses
(trimesh.load_mesh calls) happened. Median of REPEATS runs. Run from the
repository root:

    python -m tests.fixtures.vawt.v0.baseline.measure_dashboard <out.json>
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import trimesh
from streamlit.testing.v1 import AppTest

from core.config.models import (
    BackgroundMeshConfig,
    Bounds,
    GeometryConfig,
    MeshConfig,
    ProjectConfig,
    Vector3,
)
from core.services import ProjectService
from tests.fixtures.vawt.v0.baseline.dense_rotor import dense_rotor

DASHBOARD = Path(__file__).resolve().parents[5] / "app" / "dashboard.py"
TARGET_FACES = 100_000
REPEATS = 3

_parses = 0
_original_load_mesh = trimesh.load_mesh


def _counting_load_mesh(*args: Any, **kwargs: Any) -> Any:
    global _parses
    _parses += 1
    return _original_load_mesh(*args, **kwargs)


trimesh.load_mesh = _counting_load_mesh  # importer calls trimesh.load_mesh


def saved_project(directory: Path) -> Path:
    source = directory / "rotor.stl"
    source.write_bytes(dense_rotor(TARGET_FACES).export(file_type="stl"))
    case = directory / "case"
    config = ProjectConfig(
        project_name="Baseline",
        openfoam_profile="openfoam_com",
        geometry=GeometryConfig(source_path=source, source_units="m", patch_name="rotor"),
        mesh=MeshConfig(
            background=BackgroundMeshConfig(
                domain=Bounds(minimum=Vector3(x=-2, y=-2, z=-2),
                              maximum=Vector3(x=2, y=2, z=2)),
                base_cell_size=0.1,
            ),
            location_in_mesh=Vector3(x=1.5, y=0.1, z=0.1),
        ),
    )
    ProjectService(case).save(config.model_dump(mode="json"))
    return case


def step(app: AppTest, action: Callable[[AppTest], Any]) -> tuple[float, int]:
    global _parses
    before = _parses
    start = time.perf_counter()
    action(app)
    elapsed = time.perf_counter() - start
    assert not app.exception, [e.value for e in app.exception]
    return elapsed, _parses - before


def button(label: str) -> Callable[[AppTest], Any]:
    return lambda app: next(b for b in app.button if b.label == label).click().run()


def number(label: str, value: float) -> Callable[[AppTest], Any]:
    return lambda app: next(n for n in app.number_input if n.label == label).set_value(
        value).run()


def text(label: str, value: str) -> Callable[[AppTest], Any]:
    return lambda app: next(t for t in app.text_input if t.label == label).set_value(
        value).run()


def one_session(case: Path, repeat: int) -> list[tuple[str, float, int]]:
    app = AppTest.from_file(str(DASHBOARD), default_timeout=600)
    rows = [("first render (empty project path)", *step(app, lambda a: a.run()))]
    rows.append(("open saved project", *step(
        app, lambda a: a.sidebar.text_input[0].set_value(str(case)).run())))
    rows.append(("edit acceptance limit (one field)", *step(
        app, number("Max non-orthogonality (°)", 60.0 + repeat))))
    rows.append(("edit project name (one field)", *step(
        app, text("Project name", f"Baseline{repeat}"))))
    rows.append(("prepare case (geometry + dictionaries)", *step(
        app, button("Prepare case (geometry + dictionaries)"))))
    rows.append(("show geometry view", *step(app, button("Show geometry and domain"))))
    rows.append(("edit one field while geometry view is open", *step(
        app, number("Max non-orthogonality (°)", 61.0 + repeat))))
    return rows


def main() -> None:
    out = Path(sys.argv[1])
    samples: dict[str, list[tuple[float, int]]] = {}
    faces = len(dense_rotor(TARGET_FACES).faces)
    for repeat in range(REPEATS):
        with tempfile.TemporaryDirectory() as tmp:
            case = saved_project(Path(tmp))
            for name, seconds, parses in one_session(case, repeat):
                samples.setdefault(name, []).append((seconds, parses))
    out.write_text(json.dumps({
        "python": platform.python_version(),
        "machine": platform.platform(),
        "rotor_faces": faces,
        "repeats": REPEATS,
        "note": "Wall time of one AppTest script rerun (server side, no browser). "
        "All 11 tabs execute on every rerun (st.tabs), so switching tabs is not a "
        "separate server interaction.",
        "interactions": [
            {
                "interaction": name,
                "seconds_median": round(statistics.median(s for s, _ in values), 4),
                "seconds_all": [round(s, 4) for s, _ in values],
                "stl_parses": sorted({p for _, p in values}),
            }
            for name, values in samples.items()
        ],
    }, indent=2) + "\n", "utf-8")
    print(out.read_text("utf-8"))


if __name__ == "__main__":
    main()
