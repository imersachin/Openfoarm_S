from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from core.artifacts import ArtifactStore
from core.artifacts.hashing import files_under, hash_files, sha256_file, sha256_json
from core.config.models import ProjectConfig
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from core.workflow.dependency_graph import PipelineOperation
from geometry.importer import import_stl
from geometry.transformer import ARTIFACT_FORMAT_VERSION, GeometryTransform, write_stl_artifact
from geometry.validator import GeometryReport, TrimeshGeometryValidator
from mesh.generator import FEATURE_DICT, OpenFOAMMeshCaseGenerator
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityValidator
from openfoam.commands import (
    CHECK_MESH_LOG,
    MeshingStep,
    block_mesh_step,
    check_mesh_step,
    feature_extraction_command,
    feature_extraction_step,
    snappy_step,
)
from openfoam.environment import tool_identity
from openfoam.runner import CommandResult, OpenFOAMRunner

Op = PipelineOperation

GEOMETRY_REPORT = "reports/geometry_report.json"
POLY_MESH = "constant/polyMesh"
# checkMesh may write problem face/point sets here; they are diagnostics, not
# part of the mesh, so they never make the mesh look changed.
_MESH_EXCLUDED_DIRS = frozenset({"sets"})


@dataclass(frozen=True)
class PipelineResult:
    geometry_report_path: Path
    mesh_report_path: Path | None
    succeeded: bool
    message: str
    issues: tuple[Issue, ...] = ()
    executed: tuple[PipelineOperation, ...] = ()
    reused: tuple[PipelineOperation, ...] = ()


