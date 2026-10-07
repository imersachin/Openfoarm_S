"""M9: failure paths, cancellation, run lock, run records, versions, reproducibility."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tomllib
from pathlib import Path

import psutil
import pytest

from core.artifacts import MANIFEST_PATH
from core.config.models import ProjectConfig
from core.config.validation import validate_project_config
from core.version import APP_VERSION, CONFIG_SCHEMA_VERSION
from core.workflow.run_lock import LOCK_PATH, RunLock
from core.workflow.run_records import RUNS_DIR, list_run_records, write_run_record
from openfoam.runner import RunStatus
from tests.fakes import FakeOpenFOAMRunner, fake_pipeline, openfoam_env
from tests.helpers import build_config

REPO = Path(__file__).parents[2]


def mesh(tmp_path: Path, config: ProjectConfig, runner: FakeOpenFOAMRunner, **kwargs):
    pipeline = fake_pipeline(runner, openfoam_env(tmp_path))
    return asyncio.run(pipeline.generate_mesh(tmp_path / "case", config, **kwargs))


def manifest_records(tmp_path: Path) -> dict:
    path = tmp_path / "case" / MANIFEST_PATH
    return json.loads(path.read_text("utf-8"))["records"] if path.is_file() else {}


# --- failure matrix ----------------------------------------------------------

@pytest.mark.parametrize("command", ["surfaceFeatureExtract", "blockMesh", "snappyHexMesh",
                                     "checkMesh"])
@pytest.mark.parametrize(
    ("mode", "code"),
    [("fail", "COMMAND_FAILED"), (RunStatus.TIMEOUT, "COMMAND_TIMEOUT"),
     (RunStatus.CANCELLED, "COMMAND_CANCELLED")],
)
def test_every_command_failure_stops_and_recovers(
    tmp_path: Path, cube_stl: Path, command: str, mode: object, code: str
) -> None:
    config = build_config(cube_stl, extract_features=True)
    broken = (FakeOpenFOAMRunner(fail=command) if mode == "fail"
              else FakeOpenFOAMRunner(outcome={command: mode}))  # type: ignore[dict-item]

    result = mesh(tmp_path, config, broken)

    assert not result.succeeded
    assert broken.calls[-1] == command  # nothing ran after the failure
    assert result.issues[-1].code == code
    operation = {"surfaceFeatureExtract": "extract_features", "blockMesh": "generate_mesh",
                 "snappyHexMesh": "generate_mesh", "checkMesh": "check_mesh"}[command]
    assert operation not in manifest_records(tmp_path)  # never reusable
    assert result.run_record_path is not None

    healthy = FakeOpenFOAMRunner()
    recovered = mesh(tmp_path, config, healthy)

    assert recovered.succeeded
    assert command in healthy.calls


# --- cancellation ---------------------------------------------------------------

def test_cancel_between_steps_starts_nothing_further(tmp_path: Path, cube_stl: Path) -> None:
    event = asyncio.Event()
    runner = FakeOpenFOAMRunner(cancel_after=("blockMesh", event))

    result = mesh(tmp_path, build_config(cube_stl), runner, cancel_event=event)

    assert runner.calls == ["blockMesh"]
    assert not result.succeeded
    assert result.issues[-1].code == "RUN_CANCELLED"
    assert "generate_mesh" not in manifest_records(tmp_path)

    healthy = FakeOpenFOAMRunner()
    assert mesh(tmp_path, build_config(cube_stl), healthy).succeeded
    assert healthy.calls == ["blockMesh", "snappyHexMesh", "checkMesh"]


def test_already_cancelled_run_reuses_nothing_and_runs_nothing(
    tmp_path: Path, cube_stl: Path
) -> None:
    event = asyncio.Event()
    event.set()
    runner = FakeOpenFOAMRunner()

    result = mesh(tmp_path, build_config(cube_stl), runner, cancel_event=event)

    assert runner.calls == []
    assert result.issues[-1].code == "RUN_CANCELLED"


# --- run lock --------------------------------------------------------------------

def write_lock(case: Path, pid: int, host: str) -> None:
    path = case / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "host": host, "started_at": "x"}), "utf-8")


def unused_pid() -> int:
    pid = max(psutil.pids()) + 10_000
    while psutil.pid_exists(pid):
        pid += 1
    return pid


def test_live_lock_blocks_second_run(tmp_path: Path, cube_stl: Path) -> None:
    write_lock(tmp_path / "case", os.getpid(), socket.gethostname())
    runner = FakeOpenFOAMRunner()

    result = mesh(tmp_path, build_config(cube_stl), runner)

    assert not result.succeeded
    assert runner.calls == []
    assert result.issues[0].code == "RUN_IN_PROGRESS"
    assert (tmp_path / "case" / LOCK_PATH).is_file()  # not ours: left alone


def test_stale_lock_from_dead_process_is_replaced(tmp_path: Path, cube_stl: Path) -> None:
    write_lock(tmp_path / "case", unused_pid(), socket.gethostname())

    result = mesh(tmp_path, build_config(cube_stl), FakeOpenFOAMRunner())

    assert result.succeeded
    assert not (tmp_path / "case" / LOCK_PATH).exists()


def test_lock_from_other_host_is_never_assumed_stale(tmp_path: Path) -> None:
    write_lock(tmp_path, unused_pid(), "some-other-host")

    issue = RunLock(tmp_path).acquire()

    assert issue is not None and issue.code == "RUN_IN_PROGRESS"


def test_lock_released_and_evidence_kept_on_unexpected_error(
    tmp_path: Path, cube_stl: Path
) -> None:
    class Exploding(FakeOpenFOAMRunner):
        async def run(self, argv, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        mesh(tmp_path, build_config(cube_stl), Exploding())

    assert not (tmp_path / "case" / LOCK_PATH).exists()
    latest = list_run_records(tmp_path / "case")[0]
    assert latest["succeeded"] is False
    assert "boom" in latest["message"]


# --- run records -------------------------------------------------------------------

def test_run_record_captures_what_ran_and_with_what(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl)
    result = mesh(tmp_path, config, FakeOpenFOAMRunner())

    record = json.loads(result.run_record_path.read_text("utf-8"))

    assert record["kind"] == "generate_mesh"
    assert record["succeeded"] is True
    assert record["app_version"] == APP_VERSION
    assert record["config_schema_version"] == CONFIG_SCHEMA_VERSION
    assert len(record["config_sha256"]) == 64
    assert record["environment"]["WM_PROJECT_VERSION"] == "v2312"
    assert [c["argv"][0] for c in record["commands"]] == [
        "blockMesh", "snappyHexMesh", "checkMesh",
    ]
    assert all(c["status"] == "SUCCESS" for c in record["commands"])
    assert "generate_mesh" in record["executed"]


def test_run_records_list_newest_first_and_prune(tmp_path: Path) -> None:
    for index in range(5):
        write_run_record(tmp_path, {"run_id": f"{index:032d}",
                                    "started_at": f"2026-01-0{index + 1}T00:00:00+00:00"},
                         keep=3)

    records = list_run_records(tmp_path)

    assert [r["started_at"][:10] for r in records] == ["2026-01-05", "2026-01-04",
                                                         "2026-01-03"]
    assert len(list((tmp_path / RUNS_DIR).glob("*.json"))) == 3


def test_prepare_case_also_leaves_a_record(tmp_path: Path, cube_stl: Path) -> None:
    pipeline = fake_pipeline(FakeOpenFOAMRunner(), openfoam_env(tmp_path))

    result = pipeline.prepare_case(tmp_path / "case", build_config(cube_stl))

    assert json.loads(result.run_record_path.read_text("utf-8"))["kind"] == "prepare_case"


# --- versions ------------------------------------------------------------------------

def test_app_version_matches_package_metadata() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    assert pyproject["project"]["version"] == APP_VERSION


def test_newer_schema_is_rejected(cube_stl: Path) -> None:
    raw = build_config(cube_stl).model_dump(mode="json")
    raw["schema_version"] = CONFIG_SCHEMA_VERSION + 1

    result = validate_project_config(raw)

    assert result.config is None
    assert "newer version" in result.issues[0].message


def test_version_1_project_is_migrated(cube_stl: Path) -> None:
    raw = build_config(cube_stl).model_dump(mode="json")
    raw["schema_version"] = 1
    del raw["mesh"]["snappy_quality"]
    raw["mesh"]["quality"]["max_non_orthogonality"] = 55.0

    config = validate_project_config(raw).config

    assert config is not None
    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert config.mesh.snappy_quality.max_non_orthogonality == 55.0


# --- reproducibility --------------------------------------------------------------------

def test_identical_inputs_reproduce_identical_case(tmp_path: Path, cube_stl: Path) -> None:
    config = build_config(cube_stl, extract_features=True)
    env = openfoam_env(tmp_path)
    cases = [tmp_path / "a", tmp_path / "b"]
    for case in cases:
        pipeline = fake_pipeline(FakeOpenFOAMRunner(), env)
        assert asyncio.run(pipeline.generate_mesh(case, config)).succeeded

    def snapshot(case: Path) -> dict[str, bytes]:
        files = [*sorted((case / "system").iterdir()),
                 *sorted((case / "constant" / "triSurface").iterdir()),
                 *sorted((case / "constant" / "polyMesh").glob("*"))]
        return {p.relative_to(case).as_posix(): p.read_bytes() for p in files if p.is_file()}

    assert snapshot(cases[0]) == snapshot(cases[1])

    def identities(case: Path) -> dict:
        records = json.loads((case / MANIFEST_PATH).read_text("utf-8"))["records"]
        return {name: (r["inputs"], r["outputs"]) for name, r in records.items()
                if name != "import_geometry"}  # its report embeds absolute paths

    assert identities(cases[0]) == identities(cases[1])


# --- packaging ---------------------------------------------------------------------------

def test_every_package_is_shipped() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    shipped = set(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])
    on_disk = {
        path.parent.name for path in REPO.glob("*/__init__.py") if path.parent.name != "tests"
    }

    assert shipped == on_disk
    assert set(pyproject["tool"]["mypy"]["packages"]) == on_disk
