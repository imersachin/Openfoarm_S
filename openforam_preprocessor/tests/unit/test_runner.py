from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from core.issues import IssueCategory, IssueStage
from openfoam.runner import OpenFOAMRunner, RunStatus

PY = sys.executable


def run(runner: OpenFOAMRunner, code: str, tmp_path: Path, **kwargs):
    return asyncio.run(
        runner.run((PY, "-c", code), case_root=tmp_path, log_name="cmd.log", **kwargs)
    )


def test_success_captures_stdout_and_stderr_separately(tmp_path: Path) -> None:
    code = "import sys; print('out-line'); print('err-line', file=sys.stderr)"

    result = run(OpenFOAMRunner(), code, tmp_path)

    assert result.status is RunStatus.SUCCESS
    assert result.return_code == 0
    assert result.tail == ("out-line",)
    assert result.stderr_tail == ("err-line",)
    assert Path(result.log_path).read_text(encoding="utf-8").strip() == "out-line"
    assert Path(result.stderr_log_path).read_text(encoding="utf-8").strip() == "err-line"
    assert result.duration_seconds >= 0
    assert result.working_directory == str(tmp_path)
    assert result.issues == ()


def test_run_ids_are_unique(tmp_path: Path) -> None:
    runner = OpenFOAMRunner()
    assert run(runner, "pass", tmp_path).run_id != run(runner, "pass", tmp_path).run_id


def test_non_zero_exit_is_failed_with_execution_issue(tmp_path: Path) -> None:
    result = run(OpenFOAMRunner(), "import sys; sys.exit(3)", tmp_path, stage=IssueStage.BLOCK_MESH)

    assert result.status is RunStatus.FAILED
    assert result.return_code == 3
    issue = result.issues[0]
    assert issue.code == "COMMAND_FAILED"
    assert issue.category is IssueCategory.EXECUTION
    assert issue.stage is IssueStage.BLOCK_MESH
    assert issue.log_reference == result.log_path


def test_missing_executable_is_environment_issue(tmp_path: Path) -> None:
    result = asyncio.run(OpenFOAMRunner().run(
        ("definitely-not-an-openfoam-binary",), case_root=tmp_path, log_name="x.log"
    ))

    assert result.status is RunStatus.FAILED
    assert result.return_code is None
    assert result.issues[0].code == "EXECUTABLE_NOT_FOUND"
    assert result.issues[0].category is IssueCategory.OPENFOAM_ENVIRONMENT


def test_timeout_terminates_process(tmp_path: Path) -> None:
    result = run(
        OpenFOAMRunner(terminate_grace_seconds=5),
        "import time; time.sleep(30)",
        tmp_path,
        timeout_seconds=0.5,
    )

    assert result.status is RunStatus.TIMEOUT
    assert result.issues[0].code == "COMMAND_TIMEOUT"
    assert result.issues[0].category is IssueCategory.TIMEOUT_CANCELLATION
    assert result.duration_seconds < 20


def test_default_timeout_is_configurable(tmp_path: Path) -> None:
    runner = OpenFOAMRunner(default_timeout_seconds=0.5)

    result = run(runner, "import time; time.sleep(30)", tmp_path)

    assert result.status is RunStatus.TIMEOUT
    assert result.timeout_seconds == 0.5


def test_cancel_event_cancels_running_command(tmp_path: Path) -> None:
    async def scenario():
        cancel = asyncio.Event()
        task = asyncio.create_task(OpenFOAMRunner().run(
            (PY, "-c", "import time; time.sleep(30)"),
            case_root=tmp_path,
            log_name="c.log",
            cancel_event=cancel,
        ))
        await asyncio.sleep(0.5)
        cancel.set()
        return await task

    result = asyncio.run(scenario())

    assert result.status is RunStatus.CANCELLED
    assert result.issues[0].code == "COMMAND_CANCELLED"


def test_large_output_keeps_bounded_tail_and_full_log(tmp_path: Path) -> None:
    result = run(OpenFOAMRunner(tail_lines=10), "for i in range(5000): print(i)", tmp_path)

    assert result.status is RunStatus.SUCCESS
    assert result.tail == tuple(str(i) for i in range(4990, 5000))
    assert len(Path(result.log_path).read_text(encoding="utf-8").splitlines()) == 5000


def test_long_line_without_newline_is_captured(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.write('x' * 200_000)"

    result = run(OpenFOAMRunner(), code, tmp_path)

    assert result.status is RunStatus.SUCCESS
    assert len(Path(result.log_path).read_text(encoding="utf-8")) == 200_000


def test_meshing_pipeline_stops_at_first_failure(tmp_path: Path) -> None:
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()

    results = asyncio.run(OpenFOAMRunner().run_meshing_pipeline(
        case_root=tmp_path,
        extract_features_argv=None,
        env={"PATH": str(empty_bin)},
    ))

    assert len(results) == 1
    assert results[0].argv[0] == "blockMesh"
    assert results[0].issues[0].code == "EXECUTABLE_NOT_FOUND"
    assert results[0].issues[0].stage is IssueStage.BLOCK_MESH
