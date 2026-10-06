from pathlib import Path

from core.config.manager import ConfigurationManager
from core.config.models import (
    BackgroundMeshConfig,
    Bounds,
    GeometryConfig,
    MeshConfig,
    ProjectConfig,
    Vector3,
)
from core.workflow.dependency_graph import DependencyGraph, PipelineOperation


def make_config(refinement: int = 3) -> ProjectConfig:
    return ProjectConfig(
        project_name="Demo",
        geometry=GeometryConfig(source_path=Path("/tmp/part.stl")),
        mesh=MeshConfig(
            background=BackgroundMeshConfig(
                domain=Bounds(
                    minimum=Vector3(x=-2, y=-2, z=-2),
                    maximum=Vector3(x=2, y=2, z=2),
                ),
                base_cell_size=0.2,
            ),
            location_in_mesh=Vector3(x=1.5, y=0, z=0),
            surface={"minimum_level": 2, "maximum_level": refinement},
        ),
    )


def test_surface_refinement_requires_snappy_regeneration(tmp_path: Path) -> None:
    manager = ConfigurationManager(tmp_path, DependencyGraph())

    changes = manager.detect_changes(make_config(2), make_config(3))

    assert "mesh.surface.maximum_level" in changes.changed_paths
    assert PipelineOperation.GENERATE_SNAPPY_DICT in changes.affected_operations
    assert PipelineOperation.GENERATE_MESH in changes.affected_operations
    assert PipelineOperation.GENERATE_BLOCK_MESH_DICT not in changes.affected_operations
