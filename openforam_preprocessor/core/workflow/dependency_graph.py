from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Generic, TypeVar, overload


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


OpT = TypeVar("OpT", bound=StrEnum)


class DependencyGraph(Generic[OpT]):
    """Maps changed configuration paths to the operations that become stale.

    With no arguments it uses this module's tables (the generic workflow).
    Another workflow passes its own operations (in execution order) and
    tables; the rules are the same.
    """

    @overload
    def __init__(self: DependencyGraph[PipelineOperation]) -> None: ...

    @overload
    def __init__(
        self,
        *,
        operations: Sequence[OpT],
        downstream: Mapping[OpT, frozenset[OpT]],
        direct_consumers: Mapping[str, frozenset[OpT]],
        requires_fresh: Mapping[OpT, frozenset[OpT]] | None = None,
    ) -> None: ...

    def __init__(
        self,
        *,
        operations: Sequence[OpT] | None = None,
        downstream: Mapping[OpT, frozenset[OpT]] | None = None,
        direct_consumers: Mapping[str, frozenset[OpT]] | None = None,
        requires_fresh: Mapping[OpT, frozenset[OpT]] | None = None,
    ) -> None:
        if operations is None:
            self.operations: tuple[OpT, ...] = tuple(PipelineOperation)  # type: ignore[arg-type]
            self.downstream: Mapping[OpT, frozenset[OpT]] = DOWNSTREAM  # type: ignore[assignment]
            self.direct_consumer_rules: Mapping[str, frozenset[OpT]] = (
                DIRECT_CONSUMERS)  # type: ignore[assignment]
            self.requires_fresh: Mapping[OpT, frozenset[OpT]] = (
                REQUIRES_FRESH)  # type: ignore[assignment]
            return
        if downstream is None or direct_consumers is None:
            raise ValueError("A custom graph needs downstream and direct_consumers tables.")
        self.operations = tuple(operations)
        self.downstream = downstream
        self.direct_consumer_rules = direct_consumers
        self.requires_fresh = requires_fresh or {}

    def direct_consumers(self, path: str) -> frozenset[OpT] | None:
        """Operations reading this path directly, or None if the path is unmapped."""
        matched = [ops for rule, ops in self.direct_consumer_rules.items()
                   if _matches(path, rule)]
        if not matched:
            return None
        return frozenset().union(*matched)

    def unmapped(self, changed_paths: Iterable[str]) -> frozenset[str]:
        return frozenset(p for p in changed_paths if self.direct_consumers(p) is None)

    def closure(self, operations: Iterable[OpT]) -> frozenset[OpT]:
        """The operations plus everything downstream (including in-place re-runs)."""
        seen: set[OpT] = set()
        stack = list(operations)
        while stack:
            operation = stack.pop()
            if operation in seen:
                continue
            seen.add(operation)
            stack.extend(self.downstream[operation])
            stack.extend(self.requires_fresh.get(operation, ()))
        return frozenset(seen)

    def stale_by_path(self, changed_paths: Iterable[str]) -> dict[OpT, frozenset[str]]:
        """Each stale operation with the changed paths that made it stale.

        Unmapped paths conservatively invalidate every operation.
        """
        reasons: dict[OpT, set[str]] = {}
        for path in sorted(changed_paths):
            direct = self.direct_consumers(path)
            stale = self.closure(self.operations if direct is None else direct)
            for operation in stale:
                reasons.setdefault(operation, set()).add(path)
        return {op: frozenset(reasons[op]) for op in self.operations if op in reasons}

    def operations_for(self, changed_paths: frozenset[str]) -> frozenset[OpT]:
        return frozenset(self.stale_by_path(changed_paths))
