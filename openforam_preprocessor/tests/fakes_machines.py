"""Test double for the OpenFOAM utilities the machine pipeline runs (G4).

Outputs are derived from inputs, so caching is meaningful, and patches, types
and zones follow the generated dictionaries the way the real tools did in G2
and G3: snappyHexMesh keeps the surface regions and drops the emptied
background patch, createPatch turns <name>_src into the cyclicAMI <name>,
foamDictionary sets a patch type. Switches simulate failures.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus

MESH_FILES = ("points", "faces", "owner", "neighbour")


def _digest(*parts: Path | str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, Path):
            digest.update(part.read_bytes() if part.is_file() else b"<missing>")
        else:
            digest.update(part.encode())
    return digest.hexdigest()[:16]


Boundary = dict[str, tuple[str, int]]  # name -> (type, faces)


def write_boundary(poly: Path, patches: Boundary) -> None:
    rows = "".join(f"    {n}\n    {{\n        type {t};\n        nFaces {f};\n"
                   f"        startFace 0;\n    }}\n" for n, (t, f) in patches.items())
    (poly / "boundary").write_text(f"{len(patches)}\n(\n{rows})\n", encoding="utf-8")


def read_boundary(poly: Path) -> Boundary:
    path = poly / "boundary"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    return {n: (t, int(f)) for n, t, f in re.findall(
        r"(\w+)\n\s*\{\s*type (\w+);\s*nFaces (\d+);", text)}


def write_zones(poly: Path, names: Sequence[str]) -> None:
    body = "".join(f"{n}\n{{\n    type cellZone;\n}}\n" for n in names)
    (poly / "cellZones").write_text(f"{len(names)}\n(\n{body})\n", encoding="utf-8")


def read_zones(poly: Path) -> list[str]:
    path = poly / "cellZones"
    return re.findall(r"^(\w+)\n\{", path.read_text("utf-8"), re.M) if path.is_file() else []


class FakeMachineRunner(OpenFOAMRunner):
    """fail: command, or (command, case), that exits 1.
    outcome: {command: TIMEOUT | CANCELLED}.
    cancel_after: (command, event) sets the event when that command finishes.
    wrong_region: cases whose snappyHexMesh keeps the wrong region (only the
        background patch survives).
    no_zone: cases whose topoSet makes no cell zone.
    regions: override the region count checkMesh reports.
    weights: (min, max) AMI sum(weights) postProcess reports."""

    def __init__(self, *, fail: str | tuple[str, str] | None = None,
                 outcome: Mapping[str, RunStatus] | None = None,
                 cancel_after: tuple[str, asyncio.Event] | None = None,
                 wrong_region: Sequence[str] = (), no_zone: Sequence[str] = (),
                 regions: int | None = None,
                 weights: tuple[float, float] = (0.95, 1.05)) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.cwds: list[Path] = []
        self.fail, self.outcome = fail, dict(outcome or {})
        self.cancel_after = cancel_after
        self.wrong_region, self.no_zone = set(wrong_region), set(no_zone)
        self.regions, self.weights = regions, weights

    async def run(
        self, argv: Sequence[str], *, case_root: Path, log_name: str,
        stage: IssueStage = IssueStage.OPENFOAM_EXECUTION,
        timeout_seconds: float | None = None, env: Mapping[str, str] | None = None,
        cancel_event: asyncio.Event | None = None, logs_dir: Path | None = None,
        on_start: Callable[[int], None] | None = None,
    ) -> CommandResult:
        name, case = argv[0], case_root.name
        self.calls.append((name, case))
        self.cwds.append(case_root)
        logs = logs_dir if logs_dir is not None else case_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / log_name
        if self.fail in (name, (name, case)):
            log.write_text(f"FOAM FATAL ERROR in {name}\n", encoding="utf-8")
            return self._result(argv, case_root, log, RunStatus.FAILED, 1, Issue(
                category=IssueCategory.EXECUTION, severity=IssueSeverity.ERROR, stage=stage,
                code="COMMAND_FAILED", message=f"{name} exited with code 1."))
        override = self.outcome.get(name)
        if override is None and cancel_event is not None and cancel_event.is_set():
            override = RunStatus.CANCELLED
        if override is not None:
            log.write_text(f"{name} interrupted\n", encoding="utf-8")
            return self._result(argv, case_root, log, override, None, Issue(
                category=IssueCategory.TIMEOUT_CANCELLATION, severity=IssueSeverity.ERROR,
                stage=stage, code="COMMAND_CANCELLED", message=f"{name} interrupted."))
        text = getattr(self, f"_{name}")(argv, case_root)
        log.write_text(text, encoding="utf-8")
        if self.cancel_after is not None and self.cancel_after[0] == name:
            self.cancel_after[1].set()
        return self._result(argv, case_root, log, RunStatus.SUCCESS, 0)

    # --- the tools ------------------------------------------------------------------------

    @staticmethod
    def _poly(case: Path) -> Path:
        poly = case / "constant" / "polyMesh"
        poly.mkdir(parents=True, exist_ok=True)
        return poly

    def _blockMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly, block = self._poly(case), case / "system" / "blockMeshDict"
        for item in MESH_FILES:
            (poly / item).write_text(f"background {_digest(block)}", encoding="utf-8")
        rows = re.findall(r"^\s{4}(\w+) \{ type (\w+);", block.read_text("utf-8"), re.M)
        write_boundary(poly, {n: (t, 10) for n, t in rows})
        (poly / "cellZones").unlink(missing_ok=True)
        return "blockMesh End\n"

    def _surfaceFeatureExtract(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        for stl in sorted((case / "constant/triSurface").glob("*.stl")):
            stl.with_suffix(".eMesh").write_text(_digest(stl), encoding="utf-8")
        return "surfaceFeatureExtract End\n"

    def _snappyHexMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly, system = self._poly(case), case / "system"
        surfaces = sorted((case / "constant/triSurface").glob("*.stl"))
        mesh_id = _digest(poly / "points", *surfaces, system / "snappyHexMeshDict",
                          system / "meshQualityDict")
        for item in MESH_FILES:
            (poly / item).write_text(f"{item} {mesh_id}", encoding="utf-8")
        snappy = (system / "snappyHexMeshDict").read_text("utf-8")
        types = dict(re.findall(r"(\w+) \{ level \(\d+ \d+\); patchInfo \{ type (\w+); \} \}",
                                snappy))
        patches = {n: v for n, v in read_boundary(poly).items() if n != "background"}
        for stl in surfaces:
            for region in re.findall(r"^solid (\w+)", stl.read_text("utf-8"), re.M):
                patches[region] = (types.get(region, "patch"), 50)
        if case.name in self.wrong_region:
            patches = {"background": ("patch", 99)}
        write_boundary(poly, patches)
        return "snappyHexMesh Finished meshing\n"

    def _topoSet(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        topo = (case / "system/topoSetDict").read_text("utf-8")
        zone = re.search(r"name\s+(\w+);\s*type\s+cellZoneSet;", topo)
        if zone and case.name not in self.no_zone:
            write_zones(self._poly(case), [zone[1]])
        return "topoSet End\n"

    def _mergeMeshes(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        master, add = (case / argv[-2]).resolve(), (case / argv[-1]).resolve()
        poly, other = self._poly(master), add / "constant" / "polyMesh"
        mesh_id = _digest(*(poly / f for f in MESH_FILES), *(other / f for f in MESH_FILES))
        for item in MESH_FILES:
            (poly / item).write_text(f"{item} {mesh_id}", encoding="utf-8")
        write_boundary(poly, {**read_boundary(poly), **read_boundary(other)})
        write_zones(poly, [*read_zones(poly), *read_zones(other)])
        return "mergeMeshes End\n"

    def _createPatch(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly = self._poly(case)
        patches: Boundary = {}
        for n, (t, f) in read_boundary(poly).items():
            if n.endswith("_src"):
                patches[n.removesuffix("_src")] = ("cyclicAMI", f)
            else:
                patches[n] = (t, f)
        write_boundary(poly, patches)
        return "createPatch End\n"

    def _foamDictionary(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly = self._poly(case)
        entry, value = argv[argv.index("-entry") + 1], argv[argv.index("-set") + 1]
        patch = entry.split("/")[1]
        patches = read_boundary(poly)
        patches[patch] = (value, patches[patch][1])
        write_boundary(poly, patches)
        return ""

    def _checkMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        regions = self.regions if self.regions is not None else 1 + len(
            read_zones(self._poly(case)))
        region_line = (f"   *Number of regions: {regions}\n" if regions != 1
                       else "    Number of regions: 1 (OK).\n")
        return ("Mesh stats\n    points:           1200\n    faces:            3400\n"
                "    cells:            1000\nChecking topology...\n" + region_line
                + "Checking geometry...\n    Mesh non-orthogonality Max: 40.0 average: 5.0\n"
                "Mesh OK.\n")

    def _postProcess(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        low, high = self.weights
        stat = [n for n, (t, _) in read_boundary(self._poly(case)).items()
                if t == "cyclicAMI" and n.endswith("_stat")]
        return "".join(
            f"AMI: Creating AMI for source:{s} and target:{s.removesuffix('_stat')}_rot\n"
            f"AMI: Patch source sum(weights) min:{low} max:{high} average:1\n"
            f"AMI: Patch target sum(weights) min:{low} max:{high} average:1\n" for s in stat)

    @staticmethod
    def _result(argv: Sequence[str], case_root: Path, log: Path, status: RunStatus,
                return_code: int | None, *issues: Issue) -> CommandResult:
        return CommandResult(
            run_id="fake", argv=tuple(argv), working_directory=str(case_root), status=status,
            return_code=return_code, started_at="", finished_at="", duration_seconds=0.0,
            timeout_seconds=None, log_path=str(log), stderr_log_path="", tail=(),
            stderr_tail=(), issues=issues)
