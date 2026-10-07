from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum


class PipelineOperation(StrEnum):
    """Pipeline operations. Declaration order is a valid execution order."""

    IMPORT_GEOMETRY = "import_geometry"  # import + units + transform -> artifact
    VALIDATE_GEOMETRY = "validate_geometry"
    GENERATE_BLOCK_MESH_DICT = "generate_block_mesh_dict"
    GENERATE_FEATURE_DICT = "generate_feature_dict"
    GENERATE_SNAPPY_DICT = "generate_snappy_dict"
    GENERATE_MESH_QUALITY_DICT = "generate_mesh_quality_dict"
    GENERATE_BACKGROUND_MESH = "generate_background_mesh"  # blockMesh
    EXTRACT_FEATURES = "extract_features"  # surfaceFeatureExtract
    GENERATE_MESH = "generate_mesh"  # snappyHexMesh
    CHECK_MESH = "check_mesh"  # checkMesh
    VALIDATE_MESH = "validate_mesh"  # checkMesh results vs. acceptance limits


_Op = PipelineOperation

# Data flow: an operation's outputs are consumed by these operations.
DOWNSTREAM: Mapping[PipelineOperation, frozenset[PipelineOperation]] = {
    _Op.IMPORT_GEOMETRY: frozenset({_Op.VALIDATE_GEOMETRY}),
    _Op.VALIDATE_GEOMETRY: frozenset({_Op.EXTRACT_FEATURES, _Op.GENERATE_MESH}),
    _Op.GENERATE_BLOCK_MESH_DICT: frozenset({_Op.GENERATE_BACKGROUND_MESH}),
    _Op.GENERATE_FEATURE_DICT: frozenset({_Op.EXTRACT_FEATURES}),
    _Op.GENERATE_SNAPPY_DICT: frozenset({_Op.GENERATE_MESH}),
    _Op.GENERATE_MESH_QUALITY_DICT: frozenset({_Op.GENERATE_MESH}),
    _Op.GENERATE_BACKGROUND_MESH: frozenset({_Op.GENERATE_MESH}),
    _Op.EXTRACT_FEATURES: frozenset({_Op.GENERATE_MESH}),
    _Op.GENERATE_MESH: frozenset({_Op.CHECK_MESH}),
    _Op.CHECK_MESH: frozenset({_Op.VALIDATE_MESH}),
    _Op.VALIDATE_MESH: frozenset(),
}

# Operations that consume another operation's output in place. snappyHexMesh
# runs with -overwrite, replacing the blockMesh background mesh in
# constant/polyMesh, so any snappy re-run needs a fresh background mesh even
# when blockMeshDict is unchanged.
REQUIRES_FRESH: Mapping[PipelineOperation, frozenset[PipelineOperation]] = {
    _Op.GENERATE_MESH: frozenset({_Op.GENERATE_BACKGROUND_MESH}),
}

_GEOMETRY = frozenset({_Op.IMPORT_GEOMETRY})

