"""Test double for the OpenFOAM utilities of a .msh import (M1-MSH).

Outputs follow the inputs the way the real tools did in E0 and M1b, in the
real ASCII formats, so the application's readers and checks run unchanged:
the converters write a polyMesh with the file's patches (Fluent walls as
wall, everything else patch) and cell zones, an owner header with nCells,
points spanning the file's box; transformPoints scales the points;
createPatch moves a patch's faces to a new patch (and, like v2512, leaves a
patch alone when the new name is its own); foamDictionary sets a type or adds
a neighbourPatch (other entries it adds are not kept); checkMesh reports
"Mesh OK" or failed checks (with exit 0, as the real one); postProcess reports
AMI weights at rest and moveDynamicMesh over the steps of its controlDict, for
the cyclicAMI pairs of the boundary file. The re-save script copies the file.
Switches simulate failures.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.msh_import.detect import inspect_mesh_file
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus

Patches = list[tuple[str, str, int]]  # name, type, faces
Neighbours = dict[str, str]  # cyclicAMI patch -> its neighbourPatch
CONVERTERS = ("fluent3DMeshToFoam", "gmshToFoam")


def write_mesh(poly: Path, patches: Patches, zones: Mapping[str, int], cells: int,
               box: tuple[tuple[float, float, float], tuple[float, float, float]]) -> None:
    """A polyMesh the readers accept: 8 box corners, one triangle per boundary
    face, face i owned by cell i mod cells, the zones as consecutive cell labels."""
    poly.mkdir(parents=True, exist_ok=True)
    (x0, y0, z0), (x1, y1, z1) = box
    corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    (poly / "points").write_text(f"{len(corners)}\n(\n" + "".join(
        f"({x:.17g} {y:.17g} {z:.17g})\n" for x, y, z in corners) + ")\n", encoding="utf-8")
    total = sum(f for _, _, f in patches)
    (poly / "faces").write_text(f"{total}\n(\n" + "3(0 1 2)\n" * total + ")\n",
                                encoding="utf-8")
    owners = "".join(f"{i % cells}\n" for i in range(total))
    (poly / "owner").write_text(
        f'FoamFile\n{{\n    format ascii;\n    note "nPoints:8  nCells:{cells}  '
        f'nFaces:{total}  nInternalFaces:0";\n    class labelList;\n}}\n{total}\n(\n{owners})\n',
        encoding="utf-8")
    (poly / "neighbour").write_text("0\n(\n)\n", encoding="utf-8")
    write_boundary(poly, patches)
    start = 0
    rows = []
    for name, count in zones.items():
        labels = " ".join(str(i) for i in range(start, start + count))
        rows.append(f"{name}\n{{\n    type cellZone;\n    cellLabels List<label> {count}"
                    f"\n({labels})\n;\n}}\n")
        start += count
    if zones:
        (poly / "cellZones").write_text(f"{len(zones)}\n(\n{''.join(rows)})\n",
                                        encoding="utf-8")


def write_boundary(poly: Path, patches: Patches, neighbours: Neighbours | None = None) -> None:
    start, rows = 0, []
    for name, kind, faces in patches:
        neighbour = (neighbours or {}).get(name)
        extra = f"        neighbourPatch {neighbour};\n" if neighbour else ""
        rows.append(f"    {name}\n    {{\n        type {kind};\n        nFaces {faces};\n"
                    f"        startFace {start};\n{extra}    }}\n")
        start += faces
    (poly / "boundary").write_text(f"{len(patches)}\n(\n{''.join(rows)})\n", encoding="utf-8")


def read_boundary(poly: Path) -> Patches:
    text = (poly / "boundary").read_text("utf-8")
    return [(n, t, int(f)) for n, t, f in re.findall(
        r"(\S+)\n\s*\{\s*type (\w+);\s*nFaces (\d+);", text)]


def read_neighbours(poly: Path) -> Neighbours:
    text = (poly / "boundary").read_text("utf-8")
    return dict(re.findall(r"(\S+)\n\s*\{[^{}]*?neighbourPatch (\S+);", text))


def _names(data: bytes) -> tuple[list[tuple[str, str]], list[str], int | None]:
    """(patch, OpenFOAM type) list, cell zones and declared cells of a file:
    Gmsh $PhysicalNames (dimension 2 patches, 3 zones; every patch "patch"),
    Fluent zone sections (wall -> wall; fluid and solid are zones; interior
    skipped), Fluent (12 (0 ...)) for the cells."""
    if data.lstrip().startswith(b"$MeshFormat"):
        block = re.search(rb"\$PhysicalNames\s+\d+\s+(.*?)\$EndPhysicalNames", data, re.S)
        found = re.findall(rb'(\d+)\s+\d+\s+"([^"]*)"', block[1]) if block else []
        return ([(n.decode(), "patch") for d, n in found if d == b"2"],
                [n.decode() for d, n in found if d == b"3"], None)
    zones = re.findall(rb"^\((?:39|45)\s*\(\s*\d+\s+(\S+)\s+([^\s()]+)", data, re.M)
    cells = re.search(rb"^\(12 \(0 ([0-9a-fA-F]+) ([0-9a-fA-F]+) ", data, re.M)
    declared = int(cells[2], 16) - int(cells[1], 16) + 1 if cells else None
    return ([(n.decode(), "wall" if k == b"wall" else "patch") for k, n in zones
             if k not in (b"fluid", b"solid", b"interior")],
            [n.decode() for k, n in zones if k in (b"fluid", b"solid")], declared)


class FakeMshRunner(OpenFOAMRunner):
    """fail: a command that exits 1. outcome: {command: TIMEOUT | CANCELLED}.
    cells: nCells the converter writes (default: the file's declared count,
        else 100). faces: faces per patch (default 10).
    default_faces: faces gmshToFoam puts in defaultFaces.
    drop_patches: patches the converter leaves out.
    zones: override the converter's cell zones {name: cells}.
    failed_checks: checkMesh reports this many failed checks (exit 0).
    baffles: checkMesh reports this many baffle faces.
    weights: (min, max) AMI sum(weights) at rest.
    turning: (min, max) AMI sum(weights) moveDynamicMesh reports half way round
        (`weights` at the other steps).
    turned_steps: steps moveDynamicMesh does (default: its controlDict's)."""

    def __init__(self, *, fail: str | None = None,
                 outcome: Mapping[str, RunStatus] | None = None,
                 cells: int | None = None, faces: int = 10, default_faces: int = 0,
                 drop_patches: Sequence[str] = (), zones: Mapping[str, int] | None = None,
                 failed_checks: int = 0, baffles: int = 0,
                 weights: tuple[float, float] = (0.98, 1.03),
                 turning: tuple[float, float] = (0.97, 1.04),
                 turned_steps: int | None = None,
                 cancel_after: tuple[str, asyncio.Event] | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.argv: list[tuple[str, ...]] = []
        self.fail, self.outcome = fail, dict(outcome or {})
        self.cells, self.faces, self.default_faces = cells, faces, default_faces
        self.drop, self.zones = set(drop_patches), zones
        self.failed_checks, self.baffles, self.weights = failed_checks, baffles, weights
        self.turning, self.turned_steps = turning, turned_steps
        self.cancel_after = cancel_after

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def run(
        self, argv: Sequence[str], *, case_root: Path, log_name: str,
        stage: IssueStage = IssueStage.OPENFOAM_EXECUTION,
        timeout_seconds: float | None = None, env: Mapping[str, str] | None = None,
        cancel_event: asyncio.Event | None = None, logs_dir: Path | None = None,
        on_start: Callable[[int], None] | None = None,
    ) -> CommandResult:
        name = "resave" if argv[0].endswith("python") or "python" in Path(argv[0]).name \
            else argv[0]
        self.calls.append((name, case_root.name))
        self.argv.append(tuple(argv))
        logs = logs_dir if logs_dir is not None else case_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / log_name
        if self.fail == name:
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
        handler = self._convert if name in CONVERTERS else getattr(self, f"_{name}")
        text = handler(argv, case_root)
        log.write_text(text, encoding="utf-8")
        if self.cancel_after is not None and self.cancel_after[0] == name:
            self.cancel_after[1].set()
        return self._result(argv, case_root, log, RunStatus.SUCCESS, 0)

    # --- the tools ------------------------------------------------------------------------

    @staticmethod
    def _poly(case: Path) -> Path:
        return case / "constant" / "polyMesh"

    def _resave(self, argv: Sequence[str], case: Path) -> str:
        shutil.copyfile(argv[2], case / argv[3])
        return f"Re-saved {argv[2]} as MSH 2.2 ASCII: {argv[3]}\n"

    def _convert(self, argv: Sequence[str], case: Path) -> str:
        """Patches and zones read by the fake's own parser (as the converters
        write every zone), never by the detector under test (review of M1b)."""
        source = Path(argv[1]) if Path(argv[1]).is_absolute() else case / argv[1]
        names, zone_names, declared = _names(source.read_bytes())
        patches: Patches = [(n, kind, self.faces) for n, kind in names if n not in self.drop]
        if self.default_faces:
            patches.append(("defaultFaces", "patch", self.default_faces))
        cells = self.cells or declared or 100
        zones = dict(self.zones) if self.zones is not None else {
            z: max(1, cells // max(1, len(zone_names))) for z in zone_names}
        box = inspect_mesh_file(source).box  # coordinates only
        write_mesh(self._poly(case), patches, zones, cells,
                   box or ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
        return f"{argv[0]} End\n"

    def _transformPoints(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        factor = float(argv[argv.index("-scale") + 1].strip("()").split()[0])
        path = self._poly(case) / "points"
        text = path.read_text("utf-8")
        path.write_text(re.sub(r"\(([^()]+)\)", lambda m: "(" + " ".join(
            f"{float(v) * factor:.17g}" for v in m[1].split()) + ")", text), encoding="utf-8")
        return "transformPoints End\n"

    def _createPatch(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly = self._poly(case)
        found = {n: (t, f) for n, t, f in read_boundary(poly)}
        text = (case / "system/createPatchDict").read_text("utf-8")
        neighbours = read_neighbours(poly)
        moved: dict[str, tuple[str, int]] = {}
        for name, info, source in re.findall(
                r"name (\S+);\s*patchInfo\s*\{([^{}]*)\}.*?patches \((\S+)\);", text, re.S):
            if name == source or source not in found:
                continue  # v2512: a patch made from itself keeps its type (M1b)
            kind = re.search(r"type\s+(\w+);", info)
            neighbour = re.search(r"neighbourPatch\s+(\S+);", info)
            moved[name] = (kind[1] if kind else "patch", found.pop(source)[1])
            if neighbour:
                neighbours[name] = neighbour[1]
        write_boundary(poly, [(n, t, f) for n, (t, f) in {**found, **moved}.items()],
                       neighbours)
        return "createPatch End\n"

    def _foamDictionary(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        poly = self._poly(case)
        entry = argv[argv.index("-entry") + 1]
        if "-remove" in argv:
            return ""  # the fake's boundary has no inGroups
        action = "-set" if "-set" in argv else "-add"
        value = argv[argv.index(action) + 1]
        _, patch, key = entry.split("/")
        neighbours = read_neighbours(poly)
        if key == "neighbourPatch":
            neighbours[patch] = value
        write_boundary(poly, [(n, value if n == patch and key == "type" else t, f)
                              for n, t, f in read_boundary(poly)], neighbours)
        return ""

    def _checkMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        baffles = (f"    Number of identical duplicate faces (baffle faces): {self.baffles}\n"
                   if self.baffles else "")
        end = (f"\nFailed {self.failed_checks} mesh checks.\n" if self.failed_checks
               else "\nMesh OK.\n")
        return ("Mesh stats\n    points:           8\n    faces:            100\n"
                "    cells:            100\nChecking topology...\n"
                "   *Number of regions: 2\n" + baffles
                + "Checking geometry...\n    Mesh non-orthogonality Max: 40.0 average: 5.0\n"
                + end)

    def _pairs(self, case: Path) -> list[tuple[str, str]]:
        poly = self._poly(case)
        kinds = {n: t for n, t, _ in read_boundary(poly)}
        listed = [(s, t) for s, t in read_neighbours(poly).items()
                  if kinds.get(s) == "cyclicAMI"]
        return [(s, t) for k, (s, t) in enumerate(listed) if (t, s) not in listed[:k]]

    def _postProcess(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        return self._weights(self._pairs(case), *self.weights)

    def _moveDynamicMesh(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        control = (case / "system/controlDict").read_text("utf-8")
        found = re.search(r"deltaT\s+(\S+);", control)
        delta = float(found[1]) if found else 1.0
        steps = self.turned_steps if self.turned_steps is not None else round(1 / delta)
        (case / "postProcessing" / "checkAMI").mkdir(parents=True, exist_ok=True)
        pairs = self._pairs(case)
        # As the real log: the pair rebuilt at each step; the `turning` sums only
        # half way round, so a reader of the first step alone misses them.
        return "".join(f"Time = {(i + 1) * delta:g}\nMesh OK.\n" + self._weights(
            pairs, *(self.turning if i == steps // 2 else self.weights))
            for i in range(steps)) + "End\n"

    @staticmethod
    def _weights(pairs: list[tuple[str, str]], low: float, high: float) -> str:
        return "".join(
            f"AMI: Creating AMI for source:{s} and target:{t}\n"
            f"AMI: Patch source sum(weights) min:{low} max:{high} average:1\n"
            f"AMI: Patch target sum(weights) min:{low} max:{high} average:1\n" for s, t in pairs)

    @staticmethod
    def _result(argv: Sequence[str], case_root: Path, log: Path, status: RunStatus,
                return_code: int | None, *issues: Issue) -> CommandResult:
        return CommandResult(
            run_id="fake", argv=tuple(argv), working_directory=str(case_root), status=status,
            return_code=return_code, started_at="", finished_at="", duration_seconds=0.0,
            timeout_seconds=None, log_path=str(log), stderr_log_path="", tail=(),
            stderr_tail=(), issues=issues)
