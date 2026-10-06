from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from core.config.models import ProjectConfig
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


class MeshPipeline:
    def __init__(self) -> None:
        self.geometry_validator = TrimeshGeometryValidator()
        self.case_generator = OpenFOAMMeshCaseGenerator()
        self.runner = OpenFOAMRunner()
        self.checkmesh_parser = CheckMeshParser()
        self.mesh_validator = MeshQualityValidator()

    def prepare_case(self, root: Path, config: ProjectConfig) -> PipelineResult:
        tri_surface = root / "constant" / "triSurface"
        tri_surface.mkdir(parents=True, exist_ok=True)

        target_stl = tri_surface / f"{config.geometry.patch_name}.stl"
        if not target_stl.exists() or (
            target_stl.stat().st_mtime_ns < config.geometry.source_path.stat().st_mtime_ns
        ):
            shutil.copy2(config.geometry.source_path, target_stl)

        geometry_report = self.geometry_validator.validate(target_stl)
        reports_dir = root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        geometry_path = reports_dir / "geometry_report.json"
        geometry_path.write_text(
            json.dumps(geometry_report.as_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

        if any(issue.severity == "error" for issue in geometry_report.issues):
            return PipelineResult(
                geometry_report_path=geometry_path,
                mesh_report_path=None,
                succeeded=False,
                message="Geometry preflight failed; mesh generation was not started.",
            )

        self.case_generator.generate(root, config)
        return PipelineResult(
            geometry_report_path=geometry_path,
            mesh_report_path=None,
            succeeded=True,
            message="Case files are current and ready for mesh generation.",
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

        commands = await self.runner.run_meshing_pipeline(
            case_root=root,
            extract_features_argv=feature_command,
        )
        if not commands or commands[-1].return_code != 0:
            return PipelineResult(
                geometry_report_path=prepared.geometry_report_path,
                mesh_report_path=None,
                succeeded=False,
                message="Meshing failed. Review logs/ for the failing OpenFOAM command.",
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
        )

    def generate_mesh_sync(
        self,
        root: Path,
        config: ProjectConfig,
        feature_command: tuple[str, ...] | None,
    ) -> PipelineResult:
        return asyncio.run(self.generate_mesh(root, config, feature_command))
