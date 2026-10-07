from __future__ import annotations

from enum import StrEnum


class PipelineOperation(StrEnum):
    IMPORT_GEOMETRY = "import_geometry"
    VALIDATE_GEOMETRY = "validate_geometry"
    GENERATE_BLOCK_MESH_DICT = "generate_block_mesh_dict"
    GENERATE_SNAPPY_DICT = "generate_snappy_dict"
    EXTRACT_FEATURES = "extract_features"
    GENERATE_MESH = "generate_mesh"
    VALIDATE_MESH = "validate_mesh"


class DependencyGraph:
    """Maps user-facing fields to the minimum required downstream work."""

    _rules: dict[str, frozenset[PipelineOperation]] = {
        "geometry.source_path": frozenset({
            PipelineOperation.IMPORT_GEOMETRY,
            PipelineOperation.VALIDATE_GEOMETRY,
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.EXTRACT_FEATURES,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "geometry.scale": frozenset({
            PipelineOperation.IMPORT_GEOMETRY,
            PipelineOperation.VALIDATE_GEOMETRY,
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.EXTRACT_FEATURES,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "geometry.rotation_deg": frozenset({
            PipelineOperation.IMPORT_GEOMETRY,
            PipelineOperation.VALIDATE_GEOMETRY,
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.EXTRACT_FEATURES,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "geometry.translation": frozenset({
            PipelineOperation.IMPORT_GEOMETRY,
            PipelineOperation.VALIDATE_GEOMETRY,
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.EXTRACT_FEATURES,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "mesh.background": frozenset({
            PipelineOperation.GENERATE_BLOCK_MESH_DICT,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "mesh.surface": frozenset({
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.EXTRACT_FEATURES,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "mesh.layers": frozenset({
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "mesh.quality": frozenset({
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "mesh.location_in_mesh": frozenset({
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
        "mesh.max_global_cells": frozenset({
            PipelineOperation.GENERATE_SNAPPY_DICT,
            PipelineOperation.GENERATE_MESH,
            PipelineOperation.VALIDATE_MESH,
        }),
    }

    def operations_for(self, changed_paths: frozenset[str]) -> frozenset[PipelineOperation]:
        operations: set[PipelineOperation] = set()

        for path in changed_paths:
            for root, dependent_operations in self._rules.items():
                if path == root or path.startswith(f"{root}."):
                    operations.update(dependent_operations)

        return frozenset(operations)
