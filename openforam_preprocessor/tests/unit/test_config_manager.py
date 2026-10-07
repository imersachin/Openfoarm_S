from pathlib import Path

from core.config.manager import ConfigurationManager
from core.config.models import (
    BackgroundMeshConfig,
    Bounds,
    GeometryConfig,
    LengthUnit,
    MeshConfig,
    ProjectConfig,
    Vector3,
)
from core.workflow.dependency_graph import DependencyGraph, PipelineOperation


def make_config(refinement: int = 3) -> ProjectConfig:
    return ProjectConfig(
        project_name="Demo",
        geometry=GeometryConfig(source_path=Path("/tmp/part.stl"), source_units="mm"),
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


def test_translation_change_invalidates_geometry_chain_not_background(tmp_path: Path) -> None:
    manager = ConfigurationManager(tmp_path, DependencyGraph())
    old = make_config()
    new = old.model_copy(update={
        "geometry": old.geometry.model_copy(update={"translation": Vector3(x=1, y=0, z=0)}),
    })

    affected = manager.detect_changes(old, new).affected_operations

    assert {
        PipelineOperation.IMPORT_GEOMETRY,
        PipelineOperation.VALIDATE_GEOMETRY,
        PipelineOperation.EXTRACT_FEATURES,
        PipelineOperation.GENERATE_MESH,
        PipelineOperation.VALIDATE_MESH,
    } <= affected
    assert PipelineOperation.GENERATE_BLOCK_MESH_DICT not in affected


def test_source_units_change_invalidates_geometry_chain(tmp_path: Path) -> None:
    manager = ConfigurationManager(tmp_path, DependencyGraph())
    old = make_config()
    new = old.model_copy(update={
        "geometry": old.geometry.model_copy(update={"source_units": LengthUnit.METRE}),
    })

    changes = manager.detect_changes(old, new)

    assert changes.changed_paths == frozenset({"geometry.source_units"})
    assert PipelineOperation.IMPORT_GEOMETRY in changes.affected_operations
    assert PipelineOperation.GENERATE_MESH in changes.affected_operations
    assert PipelineOperation.GENERATE_BLOCK_MESH_DICT not in changes.affected_operations
