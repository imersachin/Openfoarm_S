"""Test double for OpenFOAM utilities: writes outputs derived from their inputs."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus

CHECKMESH_OK = """\
Mesh stats
    points:           1200
    faces:            3400
    cells:            1000
Checking geometry...
    Mesh non-orthogonality Max: 40.0 average: 5.0
Mesh OK.
"""


def _digest(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


class FakeOpenFOAMRunner(OpenFOAMRunner):
    def __init__(self, *, fail: str | None = None, no_output: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.argv: list[tuple[str, ...]] = []
        self.fail = fail
        self.no_output = no_output

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
        name = argv[0]
        self.calls.append(name)
        self.argv.append(tuple(argv))
        logs = case_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / log_name
        poly = case_root / "constant" / "polyMesh"
        tri = case_root / "constant" / "triSurface"
        system = case_root / "system"

        if name == self.fail:
            log.write_text(f"FOAM FATAL ERROR in {name}\n", encoding="utf-8")
            return self._result(argv, case_root, log, RunStatus.FAILED, 1, Issue(
                category=IssueCategory.EXECUTION, severity=IssueSeverity.ERROR, stage=stage,
                code="COMMAND_FAILED", message=f"{name} exited with code 1.",
                log_reference=str(log),
            ))

        if name not in self.no_output:
            if name == "blockMesh":
                poly.mkdir(parents=True, exist_ok=True)
                (poly / "points").write_text(
                    "background " + _digest(system / "blockMeshDict"), encoding="utf-8"
                )
            elif name == "surfaceFeatureExtract":
                for stl in sorted(tri.glob("*.stl")):
                    stl.with_suffix(".eMesh").write_text(
                        _digest(stl, system / "surfaceFeatureExtractDict"), encoding="utf-8"
                    )
            elif name == "snappyHexMesh":
                inputs = [poly / "points", *sorted(tri.glob("*")),
                          system / "snappyHexMeshDict", system / "meshQualityDict"]
                mesh_id = _digest(*inputs)
                for item in ("points", "faces", "owner", "neighbour", "boundary"):
                    (poly / item).write_text(f"{item} {mesh_id}", encoding="utf-8")
            elif name == "checkMesh":
                # Real checkMesh may write diagnostic sets into polyMesh/sets.
                (poly / "sets").mkdir(parents=True, exist_ok=True)
                (poly / "sets" / "skewFaces").write_text(_digest(poly / "points"), "utf-8")

        log.write_text(CHECKMESH_OK if name == "checkMesh" else f"{name} End\n", "utf-8")
        return self._result(argv, case_root, log, RunStatus.SUCCESS, 0)

    @staticmethod
    def _result(
        argv: Sequence[str], case_root: Path, log: Path, status: RunStatus,
        return_code: int, *issues: Issue,
    ) -> CommandResult:
        return CommandResult(
            run_id="fake", argv=tuple(argv), working_directory=str(case_root),
            status=status, return_code=return_code, started_at="", finished_at="",
            duration_seconds=0.0, timeout_seconds=None, log_path=str(log),
            stderr_log_path="", tail=(), stderr_tail=(), issues=issues,
        )
