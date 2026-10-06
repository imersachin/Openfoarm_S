from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    return_code: int
    started_at: str
    finished_at: str
    log_path: str
    tail: tuple[str, ...]


class OpenFOAMRunner:
    def __init__(self, tail_lines: int = 300) -> None:
        self.tail_lines = tail_lines

    async def run(
        self,
        argv: Sequence[str],
        *,
        case_root: Path,
        log_name: str,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        if not argv:
            raise ValueError("Command cannot be empty.")

        logs_dir = case_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / log_name
        tail: deque[str] = deque(maxlen=self.tail_lines)
        started_at = datetime.now(UTC)

        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=case_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        assert process.stdout is not None
        with log_path.open("w", encoding="utf-8") as output:
            try:
                async def consume() -> None:
                    async for line_bytes in process.stdout:
                        line = line_bytes.decode("utf-8", errors="replace")
                        output.write(line)
                        tail.append(line.rstrip())

                await asyncio.wait_for(consume(), timeout=timeout_seconds)
                return_code = await process.wait()

            except TimeoutError:
                process.terminate()
                await process.wait()
                raise TimeoutError(f"Timed out: {' '.join(argv)}") from None

        finished_at = datetime.now(UTC)
        return CommandResult(
            argv=tuple(argv),
            return_code=return_code,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            log_path=str(log_path),
            tail=tuple(tail),
        )

    async def run_meshing_pipeline(
        self,
        *,
        case_root: Path,
        extract_features_argv: Sequence[str] | None,
    ) -> list[CommandResult]:
        commands: list[tuple[Sequence[str], str]] = [
            (("blockMesh", "-case", str(case_root)), "01_blockMesh.log"),
        ]

        if extract_features_argv:
            commands.append((extract_features_argv, "02_surfaceFeatureExtract.log"))

        commands.extend([
            (("snappyHexMesh", "-case", str(case_root), "-overwrite"), "03_snappyHexMesh.log"),
            (
                (
                    "checkMesh", "-case", str(case_root),
                    "-allGeometry", "-allTopology",
                    "-meshQuality",
                ),
                "04_checkMesh.log",
            ),
        ])

        results: list[CommandResult] = []
        for argv, log_name in commands:
            result = await self.run(argv, case_root=case_root, log_name=log_name)
            results.append(result)
            if result.return_code != 0:
                break
        return results