# Configuration path -> operations that consume it directly. Downstream
# staleness follows from DOWNSTREAM/REQUIRES_FRESH. Every configuration
# field must be covered; unmapped paths are treated conservatively.
DIRECT_CONSUMERS: Mapping[str, frozenset[PipelineOperation]] = {
    "schema_version": frozenset(),
    "project_name": frozenset(),
    # A different OpenFOAM installation may produce different meshes and uses
    # a profile-specific feature-extraction workflow.
    "openfoam_profile": frozenset({
        _Op.GENERATE_FEATURE_DICT,
        _Op.GENERATE_BACKGROUND_MESH,
        _Op.EXTRACT_FEATURES,
        _Op.GENERATE_MESH,
        _Op.CHECK_MESH,
    }),
    "geometry.source_path": _GEOMETRY,
    "geometry.source_units": _GEOMETRY,
    "geometry.scale": _GEOMETRY,
    "geometry.rotation_deg": _GEOMETRY,
    "geometry.translation": _GEOMETRY,
    # The patch name is the artifact file name and is referenced by dictionaries.
    "geometry.patch_name": frozenset({
        _Op.IMPORT_GEOMETRY, _Op.GENERATE_FEATURE_DICT, _Op.GENERATE_SNAPPY_DICT,
    }),
    "mesh.background": frozenset({_Op.GENERATE_BLOCK_MESH_DICT}),
    "mesh.surface.minimum_level": frozenset({_Op.GENERATE_SNAPPY_DICT}),
    "mesh.surface.maximum_level": frozenset({_Op.GENERATE_SNAPPY_DICT}),
    "mesh.surface.feature_refinement_level": frozenset({_Op.GENERATE_SNAPPY_DICT}),
    # resolveFeatureAngle (snappy) and includedAngle (feature extraction).
    "mesh.surface.feature_angle_deg": frozenset({
        _Op.GENERATE_FEATURE_DICT, _Op.GENERATE_SNAPPY_DICT,
    }),
    "mesh.surface.extract_features": frozenset({
        _Op.GENERATE_FEATURE_DICT, _Op.GENERATE_SNAPPY_DICT,
    }),
    "mesh.layers": frozenset({_Op.GENERATE_SNAPPY_DICT}),
    "mesh.snappy_quality": frozenset({_Op.GENERATE_MESH_QUALITY_DICT}),
    "mesh.quality": frozenset({_Op.VALIDATE_MESH}),
    "mesh.location_in_mesh": frozenset({_Op.GENERATE_SNAPPY_DICT}),
    "mesh.max_global_cells": frozenset({_Op.GENERATE_SNAPPY_DICT}),
    # Not consumed by any operation yet.
    "mesh.overwrite_existing_mesh": frozenset(),
}


def _matches(path: str, rule: str) -> bool:
    # A rule matches its own path, any nested field, and any parent path
    # (a parent-level change, e.g. a replaced sub-model, covers the rule).
    return path == rule or path.startswith(f"{rule}.") or rule.startswith(f"{path}.")


class DependencyGraph:
    """Maps changed configuration paths to the operations that become stale."""

    def direct_consumers(self, path: str) -> frozenset[PipelineOperation] | None:
        """Operations reading this path directly, or None if the path is unmapped."""
        matched = [ops for rule, ops in DIRECT_CONSUMERS.items() if _matches(path, rule)]
        if not matched:
            return None
        return frozenset().union(*matched)

    def unmapped(self, changed_paths: Iterable[str]) -> frozenset[str]:
        return frozenset(p for p in changed_paths if self.direct_consumers(p) is None)

    @staticmethod
    def closure(operations: Iterable[PipelineOperation]) -> frozenset[PipelineOperation]:
        """The operations plus everything downstream (including in-place re-runs)."""
        seen: set[PipelineOperation] = set()
        stack = list(operations)
        while stack:
            operation = stack.pop()
            if operation in seen:
                continue
            seen.add(operation)
            stack.extend(DOWNSTREAM[operation])
            stack.extend(REQUIRES_FRESH.get(operation, ()))
        return frozenset(seen)

    def stale_by_path(
        self, changed_paths: Iterable[str]
    ) -> dict[PipelineOperation, frozenset[str]]:
        """Each stale operation with the changed paths that made it stale.

        Unmapped paths conservatively invalidate every operation.
        """
        reasons: dict[PipelineOperation, set[str]] = {}
        for path in sorted(changed_paths):
            direct = self.direct_consumers(path)
            stale = self.closure(PipelineOperation if direct is None else direct)
            for operation in stale:
                reasons.setdefault(operation, set()).add(path)
        return {op: frozenset(reasons[op]) for op in PipelineOperation if op in reasons}

    def operations_for(self, changed_paths: frozenset[str]) -> frozenset[PipelineOperation]:
        return frozenset(self.stale_by_path(changed_paths))
