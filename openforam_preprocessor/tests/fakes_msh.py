"""Test double for the OpenFOAM utilities of a .msh import (M1-MSH).

Outputs follow the inputs the way the real tools did in E0, M1b and M2a, in
the real formats, so the application's readers and checks run unchanged: the
converters write a binary polyMesh (M2a: every imported mesh is binary; the
boundary file, the zone names and the headers stay text) with the file's
patches (Fluent walls as wall, everything else patch) and cell zones, an
owner header with nCells, points spanning the file's box; transformPoints
scales the points; createPatch moves a patch's faces to a new patch (and, like
v2512, leaves a patch alone when the new name is its own); foamDictionary sets
a type or adds a neighbourPatch (other entries it adds are not kept); topoSet
does nothing the fake needs; surfaceMeshExtract writes the boundary as a VTK
PolyData file, one region per patch and per zone surface, and lists them in
its log ("surfZone N : name"); checkMesh reports "Mesh OK" or failed checks
(with exit 0, as the real one); postProcess reports AMI weights at rest, or
writes one polygon per cutting plane of a cuts dictionary; moveDynamicMesh
reports the weights over the steps of its controlDict, for the cyclicAMI
pairs of the boundary file. The re-save script copies the file. Switches
simulate failures.

The geometry, written binary where the app must never read it, is kept in
fake.json beside the mesh: 8 box corners, the cells, the zones. Boundary face
i is a triangle on box corners (0, 1, 2) when i is even, (7, 6, 5) when odd
(both halves of an end face of the box, so each patch's area is its faces x
half that face, and the boundary's points span the whole box), with its own
point labels (3i, 3i+1, 3i+2): a face of a zone's surface is the same face as
the patch face it duplicates, as in the real extraction. Face i is owned by
cell i mod cells; a zone holds consecutive cells.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.msh_import.detect import inspect_mesh_file
from openfoam.runner import CommandResult, OpenFOAMRunner, RunStatus
from tests import foam_binary

Patches = list[tuple[str, str, int]]  # name, type, faces
Neighbours = dict[str, str]  # cyclicAMI patch -> its neighbourPatch
CONVERTERS = ("fluent3DMeshToFoam", "gmshToFoam")
FAKE = "fake.json"  # the fake's own record of the geometry, beside the polyMesh files
TRIANGLES = ((0, 1, 2), (7, 6, 5))  # box corners of even and odd boundary faces
Box = tuple[tuple[float, float, float], tuple[float, float, float]]


def write_mesh(poly: Path, patches: Patches, zones: Mapping[str, int], cells: int,
               box: Box) -> None:
    """A binary polyMesh as v2512 writes it (the app reads only the boundary,
    the owner header and the zone names), and fake.json: 8 box corners, the
    cells, the zones as consecutive cell labels."""
    poly.mkdir(parents=True, exist_ok=True)
    (x0, y0, z0), (x1, y1, z1) = box
    corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    total = sum(f for _, _, f in patches)
    spans, start = {}, 0
    for name, count in zones.items():
        spans[name] = [start, count]
        start += count
    (poly / FAKE).write_text(json.dumps({"corners": corners, "cells": cells,
                                         "zones": spans}), encoding="utf-8")
    _write_points(poly, np.array(corners, dtype="<f8"))
    foam_binary.write(poly / "faces", "faceCompactList",
                      np.arange(0, 3 * total + 1, 3, dtype="<i4"))
    foam_binary.write(poly / "owner", "labelList", np.arange(total, dtype="<i4") % cells,
                      note=f"nPoints:8  nCells:{cells}  nFaces:{total}  nInternalFaces:0")
    foam_binary.write(poly / "neighbour", "labelList", np.zeros(0, dtype="<i4"))
    write_boundary(poly, patches)
    if zones:
        rows = b"".join(
            f"{name}\n{{\n    type            cellZone;\ncellLabels      List<label> ".encode()
            + foam_binary.binary_list(np.arange(s, s + n, dtype="<i4")).rstrip(b"\n")
            + b"\n;\n}\n\n" for name, (s, n) in spans.items())
        (poly / "cellZones").write_bytes(
            foam_binary.header("regIOobject", "cellZones").encode()
            + f"{len(zones)}\n(\n".encode() + rows + b")\n")


def _write_points(poly: Path, corners: np.ndarray) -> None:
    foam_binary.write(poly / "points", "vectorField", corners)


def geometry(poly: Path) -> dict[str, Any]:
    """fake.json of a polyMesh (through the links of a surface case)."""
    found: dict[str, Any] = json.loads(
        ((poly / "boundary").resolve().parent / FAKE).read_text("utf-8"))
    return found


def fake_surface(folder: Path) -> None:
    """Extract the surface of a fake mesh case as a run does (surface.py), with
    the fake surfaceMeshExtract."""
    from machines.msh_import import surface
    from machines.msh_import.checks import mesh_zone_names

    zones = sorted(mesh_zone_names(folder))
    surface.prepare(folder, zones, {})
    step = surface.steps(folder.name, zones)[-1]
    log = FakeMshRunner()._surfaceMeshExtract(step.argv, surface.surface_dir(folder))
    assert surface.index(folder, log)


def vtp(points: np.ndarray, connectivity: np.ndarray, offsets: np.ndarray,
        region: np.ndarray | None = None) -> str:
    """VTK XML PolyData as OpenFOAM's vtk writer makes it: base64 binary
    arrays, each after its UInt64 byte count."""
    def array(kind: str, name: str, values: np.ndarray, components: int = 1) -> str:
        raw = np.ascontiguousarray(values).tobytes()
        data = base64.b64encode(np.uint64(len(raw)).tobytes() + raw).decode()
        extra = f" NumberOfComponents='{components}'" if components > 1 else ""
        return (f"        <DataArray type='{kind}' Name='{name}'{extra} format='binary'>\n"
                f"{data}\n        </DataArray>\n")
    cells = ("      <CellData>\n" + array("Int32", "region", region.astype("<i4"))
             + "      </CellData>\n") if region is not None else ""
    return ("<?xml version='1.0'?>\n<VTKFile type='PolyData' version='0.1' "
            "byte_order='LittleEndian' header_type='UInt64'>\n  <PolyData>\n"
            f"    <Piece NumberOfPoints='{len(points)}' NumberOfPolys='{len(offsets)}'>\n"
            "      <Points>\n" + array("Float32", "Points", points.astype("<f4"), 3)
            + "      </Points>\n      <Polys>\n"
            + array("Int32", "connectivity", connectivity.astype("<i4"))
            + array("Int32", "offsets", offsets.astype("<i4"))
            + "      </Polys>\n" + cells + "    </Piece>\n  </PolyData>\n</VTKFile>\n")


def write_boundary(poly: Path, patches: Patches, neighbours: Neighbours | None = None) -> None:
    start, rows = 0, []
    for name, kind, faces in patches:
        neighbour = (neighbours or {}).get(name)
        extra = f"        neighbourPatch {neighbour};\n" if neighbour else ""
        rows.append(f"    {name}\n    {{\n        type {kind};\n        nFaces {faces};\n"
                    f"        startFace {start};\n{extra}    }}\n")
        start += faces
    # Text under a binary header, as v2512 writes the boundary of a binary mesh.
    (poly / "boundary").write_text(foam_binary.header("polyBoundaryMesh", "boundary")
                                   + f"{len(patches)}\n(\n{''.join(rows)})\n", encoding="utf-8")


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
        poly = self._poly(case)
        found = geometry(poly)
        found["corners"] = [[v * factor for v in c] for c in found["corners"]]
        (poly / FAKE).write_text(json.dumps(found), encoding="utf-8")
        _write_points(poly, np.array(found["corners"], dtype="<f8"))
        return "transformPoints End\n"

    def _topoSet(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        return "topoSet End\n"

    def _surfaceMeshExtract(self, argv: Sequence[str], case: Path) -> str:  # noqa: N802
        """Every patch (in boundary order), then with -faceZones each cell zone's
        surface (topoSet's zoneSurface_<zone>, in its dictionary's order): the
        boundary faces owned by the zone's cells."""
        poly = self._poly(case)
        found = geometry(poly)
        corners = np.array(found["corners"], dtype=float)
        cells = int(found["cells"])
        regions: list[tuple[str, list[int]]] = []
        start = 0
        for name, _, count in read_boundary(poly):
            regions.append((name, list(range(start, start + count))))
            start += count
        total = start
        if "-faceZones" in argv and (case / "system/topoSetDict").is_file():
            listed = re.findall(r"name (zoneSurface_\S+); type faceZoneSet",
                                (case / "system/topoSetDict").read_text("utf-8"))
            for zone_set in listed:
                first, count = found["zones"][zone_set.removeprefix("zoneSurface_")]
                regions.append((zone_set, [i for i in range(total)
                                           if first <= i % cells < first + count]))
        points = np.array([corners[TRIANGLES[i % 2][k]] for i in range(total)
                           for k in range(3)]).reshape(-1, 3)
        faces = [i for _, members in regions for i in members]
        region = np.array([r for r, (_, members) in enumerate(regions) for _ in members])
        connectivity = np.array([3 * i + k for i in faces for k in range(3)])
        offsets = 3 * np.arange(1, len(faces) + 1)
        (case / argv[-1]).write_text(vtp(points, connectivity, offsets, region),
                                     encoding="utf-8")
        listing = "".join(f"surfZone {r} : {name}\n" for r, (name, _) in enumerate(regions))
        return f"Writing merged surface to \"{argv[-1]}\"\n{listing}End\n"

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
        if "-dict" in argv and argv[argv.index("-dict") + 1].endswith("cutsDict"):
            return self._cuts(case)
        return self._weights(self._pairs(case), *self.weights)

    @staticmethod
    def _cuts(case: Path) -> str:
        """One square (side 0.2) on each cutting plane, centred on its point."""
        text = (case / "system/cutsDict").read_text("utf-8")
        out = case / "postProcessing/cuts/constant"
        out.mkdir(parents=True, exist_ok=True)
        for name, point, normal in re.findall(
                r"(\w+) \{ type cuttingPlane; point \(([^)]*)\); normal \(([^)]*)\);", text):
            p = np.array(point.split(), dtype=float)
            n = np.array(normal.split(), dtype=float)
            u = np.cross(n, (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0))
            u /= np.linalg.norm(u)
            v = np.cross(n, u)
            square = np.array([p + 0.1 * (a * u + b * v)
                               for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
            (out / f"{name}.vtp").write_text(vtp(square, np.arange(4), np.array([4])),
                                             encoding="utf-8")
        return "surfaces End\n"

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
