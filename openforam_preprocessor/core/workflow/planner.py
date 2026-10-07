from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config.manager import ChangeSet
from core.config.models import ProjectConfig
from core.workflow.dependency_graph import DependencyGraph, PipelineOperation

INITIAL_BUILD = "<initial>"


@dataclass(frozen=True)
class ExecutionPlan:
    """Stale operations in execution order, with the cause of each.

    operations: stale and applicable to the new configuration, ordered.
    skipped: stale but not applicable (e.g. feature extraction disabled).
    reasons: changed configuration paths behind each planned/skipped operation.
    unmapped_paths: changed paths with no dependency rule; these were
        treated conservatively as invalidating everything.
    """

    operations: tuple[PipelineOperation, ...]
    skipped: tuple[PipelineOperation, ...]
    reasons: dict[PipelineOperation, frozenset[str]]
    unmapped_paths: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not self.operations

    def as_dict(self) -> dict[str, Any]:
        return {
            "operations": [op.value for op in self.operations],
            "skipped": [op.value for op in self.skipped],
            "reasons": {op.value: sorted(paths) for op, paths in self.reasons.items()},
            "unmapped_paths": sorted(self.unmapped_paths),
        }


class ExecutionPlanner:
    def __init__(self, graph: DependencyGraph | None = None) -> None:
        self.graph = graph or DependencyGraph()

    def plan(self, changes: ChangeSet, config: ProjectConfig) -> ExecutionPlan:
        """Plan the minimum work after a configuration change.

        config is the new configuration; it decides which stale operations
        actually apply (conditional operations).
        """
        return self._build(self.graph.stale_by_path(changes.changed_paths), config,
                           self.graph.unmapped(changes.changed_paths))

    def plan_full(self, config: ProjectConfig) -> ExecutionPlan:
        """Plan for a project with no previous state: every applicable operation."""
        stale = {op: frozenset({INITIAL_BUILD}) for op in PipelineOperation}
        return self._build(stale, config, frozenset())

    @staticmethod
    def is_applicable(operation: PipelineOperation, config: ProjectConfig) -> bool:
        # The feature dictionary operation still runs when extraction is
        # disabled: it removes a stale generated dictionary.
        if operation is PipelineOperation.EXTRACT_FEATURES:
            return config.mesh.surface.extract_features
        return True

    def _build(
        self,
        stale: dict[PipelineOperation, frozenset[str]],
        config: ProjectConfig,
        unmapped: frozenset[str],
    ) -> ExecutionPlan:
        # Enum declaration order is a valid topological order of the graph.
        ordered = [op for op in PipelineOperation if op in stale]
        return ExecutionPlan(
            operations=tuple(op for op in ordered if self.is_applicable(op, config)),
            skipped=tuple(op for op in ordered if not self.is_applicable(op, config)),
            reasons={op: stale[op] for op in ordered},
            unmapped_paths=unmapped,
        )
