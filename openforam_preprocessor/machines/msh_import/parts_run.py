"""The run of a machine of several .msh files (M2b; parts.py for the cases).

ImportRun (pipeline.py) with its import done part by part: each file
converted at scale 1 (or its conversion from "Read the parts" reused), its
passage count detected again and compared with the confirmed one, its ring
made (or reused while ring.json says it is of these inputs), then the rings
merged into cases/imported/ with its surface. From there the run is a single
file's: the renamed and typed patches, the AMI pairs, checkMesh (without
-allTopology, the baffles counted by mergeOrSplitBaffles; parts.py), the
views, mesh validation (one region per part) and case setup.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.artifacts.hashing import hash_files, sha256_json
from core.issues import IssueCategory, IssueSeverity, IssueStage
from machines.assembly import Step
from machines.config import InterfaceJoint, MachineParts, MeshPart
from machines.mesh_folder import read_patches, region_count
from machines.msh_import import parts, resources, surface
from machines.msh_import.cases import (
    IMPORTED_CASE,
    PART_ZONES_DICT,
    convert_steps,
    converter,
    empty_patch_dict,
    import_cases,
    needs_resave,
    part_zones_dict,
    scale_step,
    used_names,
)
from machines.msh_import.checks import (
    check_converted,
    face_zone_names,
    has_mesh,
    mesh_cells,
    mesh_zone_names,
)
from machines.msh_import.detect import MeshFileInfo
from machines.msh_import.pipeline import (
    CHECK_MESH,
    IMPORT,
    IMPORT_REPORT,
    PREFLIGHT_REPORT,
    STEP_RESOURCES,
    SURFACE_EXECUTABLES,
    ImportRun,
    _mesh_files,
    _Plan,
    converter_files,
    preflight_cells,
    scratch_record,
)
from machines.msh_import.validation import mesh_digest, read_mesh_file
from machines.pipeline import (
    _EXCLUDED,
    CHECK_MESH_LOG,
    POLY_MESH,
    MachinePipeline,
    _issue,
    _read,
    _Run,
    _Stop,
)
from openfoam.dictionary import OpenFOAMFileWriter
from openfoam.environment import tool_identity

PART_EXECUTABLES = ("transformPoints", "createPatch", "topoSet", "mergeMeshes", "stitchMesh",
                    "checkMesh", *SURFACE_EXECUTABLES)


class PartsRun(ImportRun):
    """One run of a MachineParts project inside MachinePipeline.run."""

    def __init__(self, pipe: MachinePipeline, run: _Run) -> None:
        super().__init__(pipe, run)
        assert isinstance(self.mesh, MachineParts)
        self.machine: MachineParts = self.mesh
        self.files: list[tuple[MeshPart, MeshFileInfo, str]] = []

    # --- the plan ------------------------------------------------------------------------

    def _plan(self) -> _Plan:
        self.files = []
        for part in self.machine.parts:
            info, digest = read_mesh_file(part.source_path)
            assert digest is not None  # validation: readable and unchanged
            self.files.append((part, info, digest))
        digest = mesh_digest(self.machine)
        assert digest is not None
        return _Plan(None, digest, 1.0, import_cases(self.run.config))

    def _converters(self) -> tuple[str, ...]:
        return tuple(sorted({converter(info) for _, info, _ in self.files}))

    def _import_executables(self, plan: _Plan) -> tuple[str, ...]:
        return (*self._converters(), *PART_EXECUTABLES)

    def _step_kinds(self, op: str) -> tuple[str, ...]:
        if op == IMPORT:
            return ("convert", "assembly", "surface")
        if op == CHECK_MESH:
            return ("checkMeshTopology", "baffles", "amiWeights")
        return super()._step_kinds(op)

    def _file_bytes(self, plan: _Plan) -> int | None:
        return max(info.size for _, info, _ in self.files)  # one conversion at a time

    def _declared_cells(self, plan: _Plan) -> int | None:
        total = 0
        for part, info, _ in self.files:
            cells, _ = preflight_cells(info)
            if cells is None:
                return None
            total += cells * (part.passages or 1)
        return total

    def _converter(self, plan: _Plan) -> str:
        return ", ".join(self._converters())

    DROPS_FACE_ZONES = True

    def _face_zone_owners(self) -> dict[str, str]:
        """faceZone -> the part file(s) holding it (zones keep their names: two
        files' zones of one name are one zone after the merge)."""
        found: dict[str, list[str]] = {}
        for part, _, _ in self.files:
            folder = self.run.root / "cases" / parts.convert_case(part.prefix)
            for zone in face_zone_names(folder):
                found.setdefault(zone, []).append(f"{part.name} ({part.source_path.name})")
        return {zone: " and ".join(files) for zone, files in found.items()}

    def _expected_regions(self) -> int | None:
        """One per part, less one per stitched interface (it joins two parts)."""
        stitched = sum(i.joint is InterfaceJoint.STITCH for i in self.machine.interfaces)
        return len(self.machine.parts) - stitched

    def _convert_record(self, part: MeshPart, info: MeshFileInfo, digest: str) -> dict[str, Any]:
        return scratch_record(info, digest, self._tool((converter(info),)))

    def _ring_inputs(self, part: MeshPart, info: MeshFileInfo, digest: str) -> dict[str, Any]:
        return parts.ring_inputs(part, self.machine, self._convert_record(part, info, digest),
                                 self._tool(PART_EXECUTABLES))

    def _inputs(self, plan: _Plan, op: str) -> dict[str, str]:
        if op == IMPORT:
            root = self.run.root
            dictionaries = [root / "cases" / IMPORTED_CASE / r for r in sorted(plan.cases.imported)]
            return {**hash_files(root, dictionaries),
                    "parts": sha256_json([self._ring_inputs(p, i, d) for p, i, d in self.files]),
                    "tool": self._tool(self._import_executables(plan))}
        found = super()._inputs(plan, op)
        if op == CHECK_MESH:
            found["checks"] = sha256_json([s.argv for s in plan.cases.check_steps(True)])
            found["tool"] = self._tool(plan.cases.check_executables())
        return found

    # --- preflight -----------------------------------------------------------------------

    def _preflight(self, plan: _Plan, allow_risk: bool) -> None:
        """The assembled machine's cells (each file's declared count times its
        passages) for the record. M1's cell check is not used: it models
        snappyHexMesh (about 2 kB a cell; on the owner's 23 M machine 46 GB,
        BLOCKED, against the 18 GB the assembly took in E0). Each heavy step is
        checked against its own measured model instead (_step_preflight)."""
        cells = self._declared_cells(plan)
        path = self.run.root / PREFLIGHT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "status": "PER_STEP" if cells is not None else "NOT_ASSESSED",
            "estimated_cells": cells, "basis": "the files' cells x their passages",
            "steps": STEP_RESOURCES}, indent=2) + "\n", encoding="utf-8")

    # --- the import ----------------------------------------------------------------------

    async def _import(self, plan: _Plan, force: bool) -> list[Path]:
        run = self.run
        rings = [await self._ring(part, info, digest, force)
                 for part, info, digest in self.files]
        case = run.root / "cases" / IMPORTED_CASE
        shutil.rmtree(case / POLY_MESH, ignore_errors=True)
        prefixes = [p.prefix for p, _, _ in self.files]
        index = await self._steps(parts.merge_steps(prefixes, IMPORTED_CASE), 1)
        OpenFOAMFileWriter.write_if_changed(case / PART_ZONES_DICT, part_zones_dict(prefixes))
        await self._step(Step(f"{IMPORTED_CASE}_part_zones", "run", f"cases/{IMPORTED_CASE}",
                              ("topoSet", "-case", ".", "-dict", PART_ZONES_DICT)), index, None)
        shutil.rmtree(case / POLY_MESH / "sets", ignore_errors=True)
        sizes = parts.zone_sizes(case)
        short = {p: (sizes.get(p, 0), ring) for p, ring in zip(prefixes, rings, strict=True)
                 if sizes.get(p, 0) != ring}
        if short:
            shutil.rmtree(case / POLY_MESH, ignore_errors=True)
            raise _Stop("A part's cell zone does not hold all of its cells.", _issue(
                IssueSeverity.BLOCKING, IssueCategory.MESHING, IssueStage.MESH_IMPORT,
                "PART_ZONE_INCOMPLETE", "; ".join(
                    f"zone {p} holds {got:,} of the part's {ring:,} cells"
                    for p, (got, ring) in short.items()) + ".",
                "The part's cells are not all in its file's cell zones: give every cell a "
                "zone in the program that wrote the mesh.", zones=short))
        cells = mesh_cells(case)
        if not has_mesh(case) or cells != sum(rings):
            shutil.rmtree(case / POLY_MESH, ignore_errors=True)
            raise _Stop("The parts were not merged into one mesh.", _issue(
                IssueSeverity.BLOCKING, IssueCategory.MESHING, IssueStage.MESH_IMPORT,
                "PARTS_MERGE_FAILED", f"The merged mesh has {cells} cells; the rings have "
                f"{sum(rings)}.", f"Read logs/{IMPORTED_CASE}/.", cells=cells,
                rings=rings))
        await self._surface(IMPORTED_CASE)
        return [*_mesh_files(run.root, IMPORTED_CASE), *surface.outputs(case)]

    async def _steps(self, steps: tuple[Step, ...], start: int) -> int:
        """Run the steps in order ("copy_case" and "remove" here, "run" through
        the pipeline); the next index."""
        root = self.run.root
        index = start
        for step in steps:
            target = root / step.case
            if step.kind == "copy_case":
                for relative, body in converter_files().items():  # a case's own are kept
                    if not (target / relative).is_file():
                        OpenFOAMFileWriter.write_if_changed(target / relative, body)
                shutil.rmtree(target / POLY_MESH, ignore_errors=True)
                shutil.copytree(root / step.source / POLY_MESH, target / POLY_MESH,
                                ignore=shutil.ignore_patterns(*_EXCLUDED))
            elif step.kind == "remove":
                shutil.rmtree(target, ignore_errors=True)
            else:
                await self._step(step, index, None)
            index += 1
        return index

    async def _convert(self, part: MeshPart, info: MeshFileInfo, digest: str,
                       force: bool) -> Path:
        """cases/part_<P>_convert/: the file at scale 1 with its surface, reused
        when its record is this file and converter (as M2a's scratch)."""
        name = parts.convert_case(part.prefix)
        folder = self.run.root / "cases" / name
        record = self._convert_record(part, info, digest)
        if not force and _converted(folder, record):
            self.run.reused.append(f"{IMPORT}:{name}")
            return folder
        shutil.rmtree(folder, ignore_errors=True)
        for relative, body in converter_files().items():
            OpenFOAMFileWriter.write_if_changed(folder / relative, body)
        for index, step in enumerate(convert_steps(info, part.source_path, name), start=1):
            await self._step(step, index, "convert" if step.name.endswith("_convert") else None)
        unit = part.source_units.value
        found = check_converted(folder, info, _no_zones(self.run.config), unit)
        if not [i for i in found if i.is_stopping]:
            await self._surface(name)
            found = check_converted(folder, info, _no_zones(self.run.config), unit)
        stopping = [replace(i, message=f"{part.name}: {i.message}") for i in found
                    if i.is_stopping]
        if stopping:
            shutil.rmtree(folder / POLY_MESH, ignore_errors=True)
            raise _Stop(f"The converted {part.name} mesh is not usable. Review logs/{name}/.",
                        *stopping)
        self.run.issues.extend(replace(i, message=f"{part.name}: {i.message}") for i in found)
        box = surface.mesh_box(folder)
        (folder / "scratch.json").write_text(json.dumps(
            {**record, "box": [list(box[0]), list(box[1])] if box else None}, indent=2) + "\n",
            encoding="utf-8")
        return folder

    def _periodicity(self, part: MeshPart, folder: Path) -> parts.Periodicity:
        """The passage count found again on the part's surface: it must be the
        confirmed one (the file or the axis may have changed since)."""
        found = surface.load(folder)
        origin, axis = parts.axis_point(self.machine)
        local = parts.in_file_units(origin, part.source_units.to_metres)  # at scale 1
        detected = parts.detect(found, local, axis) if found is not None else None
        confirmed = {frozenset((p.first, p.second)) for p in part.periodic}
        if detected is None or detected.problem or detected.passages != part.passages or \
                {frozenset((a, b)) for a, b, _ in detected.pairs} != confirmed:
            seen = ("no surface" if detected is None else detected.problem or
                    f"{detected.passages} passage(s), pairs "
                    + (", ".join(f"{a}/{b}" for a, b, _ in detected.pairs) or "none"))
            raise _Stop(f"{part.name}: the passage count is not the confirmed one.", _issue(
                IssueSeverity.BLOCKING, IssueCategory.MESHING, IssueStage.MESH_IMPORT,
                "PART_PASSAGES_CHANGED", f"{part.name}: confirmed {part.passages} passage(s) "
                "with pairs " + (", ".join(f"{p.first}/{p.second}" for p in part.periodic)
                                 or "none") + f"; found {seen} about the machine axis.",
                "Read the parts again and confirm what is found, or check the machine axis.",
                part=part.prefix))
        return detected

    async def _ring(self, part: MeshPart, info: MeshFileInfo, digest: str, force: bool) -> int:
        """cases/part_<P>/: the part's whole ring (parts.py); its cells."""
        run = self.run
        convert = await self._convert(part, info, digest, force)
        self._periodicity(part, convert)
        name = parts.part_case(part.prefix)
        folder = run.root / "cases" / name
        inputs = self._ring_inputs(part, info, digest)
        if not force and parts.ring_record(folder) == inputs and has_mesh(folder):
            run.reused.append(f"{IMPORT}:{name}")
            cells = mesh_cells(folder)
            assert cells is not None
            return cells
        shutil.rmtree(folder, ignore_errors=True)
        index = await self._steps((Step(f"{name}_copy", "copy_case", f"cases/{name}",
                                        source=f"cases/{parts.convert_case(part.prefix)}"),), 1)
        scale = scale_step(part.source_units.to_metres, name)
        if scale is not None:
            await self._step(scale, index, None)
            index += 1
        found, unreadable = read_patches(folder, name)
        if unreadable is not None:
            raise _Stop(f"The {part.name} mesh has no readable boundary.", unreadable)
        OpenFOAMFileWriter.write_if_changed(folder / "system/createPatchDict",
                                            parts.prefix_patch_dict(
                                                part.prefix,
                                                [(p.name, p.patch_type) for p in found]))
        dropped = sorted(face_zone_names(folder)) if self.machine.drop_face_zones else []
        OpenFOAMFileWriter.write_if_changed(folder / "system/topoSetDict", parts.prefix_zone_dict(
            part.prefix, sorted(mesh_zone_names(folder)), dropped))
        OpenFOAMFileWriter.write_if_changed(folder / parts.EMPTY_PATCH_DICT,
                                            empty_patch_dict())
        origin, axis = parts.axis_point(self.machine)
        stitches = parts.stitch_steps(part)
        index = await self._steps((
            Step(f"{name}_prefix_patches", "run", f"cases/{name}",
                 ("createPatch", "-overwrite", "-case", ".")),
            Step(f"{name}_prefix_zones", "run", f"cases/{name}", ("topoSet", "-case", ".")),
            *parts.copy_steps(part.prefix, part.passages or 1, origin, axis),
            *stitches, *((parts.drop_empty_step(part.prefix),) if stitches else ()),
            parts.check_step(part.prefix)), index)
        shutil.rmtree(folder / POLY_MESH / "sets", ignore_errors=True)
        self._prove(part, folder)
        cells = mesh_cells(folder)
        assert cells is not None
        (folder / parts.RING_RECORD).write_text(json.dumps(inputs, indent=2) + "\n",
                                                encoding="utf-8")
        return cells

    def _prove(self, part: MeshPart, folder: Path) -> None:
        """One region and no face left on a periodic patch (E0)."""
        name = parts.part_case(part.prefix)
        regions = region_count(_read(self.run.root / "logs" / name / CHECK_MESH_LOG))
        found, _ = read_patches(folder, name)
        faces = {p.name: p.n_faces for p in found}
        left = {n: faces.get(n, 0) for p in part.periodic for n in
                (parts.prefixed(part.prefix, p.first), parts.prefixed(part.prefix, p.second))
                if faces.get(n, 0)}
        if regions == 1 and not left:
            return
        raise _Stop(f"The {part.name} ring is not one closed mesh.", _issue(
            IssueSeverity.BLOCKING, IssueCategory.MESHING, IssueStage.MESH_IMPORT,
            "PART_RING_INVALID", f"{part.name}: {regions} region(s)"
            + ("; faces left on periodic patches: "
               + ", ".join(f"{n} {c}" for n, c in left.items()) if left else "")
            + ".",
            f"Read logs/{name}/ (stitchMesh and checkMesh). The copies meet at the periodic "
            "patches only when the passage count and the machine axis are right.",
            regions=regions, faces_left=left), )

    # --- the report ----------------------------------------------------------------------

    def _report(self, plan: _Plan) -> None:
        """reports/mesh_import.json for the machine: per part, its file, units,
        passages and ring; the merged mesh's patches, zones, seams, areas."""
        run = self.run
        case = run.root / "cases" / IMPORTED_CASE
        identity = tool_identity(run.config.openfoam_profile, self._import_executables(plan),
                                 self.pipe._env())
        patches, _ = read_patches(case, IMPORTED_CASE)
        radii = {z.name: surface.config_zone_radius(case, z) for z in self.mesh.zones}
        rows = []
        for part, info, digest in self.files:
            ring = run.root / "cases" / parts.part_case(part.prefix)
            rows.append({
                "name": part.name, "prefix": part.prefix, "source": str(part.source_path),
                "sha256": digest, "format": info.format.value if info.format else None,
                "converter": converter(info), "resaved_as_msh22": needs_resave(info),
                "units": part.source_units.value, "scale_to_metres": part.source_units.to_metres,
                "declared_cells": info.declared_cells, "passages": part.passages,
                "periodic": [[p.first, p.second] for p in part.periodic],
                "ring_cells": mesh_cells(ring), "seams": parts.seams(ring),
                "logs": [f"logs/{parts.convert_case(part.prefix)}/",
                         f"logs/{parts.part_case(part.prefix)}/"]})
        payload = {
            "run_id": run.run_id, "sha256": plan.digest, "parts": rows,
            "openfoam": {"project": identity["WM_PROJECT"],
                         "version": identity["WM_PROJECT_VERSION"],
                         "executables": identity["executables"]},
            "axis": self.machine.axis.value, "origin": list(parts.axis_point(self.machine)[0]),
            "cells": mesh_cells(case),
            "patches": {p.name: {"type": p.patch_type, "faces": p.n_faces} for p in patches},
            "names_used": used_names(run.config),
            "cell_zones": sorted(mesh_zone_names(case)), "seams": parts.seams(case),
            "zone_radius_m": radii, "zone_radius_note": "computed from the zone: the largest "
            "distance of its points from the rotation axis",
            "patch_area_m2": self.areas(), "patch_area_note": "measured from the mesh: the "
            "sum of the patch's face areas, after renaming and scaling",
            "estimate_note": resources.NOTE, "logs": f"logs/{IMPORTED_CASE}/"}
        path = run.root / IMPORT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _converted(folder: Path, record: dict[str, Any]) -> bool:
    """A conversion of this file by this converter, with its surface."""
    try:
        found = json.loads((folder / "scratch.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(found, dict):
        return False
    found.pop("box", None)
    return found == record and has_mesh(folder) and surface.load(folder) is not None


def _no_zones(config: Any) -> Any:
    """The configuration without rotating zones: a part's own file is checked
    for what it holds, not for the machine's zone <P> (made later)."""
    mesh = config.imported_mesh
    return config.model_copy(update={"imported_mesh": mesh.model_copy(update={"zones": ()})})

