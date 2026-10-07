"""Test double for OpenFOAM utilities: writes outputs derived from their inputs."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from core.workflow.pipeline import MeshPipeline
from mesh.estimator import SystemResources
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus

FAKE_TOOLS = ("blockMesh", "snappyHexMesh", "checkMesh", "surfaceFeatureExtract")
PLENTY = SystemResources(available_ram_bytes=64 * 10**9, available_disk_bytes=10**12,
                         cpu_count=8)


def openfoam_env(
    directory: Path, version: str = "v2312", tools: Sequence[str] = FAKE_TOOLS
) -> dict[str, str]:
    """A PATH with placeholder OpenFOAM executables, so environment checks pass."""
    bin_dir = directory / "fake-openfoam-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in tools:
        for file_name in (name, f"{name}.bat"):  # POSIX and Windows lookup
            path = bin_dir / file_name
            path.write_text("exit 0\n", encoding="utf-8")
            path.chmod(0o755)
    return {"PATH": str(bin_dir), "WM_PROJECT": "OpenFOAM", "WM_PROJECT_VERSION": version}


def fake_pipeline(
    runner: OpenFOAMRunner, env: Mapping[str, str], resources: SystemResources = PLENTY,
    **kwargs: object,
) -> MeshPipeline:
    return MeshPipeline(runner, environment=env, system_probe=lambda _: resources,
                        **kwargs)  # type: ignore[arg-type]

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


_ABNORMAL = {
    RunStatus.TIMEOUT: (IssueCategory.TIMEOUT_CANCELLATION, "COMMAND_TIMEOUT"),
    RunStatus.CANCELLED: (IssueCategory.TIMEOUT_CANCELLATION, "COMMAND_CANCELLED"),
}


class FakeOpenFOAMRunner(OpenFOAMRunner):
    """fail: command that exits non-zero. outcome: {command: TIMEOUT|CANCELLED}.
    cancel_after: (command, event) sets the event when that command finishes,
    simulating a user cancelling while the run continues. A set cancel_event
    makes the running command end CANCELLED."""

    def __init__(
        self, *, fail: str | None = None, no_output: tuple[str, ...] = (),
        outcome: Mapping[str, RunStatus] | None = None,
        cancel_after: tuple[str, asyncio.Event] | None = None,
    ) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.argv: list[tuple[str, ...]] = []
        self.fail = fail
        self.no_output = no_output
        self.outcome = dict(outcome or {})
        self.cancel_after = cancel_after

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

        status = self.outcome.get(name)
        if status is None and cancel_event is not None and cancel_event.is_set():
            status = RunStatus.CANCELLED
        if status is not None:
            category, code = _ABNORMAL[status]
            log.write_text(f"{name} interrupted\n", encoding="utf-8")
            return self._result(argv, case_root, log, status, None, Issue(
                category=category, severity=IssueSeverity.ERROR, stage=stage,
                code=code, message=f"{name} {status.value.lower()}.", log_reference=str(log),
            ))

        if self.cancel_after is not None and self.cancel_after[0] == name:
            self.cancel_after[1].set()

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
        return_code: int | None, *issues: Issue,
    ) -> CommandResult:
        return CommandResult(
            run_id="fake", argv=tuple(argv), working_directory=str(case_root),
            status=status, return_code=return_code, started_at="", finished_at="",
            duration_seconds=0.0, timeout_seconds=None, log_path=str(log),
            stderr_log_path="", tail=(), stderr_tail=(), issues=issues,
        )
