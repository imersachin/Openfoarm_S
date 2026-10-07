from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
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
from mesh.estimator import (
    ResourceAssessment,
    ResourceEstimator,
    ResourceStatus,
    SystemResources,
)
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
from openfoam.environment import tool_identity, validate_environment
from openfoam.runner import CommandResult, OpenFOAMRunner

Op = PipelineOperation

GEOMETRY_REPORT = "reports/geometry_report.json"
PREFLIGHT_REPORT = "reports/resource_preflight.json"
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
        estimator: ResourceEstimator | None = None,
        system_probe: Callable[[Path], SystemResources] | None = None,
    ) -> None:
        self.geometry_validator = TrimeshGeometryValidator()
        self.case_generator = OpenFOAMMeshCaseGenerator()
        self.runner = runner or OpenFOAMRunner()
        self.checkmesh_parser = CheckMeshParser()
        self.mesh_validator = MeshQualityValidator()
        # None: use the process environment at run time.
        self.environment = environment
        self.estimator = estimator or ResourceEstimator()
        # Measures RAM/disk/CPU of this machine at run time; injectable for tests.
        self.system_probe = system_probe or SystemResources.detect

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

    def preflight(
        self, root: Path, config: ProjectConfig
    ) -> tuple[ResourceAssessment, tuple[Issue, ...]]:
        """Heuristic resource check before expensive meshing; writes a report."""
        extra: list[Issue] = []
        surface_area = 0.0
        try:
            report = json.loads((root / GEOMETRY_REPORT).read_text(encoding="utf-8"))
            surface_area = float(report["artifact"]["surface_area"])
        except (OSError, ValueError, KeyError, TypeError):
            extra.append(Issue(
                category=IssueCategory.RESOURCE_RISK,
                severity=IssueSeverity.WARNING,
                stage=IssueStage.RESOURCE_PREFLIGHT,
                code="SURFACE_AREA_UNKNOWN",
                message="Geometry surface area is unavailable; surface refinement cost was "
                "not included in the estimate.",
                suggested_action="Re-run geometry preparation.",
            ))
        assessment = self.estimator.assess(config, surface_area, self.system_probe(root))
        path = root / PREFLIGHT_REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(assessment.as_dict(), indent=2) + "\n", encoding="utf-8")
        return assessment, (*extra, *assessment.issues)

    async def generate_mesh(
        self,
        root: Path,
        config: ProjectConfig,
        feature_command: tuple[str, ...] | None = None,
        *,
        force: bool = False,
        allow_high_resource_risk: bool = False,
    ) -> PipelineResult:
        """Run only the meshing steps whose verified artifacts are not reusable.

        Before any OpenFOAM command runs, the environment is checked for the
        steps that may run, and a resource preflight runs if meshing may run.
        BLOCKED always stops; HIGH RESOURCE RISK stops unless
        allow_high_resource_risk is True.

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
        notes: list[Issue] = []  # non-stopping environment/preflight findings
        patch = config.geometry.patch_name
        stl = root / "constant" / "triSurface" / f"{patch}.stl"
        emesh = root / "constant" / "triSurface" / f"{patch}.eMesh"

        def failed(message: str, *issues: Issue) -> PipelineResult:
            return PipelineResult(
                geometry_report_path=prepared.geometry_report_path,
                mesh_report_path=None,
                succeeded=False,
                message=message,
                issues=(*prepared.issues, *notes, *issues),
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

        def mesh_inputs() -> dict[str, str]:
            files = [
                root / "system/controlDict", root / "system/blockMeshDict",
                root / "system/snappyHexMeshDict", root / "system/meshQualityDict", stl,
            ]
            if requires_features:
                files.append(emesh)
            return {
                **hash_files(root, files),
                "tool": self._tool(config, "blockMesh", "snappyHexMesh"),
            }

        def check_inputs() -> dict[str, str]:
            mesh_files = files_under(root / POLY_MESH, exclude_dirs=_MESH_EXCLUDED_DIRS)
            return {**hash_files(root, mesh_files), "tool": self._tool(config, "checkMesh")}

        # Decide up front which steps may run (cached steps verify first).
        feature_step = feature_extraction_step(feature_command) if feature_command else None
        feature_inputs: dict[str, str] = {}
        features_run = False
        if requires_features:
            assert feature_step is not None
            feature_inputs = {
                **hash_files(root, [root / FEATURE_DICT, root / "system/controlDict", stl]),
                "tool": self._tool(config, feature_step.argv[0]),
            }
            features_run = force or not store.check(Op.EXTRACT_FEATURES, feature_inputs).reusable
        mesh_may_run = (
            force or features_run or not store.check(Op.GENERATE_MESH, mesh_inputs()).reusable
        )
        check_may_run = mesh_may_run or not store.check(Op.CHECK_MESH, check_inputs()).reusable

        # Environment check for every executable that may run.
        executables = [
            *([feature_step.argv[0]] if features_run and feature_step else []),
            *(["blockMesh", "snappyHexMesh"] if mesh_may_run else []),
            *(["checkMesh"] if check_may_run else []),
        ]
        if executables:
            environment_issues = validate_environment(
                config.openfoam_profile, executables, self._env()
            )
            if has_stopping_issue(environment_issues):
                return failed("The OpenFOAM environment is not usable; nothing was run.",
                              *environment_issues)
            notes.extend(environment_issues)

        # Resource preflight before expensive meshing.
        if mesh_may_run:
            assessment, preflight_issues = self.preflight(root, config)
            if assessment.status is ResourceStatus.BLOCKED:
                return failed("Resource preflight BLOCKED meshing.", *preflight_issues)
            if (assessment.status is ResourceStatus.HIGH_RESOURCE_RISK
                    and not allow_high_resource_risk):
                return failed(
                    "Resource preflight reported HIGH RESOURCE RISK; meshing was not started.",
                    *preflight_issues,
                    Issue(
                        category=IssueCategory.RESOURCE_RISK,
                        severity=IssueSeverity.BLOCKING,
                        stage=IssueStage.RESOURCE_PREFLIGHT,
                        code="HIGH_RESOURCE_RISK_NOT_ACKNOWLEDGED",
                        message="Meshing needs explicit acknowledgement of the high "
                        "resource risk.",
                        suggested_action="Reduce the mesh settings, or re-run with "
                        "allow_high_resource_risk=True to proceed anyway.",
                    ),
                )
            notes.extend(preflight_issues)

        # 1. Feature extraction (conditional).
        if requires_features:
            assert feature_step is not None
            if not features_run:
                reused.append(Op.EXTRACT_FEATURES)
            else:
                store.invalidate(Op.EXTRACT_FEATURES)
                executed.append(Op.EXTRACT_FEATURES)
                result = await self._run(feature_step, root)
                if not result.succeeded:
                    return failed("Feature extraction failed. Review logs/.", *result.issues)
                if not emesh.is_file():
                    return failed("Feature extraction produced no .eMesh.", missing_output(
                        "FEATURE_OUTPUT_MISSING", feature_step.stage, f"{emesh.name}",
                        feature_step.log_name,
                    ))
                store.record(Op.EXTRACT_FEATURES, feature_inputs, [emesh])

        # 2. Background mesh + snappyHexMesh as one unit: snappy -overwrite
        #    replaces the background mesh in place. Re-verify here: feature
        #    extraction output is a mesh input.
        current_mesh_inputs = mesh_inputs()
        if not force and store.check(Op.GENERATE_MESH, current_mesh_inputs).reusable:
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
            store.record(Op.GENERATE_MESH, current_mesh_inputs, mesh_files)

        # 3. checkMesh on the current mesh files.
        log = root / "logs" / CHECK_MESH_LOG
        current_check_inputs = check_inputs()
        if not force and store.check(Op.CHECK_MESH, current_check_inputs).reusable:
            reused.append(Op.CHECK_MESH)
        else:
            store.invalidate(Op.CHECK_MESH)
            executed.append(Op.CHECK_MESH)
            result = await self._run(check_mesh_step(root), root)
            if not result.succeeded:
                return failed("checkMesh failed. Review logs/.", *result.issues)
            store.record(Op.CHECK_MESH, current_check_inputs, [log])

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
            issues=(*prepared.issues, *notes, *report.issues),
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
        allow_high_resource_risk: bool = False,
    ) -> PipelineResult:
        return asyncio.run(self.generate_mesh(
            root, config, feature_command, force=force,
            allow_high_resource_risk=allow_high_resource_risk,
        ))