class MeshPipeline:
    def __init__(
        self,
        runner: OpenFOAMRunner | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.geometry_validator = TrimeshGeometryValidator()
        self.case_generator = OpenFOAMMeshCaseGenerator()
        self.runner = runner or OpenFOAMRunner()
        self.checkmesh_parser = CheckMeshParser()
        self.mesh_validator = MeshQualityValidator()
        # None: use the process environment at run time.
        self.environment = environment

    def _env(self) -> Mapping[str, str]:
        return self.environment if self.environment is not None else os.environ

    # --- geometry and case files -------------------------------------------

    def _geometry_inputs(self, config: ProjectConfig) -> dict[str, str]:
        geometry = config.geometry
        return {
            "source": sha256_file(geometry.source_path),
            "config:geometry": sha256_json(geometry.model_dump(mode="json")),
            "artifact_format": str(ARTIFACT_FORMAT_VERSION),
            "validator": sha256_json(asdict(self.geometry_validator.dimension_limits)),
        }

    def prepare_case(
        self, root: Path, config: ProjectConfig, *, force: bool = False
    ) -> PipelineResult:
        """Import -> validate source -> transform -> write artifact -> re-validate -> case.

        The transformed artifact at constant/triSurface/<patch>.stl, never the
        source file, is what snappyHexMesh consumes. The geometry step is
        reused only when its recorded inputs and outputs verify.
        """
        store = ArtifactStore(root)
        load_issues = (store.load_issue,) if store.load_issue else ()
        geometry_path = root / GEOMETRY_REPORT
        target_stl = root / "constant" / "triSurface" / f"{config.geometry.patch_name}.stl"
        geometry_ops = (Op.IMPORT_GEOMETRY, Op.VALIDATE_GEOMETRY)

        inputs = self._geometry_inputs(config)
        cached = store.check(Op.IMPORT_GEOMETRY, inputs) if not force else None
        if cached is not None and cached.reusable and cached.record is not None:
            geometry_issues = tuple(
                Issue.from_dict(item) for item in cached.record.details.get("issues", [])
            )
            executed: tuple[PipelineOperation, ...] = ()
            reused: tuple[PipelineOperation, ...] = geometry_ops
        else:
            store.invalidate(Op.IMPORT_GEOMETRY)
            succeeded, message, geometry_issues = self._prepare_geometry(
                config, geometry_path, target_stl
            )
            if not succeeded:
                return PipelineResult(
                    geometry_report_path=geometry_path,
                    mesh_report_path=None,
                    succeeded=False,
                    message=message,
                    issues=(*load_issues, *geometry_issues),
                    executed=geometry_ops,
                )
            store.record(
                Op.IMPORT_GEOMETRY, inputs, [target_stl, geometry_path],
                details={"issues": [issue.as_dict() for issue in geometry_issues]},
            )
            executed, reused = geometry_ops, ()

        generated = self.case_generator.generate(root, config)
        issues = (*load_issues, *geometry_issues, *generated.issues)
        generation_ops = (
            Op.GENERATE_BLOCK_MESH_DICT, Op.GENERATE_FEATURE_DICT,
            Op.GENERATE_SNAPPY_DICT, Op.GENERATE_MESH_QUALITY_DICT,
        )
        succeeded = not has_stopping_issue(generated.issues)
        return PipelineResult(
            geometry_report_path=geometry_path,
            mesh_report_path=None,
            succeeded=succeeded,
            message="Case files are current and ready for mesh generation." if succeeded
            else "Case generation failed; mesh generation was not started.",
            issues=issues,
            executed=(*executed, *generation_ops),
            reused=reused,
        )

    def _prepare_geometry(
        self, config: ProjectConfig, report_path: Path, target_stl: Path
    ) -> tuple[bool, str, tuple[Issue, ...]]:
        source = config.geometry.source_path
        report: dict[str, Any] = {"source_path": str(source)}

        def finish(succeeded: bool, message: str, issues: tuple[Issue, ...]):
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return succeeded, message, issues

        imported = import_stl(source)
        if imported.mesh is None:
            report["source"] = GeometryReport(source=str(source), issues=imported.issues).as_dict()
            return finish(False, "Geometry import failed; mesh generation was not started.",
                          imported.issues)

        source_report = self.geometry_validator.validate_mesh(imported.mesh, source)
        report["source"] = source_report.as_dict()
        if has_stopping_issue(source_report.issues):
            return finish(False, "Geometry preflight failed; mesh generation was not started.",
                          source_report.issues)

        transform = GeometryTransform.from_config(config.geometry)
        write_stl_artifact(
            transform.apply_to_mesh(imported.mesh), target_stl, config.geometry.patch_name
        )
        report["transformation"] = transform.as_dict()
        report["artifact_path"] = str(target_stl)

        # Re-validate the artifact as written to disk: this is what meshing consumes.
        artifact_report = self.geometry_validator.validate(
            target_stl, check_dimensions_in_metres=True
        )
        report["artifact"] = artifact_report.as_dict()
        if has_stopping_issue(artifact_report.issues):
            return finish(False, "Transformed geometry failed validation; mesh generation "
                          "was not started.", artifact_report.issues)
        return finish(True, "Geometry artifact is current.", artifact_report.issues)

    # --- OpenFOAM execution -------------------------------------------------

    def _tool(self, config: ProjectConfig, *executables: str) -> str:
        return sha256_json(tool_identity(config.openfoam_profile, executables, self._env()))

    async def _run(self, step: MeshingStep, root: Path) -> CommandResult:
        return await self.runner.run_step(step, case_root=root, env=self.environment)

    async def generate_mesh(
        self,
        root: Path,
        config: ProjectConfig,
        feature_command: tuple[str, ...] | None = None,
        *,
        force: bool = False,
    ) -> PipelineResult:
        """Run only the meshing steps whose verified artifacts are not reusable.

        feature_command overrides the profile's feature-extraction command.
        force=True ignores every cached artifact.
        """
        prepared = self.prepare_case(root, config, force=force)
        if not prepared.succeeded:
            return prepared

        # Feature extraction runs only when the configuration requires an .eMesh.
        requires_features = config.mesh.surface.extract_features
        if requires_features and feature_command is None:
            feature_command = feature_extraction_command(config.openfoam_profile, root)
        if requires_features and not feature_command:
            return replace(
                prepared,
                succeeded=False,
                message="Feature extraction is required but unavailable; meshing was not started.",
                issues=(*prepared.issues, Issue(
                    category=IssueCategory.DEPENDENCY,
                    severity=IssueSeverity.BLOCKING,
                    stage=IssueStage.FEATURE_EXTRACTION,
                    code="FEATURE_EXTRACTION_UNAVAILABLE",
                    message="snappyHexMesh requires an .eMesh, but no feature extraction "
                    "command is configured for the selected OpenFOAM profile.",
                    explanation="mesh.surface.extract_features is enabled, so the generated "
                    "snappyHexMeshDict references an edge mesh that must be produced first.",
                    suggested_action="Disable extract_features or configure feature extraction "
                    "for the installed OpenFOAM profile.",
                    details={"openfoam_profile": config.openfoam_profile.value},
                )),
            )

        store = ArtifactStore(root)
        executed = list(prepared.executed)
        reused = list(prepared.reused)
        patch = config.geometry.patch_name
        stl = root / "constant" / "triSurface" / f"{patch}.stl"
        emesh = root / "constant" / "triSurface" / f"{patch}.eMesh"

        def failed(message: str, *issues: Issue) -> PipelineResult:
            return PipelineResult(
                geometry_report_path=prepared.geometry_report_path,
                mesh_report_path=None,
                succeeded=False,
                message=message,
                issues=(*prepared.issues, *issues),
                executed=tuple(executed),
                reused=tuple(reused),
            )

        def missing_output(code: str, stage: IssueStage, what: str, log: str) -> Issue:
            return Issue(
                category=IssueCategory.EXECUTION,
                severity=IssueSeverity.ERROR,
                stage=stage,
                code=code,
                message=f"The command reported success but produced no {what}.",
                explanation="The expected output file(s) are missing after the run.",
                suggested_action="Inspect the command log and the OpenFOAM profile.",
                log_reference=str(root / "logs" / log),
            )

        # 1. Feature extraction (conditional).
        if requires_features:
            assert feature_command is not None
            step = feature_extraction_step(feature_command)
            inputs = {
                **hash_files(root, [root / FEATURE_DICT, root / "system/controlDict", stl]),
                "tool": self._tool(config, step.argv[0]),
            }
            if not force and store.check(Op.EXTRACT_FEATURES, inputs).reusable:
                reused.append(Op.EXTRACT_FEATURES)
            else:
                store.invalidate(Op.EXTRACT_FEATURES)
                executed.append(Op.EXTRACT_FEATURES)
                result = await self._run(step, root)
                if not result.succeeded:
                    return failed("Feature extraction failed. Review logs/.", *result.issues)
                if not emesh.is_file():
                    return failed("Feature extraction produced no .eMesh.", missing_output(
                        "FEATURE_OUTPUT_MISSING", step.stage, f"{emesh.name}", step.log_name
                    ))
                store.record(Op.EXTRACT_FEATURES, inputs, [emesh])

        # 2. Background mesh + snappyHexMesh as one unit: snappy -overwrite
        #    replaces the background mesh in place.
        mesh_inputs_files = [
            root / "system/controlDict", root / "system/blockMeshDict",
            root / "system/snappyHexMeshDict", root / "system/meshQualityDict", stl,
        ]
        if requires_features:
            mesh_inputs_files.append(emesh)
        mesh_inputs = {
            **hash_files(root, mesh_inputs_files),
            "tool": self._tool(config, "blockMesh", "snappyHexMesh"),
        }
        if not force and store.check(Op.GENERATE_MESH, mesh_inputs).reusable:
            reused.extend((Op.GENERATE_BACKGROUND_MESH, Op.GENERATE_MESH))
        else:
            store.invalidate(Op.GENERATE_MESH, Op.CHECK_MESH)
            for operation, step in (
                (Op.GENERATE_BACKGROUND_MESH, block_mesh_step(root)),
                (Op.GENERATE_MESH, snappy_step(root)),
            ):
                executed.append(operation)
                result = await self._run(step, root)
                if not result.succeeded:
                    return failed("Meshing failed. Review logs/ for the failing OpenFOAM "
                                  "command.", *result.issues)
            mesh_files = files_under(root / POLY_MESH, exclude_dirs=_MESH_EXCLUDED_DIRS)
            if not mesh_files:
                return failed("snappyHexMesh produced no mesh.", missing_output(
                    "MESH_OUTPUT_MISSING", IssueStage.SNAPPY_HEX_MESH, "polyMesh files",
                    snappy_step(root).log_name,
                ))
            store.record(Op.GENERATE_MESH, mesh_inputs, mesh_files)

        # 3. checkMesh on the current mesh files.
        log = root / "logs" / CHECK_MESH_LOG
        check_inputs = {
            **hash_files(root, files_under(root / POLY_MESH, exclude_dirs=_MESH_EXCLUDED_DIRS)),
            "tool": self._tool(config, "checkMesh"),
        }
        if not force and store.check(Op.CHECK_MESH, check_inputs).reusable:
            reused.append(Op.CHECK_MESH)
        else:
            store.invalidate(Op.CHECK_MESH)
            executed.append(Op.CHECK_MESH)
            result = await self._run(check_mesh_step(root), root)
            if not result.succeeded:
                return failed("checkMesh failed. Review logs/.", *result.issues)
            store.record(Op.CHECK_MESH, check_inputs, [log])

        # 4. Validation against acceptance limits: cheap, always re-run.
        executed.append(Op.VALIDATE_MESH)
        metrics = self.checkmesh_parser.parse_file(log)
        report = self.mesh_validator.assess(metrics, config.mesh.quality)

        mesh_report_path = root / "reports" / "mesh_quality_report.json"
        mesh_report_path.write_text(
            json.dumps(report.as_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

        return PipelineResult(
            geometry_report_path=prepared.geometry_report_path,
            mesh_report_path=mesh_report_path,
            succeeded=report.status != "failed",
            message=f"Mesh validation completed with status: {report.status}.",
            issues=(*prepared.issues, *report.issues),
            executed=tuple(executed),
            reused=tuple(reused),
        )

    def generate_mesh_sync(
        self,
        root: Path,
        config: ProjectConfig,
        feature_command: tuple[str, ...] | None = None,
        *,
        force: bool = False,
    ) -> PipelineResult:
        return asyncio.run(self.generate_mesh(root, config, feature_command, force=force))
