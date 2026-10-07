from __future__ import annotations

import asyncio
import codecs
import shutil
import time
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from openfoam.commands import (
    MeshingStep,
    block_mesh_step,
    check_mesh_step,
    feature_extraction_step,
    snappy_step,
)


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class CommandResult:
    run_id: str
    argv: tuple[str, ...]
    working_directory: str
    status: RunStatus
    return_code: int | None
    started_at: str
    finished_at: str
    duration_seconds: float
    timeout_seconds: float | None
    log_path: str
    stderr_log_path: str
    tail: tuple[str, ...]
    stderr_tail: tuple[str, ...]
    issues: tuple[Issue, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status is RunStatus.SUCCESS


_READ_CHUNK = 64 * 1024


class OpenFOAMRunner:
    def __init__(
        self,
        tail_lines: int = 300,
        default_timeout_seconds: float | None = None,
        terminate_grace_seconds: float = 10.0,
    ) -> None:
        self.tail_lines = tail_lines
        self.default_timeout_seconds = default_timeout_seconds
        self.terminate_grace_seconds = terminate_grace_seconds

    async def run(
        self,
        argv: Sequence[str],
        *,
        case_root: Path,
        log_name: str,
        stage: IssueStage = IssueStage.OPENFOAM_EXECUTION,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> CommandResult:
        """Run one command, streaming stdout/stderr to separate logs.

        Never raises for process-level failures: a missing executable, non-zero
        exit, timeout, or cancel_event all produce a structured CommandResult.
        Task cancellation (asyncio.CancelledError) terminates the process and
        is re-raised.
        """
        if not argv:
            raise ValueError("Command cannot be empty.")

        timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        run_id = uuid.uuid4().hex
        logs_dir = case_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / log_name
        stderr_log_path = log_path.with_name(f"{log_path.stem}.stderr{log_path.suffix}")
        tail: deque[str] = deque(maxlen=self.tail_lines)
        stderr_tail: deque[str] = deque(maxlen=self.tail_lines)
        started_at = datetime.now(UTC)
        started_clock = time.monotonic()

        def result(status: RunStatus, return_code: int | None, *issues: Issue) -> CommandResult:
            return CommandResult(
                run_id=run_id,
                argv=tuple(argv),
                working_directory=str(case_root),
                status=status,
                return_code=return_code,
                started_at=started_at.isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
                duration_seconds=time.monotonic() - started_clock,
                timeout_seconds=timeout,
                log_path=str(log_path),
                stderr_log_path=str(stderr_log_path),
                tail=tuple(tail),
                stderr_tail=tuple(stderr_tail),
                issues=issues,
            )

        def issue(
            category: IssueCategory,
            code: str,
            message: str,
            explanation: str,
            action: str,
            **details: object,
        ) -> Issue:
            return Issue(
                category=category,
                severity=IssueSeverity.ERROR,
                stage=stage,
                code=code,
                message=message,
                explanation=explanation,
                suggested_action=action,
                log_reference=str(log_path),
                details={"argv": list(argv), "run_id": run_id, **details},
            )

        def missing_executable(reason: str) -> CommandResult:
            return result(RunStatus.FAILED, None, issue(
                IssueCategory.OPENFOAM_ENVIRONMENT,
                "EXECUTABLE_NOT_FOUND",
                f"OpenFOAM executable '{argv[0]}' could not be started.",
                reason,
                "Source the OpenFOAM environment or configure the OpenFOAM profile.",
            ))

        search_path = env.get("PATH") if env is not None else None
        if shutil.which(argv[0], path=search_path) is None:
            return missing_executable("The executable was not found on PATH.")

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=case_root,
                env=dict(env) if env is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return missing_executable(f"{type(exc).__name__}: {exc}")

        stdout, stderr = process.stdout, process.stderr
        assert stdout is not None and stderr is not None

        with (
            log_path.open("w", encoding="utf-8", newline="") as out_log,
            stderr_log_path.open("w", encoding="utf-8", newline="") as err_log,
        ):
            drains = asyncio.gather(
                self._drain(stdout, out_log, tail),
                self._drain(stderr, err_log, stderr_tail),
            )
            wait_task = asyncio.ensure_future(process.wait())
            cancel_task = (
                asyncio.ensure_future(cancel_event.wait()) if cancel_event is not None else None
            )
            watched = {wait_task} if cancel_task is None else {wait_task, cancel_task}

            try:
                done, _ = await asyncio.wait(
                    watched, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
                if wait_task not in done:
                    await self._terminate(process)
                await drains
            except asyncio.CancelledError:
                await self._terminate(process)
                drains.cancel()
                raise
            finally:
                if cancel_task is not None:
                    cancel_task.cancel()
                wait_task.cancel()

        return_code = process.returncode
        if wait_task in done:
            if return_code == 0:
                return result(RunStatus.SUCCESS, return_code)
            return result(RunStatus.FAILED, return_code, issue(
                IssueCategory.EXECUTION,
                "COMMAND_FAILED",
                f"{argv[0]} exited with code {return_code}.",
                "The OpenFOAM command reported an error.",
                "Inspect the command log and stderr log for the first error message.",
                return_code=return_code,
            ))
        if cancel_task is not None and cancel_task in done:
            return result(RunStatus.CANCELLED, return_code, issue(
                IssueCategory.TIMEOUT_CANCELLATION,
                "COMMAND_CANCELLED",
                f"{argv[0]} was cancelled.",
                "Execution was cancelled before the command finished.",
                "Re-run the operation when ready; partial outputs are not valid.",
            ))
        return result(RunStatus.TIMEOUT, return_code, issue(
            IssueCategory.TIMEOUT_CANCELLATION,
            "COMMAND_TIMEOUT",
            f"{argv[0]} exceeded the {timeout:g} s timeout and was terminated.",
            "The command did not finish within the configured time limit.",
            "Reduce mesh size/refinement or increase the timeout.",
            timeout_seconds=timeout,
        ))

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=self.terminate_grace_seconds)
        except ProcessLookupError:
            return
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    async def _drain(stream: asyncio.StreamReader, sink: TextIO, tail: deque[str]) -> None:
        # Chunked reads: bounded memory and no line-length limit for huge log lines.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while chunk := await stream.read(_READ_CHUNK):
            text = decoder.decode(chunk)
            sink.write(text)
            *lines, pending = (pending + text).split("\n")
            tail.extend(line.rstrip("\r") for line in lines)
            pending = pending[-_READ_CHUNK:]
        text = decoder.decode(b"", final=True)
        sink.write(text)
        pending += text
        if pending:
            tail.append(pending.rstrip("\r"))

    async def run_meshing_pipeline(
        self,
        *,
        case_root: Path,
        extract_features_argv: Sequence[str] | None,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> list[CommandResult]:
        steps = [block_mesh_step(case_root)]
        if extract_features_argv:
            steps.append(feature_extraction_step(extract_features_argv))
        steps.extend([snappy_step(case_root), check_mesh_step(case_root)])

        results: list[CommandResult] = []
        for step in steps:
            result = await self.run_step(
                step, case_root=case_root, timeout_seconds=timeout_seconds,
                env=env, cancel_event=cancel_event,
            )
            results.append(result)
            if not result.succeeded:
                break
        return results

    async def run_step(
        self,
        step: MeshingStep,
        *,
        case_root: Path,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> CommandResult:
        return await self.run(
            step.argv,
            case_root=case_root,
            log_name=step.log_name,
            stage=step.stage,
            timeout_seconds=timeout_seconds,
            env=env,
            cancel_event=cancel_event,
        )
