from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Generic, Protocol, TypeVar, overload

from core.config.models import ProjectConfig
from core.workflow.dependency_graph import DependencyGraph, OpT, PipelineOperation

ConfigT = TypeVar("ConfigT")


class _Changes(Protocol):
    @property
    def changed_paths(self) -> frozenset[str]: ...

INITIAL_BUILD = "<initial>"

# Settings that only reach a generated file while another setting enables
# them. While inactive in the new configuration, changing them is ignored.
# (If the enabling setting changes too, that change covers the work.)
ACTIVE_WHEN: Mapping[str, Callable[[ProjectConfig], bool]] = {
    "mesh.surface.feature_refinement_level": lambda c: c.mesh.surface.extract_features,
    "mesh.layers.number_of_layers": lambda c: c.mesh.layers.enabled,
}


def _engine_applicable(operation: PipelineOperation, config: ProjectConfig) -> bool:
    # The feature dictionary operation still runs when extraction is
    # disabled: it removes a stale generated dictionary.
    if operation is PipelineOperation.EXTRACT_FEATURES:
        return config.mesh.surface.extract_features
    return True


@dataclass(frozen=True)
class ExecutionPlan(Generic[OpT]):
    """Stale operations in execution order, with the cause of each.

    operations: stale and applicable to the new configuration, ordered.
    skipped: stale but not applicable (e.g. feature extraction disabled).
    reasons: changed configuration paths behind each planned/skipped operation.
    unmapped_paths: changed paths with no dependency rule; these were
        treated conservatively as invalidating everything.
    inactive_paths: changed settings ignored because they are inactive.
    """

    operations: tuple[OpT, ...]
    skipped: tuple[OpT, ...]
    reasons: dict[OpT, frozenset[str]]
    unmapped_paths: frozenset[str] = frozenset()
    inactive_paths: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not self.operations

    def as_dict(self) -> dict[str, Any]:
        return {
            "operations": [op.value for op in self.operations],
            "skipped": [op.value for op in self.skipped],
            "reasons": {op.value: sorted(paths) for op, paths in self.reasons.items()},
            "unmapped_paths": sorted(self.unmapped_paths),
            "inactive_paths": sorted(self.inactive_paths),
        }


class ExecutionPlanner(Generic[OpT, ConfigT]):
    """Plans stale operations. With no arguments it plans the generic workflow;
    another workflow passes its graph, active-when rules and applicability."""

    @overload
    def __init__(
        self: ExecutionPlanner[PipelineOperation, ProjectConfig],
        graph: DependencyGraph[PipelineOperation] | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        graph: DependencyGraph[OpT],
        *,
        active_when: Mapping[str, Callable[[ConfigT], bool]],
        applicable: Callable[[OpT, ConfigT], bool],
        propagate_through_inapplicable: bool = True,
    ) -> None: ...

    def __init__(
        self,
        graph: DependencyGraph[Any] | None = None,
        *,
        active_when: Mapping[str, Callable[[Any], bool]] | None = None,
        applicable: Callable[[Any, Any], bool] | None = None,
        propagate_through_inapplicable: bool = True,
    ) -> None:
        # Without arguments OpT/ConfigT are the generic workflow's types (first
        # overload), so its tables and applicability rule fit.
        self.graph: DependencyGraph[OpT] = graph or DependencyGraph()  # type: ignore[assignment]
        self.active_when: Mapping[str, Callable[[ConfigT], bool]] = (
            active_when if active_when is not None else ACTIVE_WHEN)  # type: ignore[assignment]
        self.applicable: Callable[[OpT, ConfigT], bool] = (
            applicable if applicable is not None
            else _engine_applicable)  # type: ignore[assignment]
        # False: an operation that does not apply to the configuration (e.g. a
        # sub-case the layout does not have) does not pass staleness downstream.
        self.propagate_through_inapplicable = propagate_through_inapplicable

    def plan(self, changes: _Changes, config: ConfigT) -> ExecutionPlan[OpT]:
        """Plan the minimum work after a configuration change.

        config is the new configuration; it decides which stale operations
        actually apply (conditional operations).
        """
        inactive = frozenset(
            path for path in changes.changed_paths
            if path in self.active_when and not self.active_when[path](config)
        )
        active = changes.changed_paths - inactive
        through = (None if self.propagate_through_inapplicable
                   else lambda op: self.is_applicable(op, config))
        plan = self._build(self.graph.stale_by_path(active, through), config,
                           self.graph.unmapped(active))
        return replace(plan, inactive_paths=inactive)

    def plan_full(self, config: ConfigT) -> ExecutionPlan[OpT]:
        """Plan for a project with no previous state: every applicable operation."""
        stale = {op: frozenset({INITIAL_BUILD}) for op in self.graph.operations}
        return self._build(stale, config, frozenset())

    def is_applicable(self, operation: OpT, config: ConfigT) -> bool:
        return self.applicable(operation, config)

    def _build(
        self,
        stale: dict[OpT, frozenset[str]],
        config: ConfigT,
        unmapped: frozenset[str],
    ) -> ExecutionPlan[OpT]:
        # The graph's operation order is a valid topological order.
        ordered = [op for op in self.graph.operations if op in stale]
        return ExecutionPlan(
            operations=tuple(op for op in ordered if self.is_applicable(op, config)),
            skipped=tuple(op for op in ordered if not self.is_applicable(op, config)),
            reasons={op: stale[op] for op in ordered},
            unmapped_paths=unmapped,
        )
