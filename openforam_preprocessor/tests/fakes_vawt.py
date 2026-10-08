"""Test double for the OpenFOAM utilities the VAWT pipeline runs.

Outputs are derived from inputs (so caching is meaningful), and patches and
zones follow the generated dictionaries the way the real tools do (V0, V2).
Switches simulate the silent failures V0 found.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus
from vawt.status import read_status

VAWT_TOOLS = ("blockMesh", "snappyHexMesh", "surfaceFeatureExtract", "topoSet",
              "mergeMeshes", "createPatch", "checkMesh")
MESH_FILES = ("points", "faces", "owner", "neighbour")


def _digest(*parts: Path | str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, Path):
            digest.update(part.read_bytes() if part.is_file() else b"<missing>")
        else:
            digest.update(part.encode())
    return digest.hexdigest()[:16]


def write_boundary(poly: Path, faces: Mapping[str, int]) -> None:
    rows = "".join(
        f"    {name}\n    {{\n        type patch;\n        nFaces {count};\n"
        f"        startFace 0;\n    }}\n" for name, count in faces.items()
    )
    (poly / "boundary").write_text(f"{len(faces)}\n(\n{rows})\n", encoding="utf-8")


def read_faces(poly: Path) -> dict[str, int]:
    path = poly / "boundary"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    return {name: int(n) for name, n in re.findall(r"(\w+)\n\s*\{[^}]*?nFaces (\d+);", text)}


def write_zones(poly: Path, kind: str, names: Sequence[str]) -> None:
    body = "".join(f"{name}\n{{\n    type {kind[:-1]};\n}}\n" for name in names)
    (poly / kind).write_text(f"{len(names)}\n(\n{body})\n", encoding="utf-8")


def read_zones(poly: Path, kind: str) -> list[str]:
    path = poly / kind
    if not path.is_file():
        return []
    return re.findall(r"^(\w+)\n\{", path.read_text(encoding="utf-8"), re.MULTILINE)


class FakeVawtRunner(OpenFOAMRunner):
    """fail: command name, or (command, sub-case), that exits 1.
    outcome: {command: TIMEOUT | CANCELLED}.
    cancel_after: (command, event) sets the event when that command finishes.
    wrong_region: sub-cases whose snappyHexMesh meshes the wrong region (only the
        rotor patch survives, as with a mesh point inside a blade).
    empty_ami: createPatch leaves AMI1/AMI2 with no faces.
    no_zone: sub-cases where topoSet/snappyHexMesh create no zone.
    regions: override the region count checkMesh reports.
    checkmesh_failure: checkMesh reports one failed check."""

    def __init__(
        self, *, fail: str | tuple[str, str] | None = None,
        outcome: Mapping[str, RunStatus] | None = None,
        cancel_after: tuple[str, asyncio.Event] | None = None,
        wrong_region: Sequence[str] = (), empty_ami: bool = False,
        no_zone: Sequence[str] = (), regions: int | None = None,
        checkmesh_failure: bool = False, project_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []  # (command, sub-case)
        self.cwds: list[Path] = []
        self.statuses: list[dict[str, object]] = []  # status file seen at each command
        self.fail, self.outcome = fail, dict(outcome or {})
        self.cancel_after = cancel_after
        self.wrong_region, self.empty_ami = set(wrong_region), empty_ami
        self.no_zone, self.regions = set(no_zone), regions
        self.checkmesh_failure = checkmesh_failure
        self.project_root = project_root

    async def run(
        self, argv: Sequence[str], *, case_root: Path, log_name: str,
        stage: IssueStage = IssueStage.OPENFOAM_EXECUTION,
        timeout_seconds: float | None = None, env: Mapping[str, str] | None = None,
        cancel_event: asyncio.Event | None = None, logs_dir: Path | None = None,
    ) -> CommandResult:
        name, case = argv[0], case_root.name
        self.calls.append((name, case))
        self.cwds.append(case_root)
        if self.project_root is not None:
            status = read_status(self.project_root)
            if status is not None:
                self.statuses.append(status)
        logs = logs_dir if logs_dir is not None else case_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / log_name

        if self.fail in (name, (name, case)):
            log.write_text(f"FOAM FATAL ERROR in {name}\n", encoding="utf-8")
            return self._result(argv, case_root, log, RunStatus.FAILED, 1, Issue(
                category=IssueCategory.EXECUTION, severity=IssueSeverity.ERROR, stage=stage,
                code="COMMAND_FAILED", message=f"{name} exited with code 1.",
                log_reference=str(log)))
        status_override = self.outcome.get(name)
        if status_override is None and cancel_event is not None and cancel_event.is_set():
            status_override = RunStatus.CANCELLED
        if status_override is not None:
            code = ("COMMAND_TIMEOUT" if status_override is RunStatus.TIMEOUT
                    else "COMMAND_CANCELLED")
            log.write_text(f"{name} interrupted\n", encoding="utf-8")
            return self._result(argv, case_root, log, status_override, None, Issue(
                category=IssueCategory.TIMEOUT_CANCELLATION, severity=IssueSeverity.ERROR,
                stage=stage, code=code, message=f"{name} {status_override.value.lower()}.",
                log_reference=str(log)))

        text = getattr(self, f"_{name}")(argv, case_root)
        log.write_text(text, encoding="utf-8")
        if self.cancel_after is not None and self.cancel_after[0] == name:
            self.cancel_after[1].set()
        return self._result(argv, case_root, log, RunStatus.SUCCESS, 0)

    # --- the tools ---------------------------------------------------------------------

    @staticmethod
    def _poly(case: Path) -> Path:
        poly = case / "constant" / "polyMesh"
        poly.mkdir(parents=True, exist_ok=True)
        return poly

    def _blockMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly, block = self._poly(case), case / "system" / "blockMeshDict"
        for item in MESH_FILES:
            (poly / item).write_text(f"background {_digest(block)}", encoding="utf-8")
        names = re.findall(r"^\s{4}(\w+) \{ type patch;", block.read_text("utf-8"), re.M)
        write_boundary(poly, {n: 10 for n in names})
        for kind in ("cellZones", "faceZones"):
            (poly / kind).unlink(missing_ok=True)
        return "blockMesh End\n"

    def _surfaceFeatureExtract(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        tri = case / "constant" / "triSurface"
        for stl in sorted(tri.glob("*.stl")):
            stl.with_suffix(".eMesh").write_text(
                _digest(stl, case / "system" / "surfaceFeatureExtractDict"), encoding="utf-8")
        return "surfaceFeatureExtract End\n"

    def _snappyHexMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly, system = self._poly(case), case / "system"
        snappy = (system / "snappyHexMeshDict").read_text("utf-8")
        mesh_id = _digest(poly / "points", *sorted((case / "constant/triSurface").glob("*")),
                          system / "snappyHexMeshDict", system / "meshQualityDict")
        for item in MESH_FILES:
            (poly / item).write_text(f"{item} {mesh_id}", encoding="utf-8")
        before = read_faces(poly)
        rotor = re.search(r"type triSurfaceMesh;\s*name (\w+);", snappy)
        cylinder = re.search(r"(\w+)\n\s*\{\n\s*type\s+searchableCylinder", snappy)
        zoned = "faceZone" in snappy
        faces = {n: c for n, c in before.items() if n != "rotorBackground"}
        if cylinder and not zoned:
            faces[cylinder.group(1)] = 40
        if rotor:
            faces[rotor.group(1)] = 100
        if case.name in self.wrong_region:  # e.g. mesh point inside a blade
            faces = {rotor.group(1): 100} if rotor else {}
        write_boundary(poly, faces)
        if zoned and case.name not in self.no_zone:
            face_zone = re.search(r"faceZone\s+(\w+);", snappy)
            cell_zone = re.search(r"cellZone\s+(\w+);", snappy)
            write_zones(poly, "faceZones", [face_zone.group(1)] if face_zone else [])
            write_zones(poly, "cellZones", [cell_zone.group(1)] if cell_zone else [])
        return "snappyHexMesh Finished meshing\n"

    def _topoSet(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly = self._poly(case)
        topo = (case / "system" / "topoSetDict").read_text("utf-8")
        zone = re.search(r"name\s+(\w+);\s*type\s+cellZoneSet;", topo)
        if zone and case.name not in self.no_zone:
            write_zones(poly, "cellZones", [zone.group(1)])
        return "topoSet End\n"

    def _mergeMeshes(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        master, add = Path(argv[-2]), Path(argv[-1])
        poly, other = self._poly(master), add / "constant" / "polyMesh"
        mesh_id = _digest(*(poly / f for f in MESH_FILES), *(other / f for f in MESH_FILES))
        for item in MESH_FILES:
            (poly / item).write_text(f"{item} {mesh_id}", encoding="utf-8")
        write_boundary(poly, {**read_faces(poly), **read_faces(other)})
        write_zones(poly, "cellZones", read_zones(other, "cellZones"))
        return "mergeMeshes End\n"

    def _createPatch(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly = self._poly(case)
        renamed = {"AMI_outer": "AMI1", "AMI_rotor": "AMI2"}
        faces = {renamed.get(n, n): (0 if self.empty_ami and n in renamed else c)
                 for n, c in read_faces(poly).items()}
        write_boundary(poly, faces)
        return "createPatch End\n"

    def _checkMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        faces = read_faces(self._poly(case))
        regions = self.regions if self.regions is not None else (2 if "AMI1" in faces else 1)
        region_line = (f"   *Number of regions: {regions}\n" if regions != 1
                       else "    Number of regions: 1 (OK).\n")
        verdict = (" ***Concave cells (using face planes) found, number of cells: 3\n"
                   "Failed 1 mesh checks.\n" if self.checkmesh_failure else "Mesh OK.\n")
        return ("Mesh stats\n    points:           1200\n    faces:            3400\n"
                "    cells:            1000\nChecking topology...\n" + region_line
                + "Checking geometry...\n    Mesh non-orthogonality Max: 40.0 average: 5.0\n"
                + verdict)

    @staticmethod
    def _result(argv: Sequence[str], case_root: Path, log: Path, status: RunStatus,
                return_code: int | None, *issues: Issue) -> CommandResult:
        return CommandResult(
            run_id="fake", argv=tuple(argv), working_directory=str(case_root), status=status,
            return_code=return_code, started_at="", finished_at="", duration_seconds=0.0,
            timeout_seconds=None, log_path=str(log), stderr_log_path="", tail=(),
            stderr_tail=(), issues=issues,
        )
