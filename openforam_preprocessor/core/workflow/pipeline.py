from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from core.config.models import ProjectConfig
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage, has_stopping_issue
from geometry.validator import TrimeshGeometryValidator
from mesh.generator import OpenFOAMMeshCaseGenerator
from mesh.parser import CheckMeshParser
from mesh.validator import MeshQualityValidator
from openfoam.runner import OpenFOAMRunner


@dataclass(frozen=True)
class PipelineResult:
    geometry_report_path: Path
    mesh_report_path: Path | None
    succeeded: bool
    message: str
    issues: tuple[Issue, ...] = ()


class MeshPipeline:
    def __init__(self, runner: OpenFOAMRunner | None = None) -> None:
        self.geometry_validator = TrimeshGeometryValidator()
        self.case_generator = OpenFOAMMeshCaseGenerator()
        self.runner = runner or OpenFOAMRunner()
        self.checkmesh_parser = CheckMeshParser()
        self.mesh_validator = MeshQualityValidator()

    def prepare_case(self, root: Path, config: ProjectConfig) -> PipelineResult:
        source = config.geometry.source_path
        tri_surface = root / "constant" / "triSurface"
        tri_surface.mkdir(parents=True, exist_ok=True)

        target_stl = tri_surface / f"{config.geometry.patch_name}.stl"
        if source.is_file():
            if not target_stl.exists() or (
                target_stl.stat().st_mtime_ns < source.stat().st_mtime_ns
            ):
                shutil.copy2(source, target_stl)
            geometry_report = self.geometry_validator.validate(target_stl)
        else:
            # Report against the configured source; never mesh a stale project copy.
            geometry_report = self.geometry_validator.validate(source)

        reports_dir = root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        geometry_path = reports_dir / "geometry_report.json"
        geometry_path.write_text(
            json.dumps(geometry_report.as_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

        if has_stopping_issue(geometry_report.issues):
            return PipelineResult(
                geometry_report_path=geometry_path,
                mesh_report_path=None,
                succeeded=False,
                message="Geometry preflight failed; mesh generation was not started.",
                issues=geometry_report.issues,
            )

        self.case_generator.generate(root, config)
        return PipelineResult(
            geometry_report_path=geometry_path,
            mesh_report_path=None,
            succeeded=True,
            message="Case files are current and ready for mesh generation.",
            issues=geometry_report.issues,
        )

    async def generate_mesh(
        self,
        root: Path,
        config: ProjectConfig,
        feature_command: tuple[str, ...] | None,
    ) -> PipelineResult:
        prepared = self.prepare_case(root, config)
        if not prepared.succeeded:
            return prepared

        # Feature extraction runs only when the configuration requires an .eMesh.
        requires_features = config.mesh.surface.extract_features
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

        commands = await self.runner.run_meshing_pipeline(
            case_root=root,
            extract_features_argv=feature_command if requires_features else None,
        )
        if not commands or not commands[-1].succeeded:
            command_issues = commands[-1].issues if commands else ()
            return PipelineResult(
                geometry_report_path=prepared.geometry_report_path,
                mesh_report_path=None,
                succeeded=False,
                message="Meshing failed. Review logs/ for the failing OpenFOAM command.",
                issues=(*prepared.issues, *command_issues),
            )

        checkmesh_log = root / "logs" / "04_checkMesh.log"
        metrics = self.checkmesh_parser.parse_file(checkmesh_log)
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
        )

    def generate_mesh_sync(
        self,
        root: Path,
        config: ProjectConfig,
        feature_command: tuple[str, ...] | None,
    ) -> PipelineResult:
        return asyncio.run(self.generate_mesh(root, config, feature_command))
