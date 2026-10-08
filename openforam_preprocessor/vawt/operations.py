"""VAWT pipeline operations and dependency tables (spec section 9).

The tables feed the engine's generic DependencyGraph and ExecutionPlanner.
Each configuration field maps to the operations that read it directly;
staleness then follows the data flow. Field scopes match the sub-cases whose
dictionaries the field changes (vawt/case_generator.py, tested in V2), so the
outer and rotor meshes are re-run independently (spec section 9.2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.config.manager import ConfigurationManager
from core.workflow.dependency_graph import DependencyGraph
from core.workflow.planner import ExecutionPlan, ExecutionPlanner
from vawt.case_generator import MERGED, OUTER, ROTOR, case_layout
from vawt.config import LayerSizing, VawtProjectConfig


class VawtOperation(StrEnum):
    """Declaration order is the execution order."""

    VALIDATE = "vawt_validate"  # V1/V2 checks; a gate before any OpenFOAM command
    IMPORT_GEOMETRY = "vawt_import_geometry"  # source -> units -> transform -> artifact
    GENERATE_CASES = "vawt_generate_cases"  # every sub-case's dictionaries
    OUTER_MESH = "vawt_outer_mesh"  # outer: blockMesh, snappyHexMesh
    ROTOR_FEATURES = "vawt_rotor_features"  # surfaceFeatureExtract (rotor or single case)
    ROTOR_MESH = "vawt_rotor_mesh"  # rotor: blockMesh, snappyHexMesh, topoSet
    SINGLE_MESH = "vawt_single_mesh"  # merged, CELL_ZONE with a domain: blockMesh, snappy
    ASSEMBLE = "vawt_assemble"  # merged, AMI: mergeMeshes, createPatch
    CHECK_MESH = "vawt_check_mesh"  # checkMesh on the final mesh
    VALIDATE_MESH = "vawt_validate_mesh"  # acceptance limits and region rule


Op = VawtOperation

# Operations that run OpenFOAM and whose outputs are cached.
CACHED = frozenset({Op.IMPORT_GEOMETRY, Op.OUTER_MESH, Op.ROTOR_FEATURES, Op.ROTOR_MESH,
                    Op.SINGLE_MESH, Op.ASSEMBLE, Op.CHECK_MESH})

DOWNSTREAM: Mapping[VawtOperation, frozenset[VawtOperation]] = {
    Op.VALIDATE: frozenset(),
    Op.IMPORT_GEOMETRY: frozenset({Op.ROTOR_FEATURES, Op.ROTOR_MESH, Op.SINGLE_MESH}),
    # Dictionaries are cheap and always regenerated; fields map straight to the
    # mesh operation of their sub-case, so generation does not spread staleness.
    Op.GENERATE_CASES: frozenset(),
    Op.OUTER_MESH: frozenset({Op.ASSEMBLE}),
    Op.ROTOR_FEATURES: frozenset({Op.ROTOR_MESH, Op.SINGLE_MESH}),
    Op.ROTOR_MESH: frozenset({Op.ASSEMBLE, Op.CHECK_MESH}),
    Op.SINGLE_MESH: frozenset({Op.CHECK_MESH}),
    Op.ASSEMBLE: frozenset({Op.CHECK_MESH}),
    Op.CHECK_MESH: frozenset({Op.VALIDATE_MESH}),
    Op.VALIDATE_MESH: frozenset(),
}

# Not a configuration field: emitted when the set of sub-cases changes (the
# domain is switched on/off, or the interface type changes).
LAYOUT = "<layout>"

_GATE = frozenset({Op.VALIDATE, Op.GENERATE_CASES})
_ALL_MESHES = frozenset({Op.OUTER_MESH, Op.ROTOR_FEATURES, Op.ROTOR_MESH, Op.SINGLE_MESH,
                         Op.ASSEMBLE, Op.CHECK_MESH})
_SOURCE = frozenset({Op.VALIDATE, Op.IMPORT_GEOMETRY})
_ROTOR_SIDE = frozenset({Op.ROTOR_MESH, Op.SINGLE_MESH})
_CYLINDER = _GATE | {Op.OUTER_MESH, Op.ROTOR_MESH, Op.SINGLE_MESH}

DIRECT_CONSUMERS: Mapping[str, frozenset[VawtOperation]] = {
    LAYOUT: _GATE | _ALL_MESHES,
    "schema_version": frozenset(),
    "project_name": frozenset(),
    "openfoam_profile": _GATE | _ALL_MESHES,
    "geometry.source_path": _SOURCE,
    "geometry.source_units": _SOURCE,
    "geometry.scale": _SOURCE,
    "geometry.rotation_deg": _SOURCE,
    "geometry.translation": _SOURCE,
    "geometry.patch_name": _SOURCE | _GATE | {Op.ROTOR_FEATURES} | _ROTOR_SIDE,
    "rotor.axis": _CYLINDER,  # places the cylinder and the rotor block
    # Only the domain's patch faces follow the flow direction (inlet on the
    # minimum face); the rotor dictionaries do not.
    "rotor.flow_axis": _GATE | {Op.OUTER_MESH, Op.SINGLE_MESH},
    "rotating_zone.interface": _GATE,  # its effect on the mesh is the layout change
    "rotating_zone.centre_u": _CYLINDER,
    "rotating_zone.centre_v": _CYLINDER,
    "rotating_zone.axis_min": _CYLINDER,
    "rotating_zone.axis_max": _CYLINDER,
    "rotating_zone.diameter": _CYLINDER,
    "rotating_zone.cell_size": _GATE | _ROTOR_SIDE,
    "rotating_zone.location_in_mesh": _GATE | {Op.ROTOR_MESH},
    "domain.bounds": _GATE | {Op.OUTER_MESH, Op.SINGLE_MESH},
    "domain.cell_size": _GATE | {Op.OUTER_MESH, Op.SINGLE_MESH},
    "domain.patches": _GATE | {Op.OUTER_MESH, Op.SINGLE_MESH},
    "domain.location_in_mesh": _GATE | {Op.OUTER_MESH, Op.SINGLE_MESH},
    "refinement.blade_min_level": _GATE | _ROTOR_SIDE,
    "refinement.blade_max_level": _GATE | _ROTOR_SIDE,
    "refinement.interface_level": _GATE | {Op.OUTER_MESH},
    "refinement.wake": _GATE | {Op.OUTER_MESH, Op.SINGLE_MESH},
    "refinement.extract_features": _GATE | {Op.ROTOR_FEATURES} | _ROTOR_SIDE,
    "refinement.feature_level": _GATE | _ROTOR_SIDE,
    "refinement.feature_angle_deg": _GATE | {Op.ROTOR_FEATURES} | _ROTOR_SIDE,
    "layers": _GATE | _ROTOR_SIDE,
    # meshQualityDict is read by snappyHexMesh in every meshing sub-case and by
    # checkMesh -meshQuality in the final case.
    "snappy_quality": _GATE | {Op.OUTER_MESH, Op.ROTOR_MESH, Op.SINGLE_MESH, Op.CHECK_MESH},
    "quality": frozenset({Op.VALIDATE_MESH}),
    "max_global_cells": _GATE | {Op.OUTER_MESH, Op.ROTOR_MESH, Op.SINGLE_MESH},
    "export": frozenset(),  # export is V7
}


def _layers_on(sizing: LayerSizing | None) -> Callable[[VawtProjectConfig], bool]:
    def active(config: VawtProjectConfig) -> bool:
        return config.layers.enabled and (sizing is None or config.layers.sizing is sizing)
    return active


def _has_rotor_case(config: VawtProjectConfig) -> bool:
    return ROTOR in case_layout(config).sub_cases


# Settings that reach a dictionary only while another setting enables them.
ACTIVE_WHEN: Mapping[str, Callable[[VawtProjectConfig], bool]] = {
    "refinement.feature_level": lambda c: c.refinement.extract_features,
    "layers.count": _layers_on(None),
    "layers.expansion_ratio": _layers_on(None),
    "layers.sizing": _layers_on(None),
    "layers.final_layer_thickness": _layers_on(LayerSizing.RELATIVE),
    "layers.min_thickness": _layers_on(LayerSizing.RELATIVE),
    "layers.first_layer_thickness": _layers_on(LayerSizing.ABSOLUTE),
    "layers.min_thickness_m": _layers_on(LayerSizing.ABSOLUTE),
    # A change arrives per coordinate; the parent entry covers a replaced point.
    **{f"rotating_zone.location_in_mesh{suffix}": _has_rotor_case
       for suffix in ("", ".x", ".y", ".z")},
}


def applicable(operation: VawtOperation, config: VawtProjectConfig) -> bool:
    """Whether the operation exists in this configuration's layout."""
    layout = case_layout(config)
    if operation is Op.OUTER_MESH or operation is Op.ASSEMBLE:
        return OUTER in layout.sub_cases
    if operation is Op.ROTOR_MESH:
        return ROTOR in layout.sub_cases
    if operation is Op.SINGLE_MESH:
        return layout.single_mesh
    if operation is Op.ROTOR_FEATURES:
        return config.refinement.extract_features
    return True


def operations_for(config: VawtProjectConfig) -> tuple[VawtOperation, ...]:
    """The operations a run of this configuration goes through, in order."""
    return tuple(op for op in VawtOperation if applicable(op, config))


def feature_case(config: VawtProjectConfig) -> str:
    """The sub-case where surfaceFeatureExtract runs (the one meshing the rotor)."""
    return MERGED if case_layout(config).single_mesh else ROTOR


GRAPH: DependencyGraph[VawtOperation] = DependencyGraph(
    operations=list(VawtOperation), downstream=DOWNSTREAM, direct_consumers=DIRECT_CONSUMERS,
)
# An operation outside the layout (e.g. the outer mesh of a rotor-only project)
# never runs, so it makes nothing after it stale.
PLANNER: ExecutionPlanner[VawtOperation, VawtProjectConfig] = ExecutionPlanner(
    GRAPH, active_when=ACTIVE_WHEN, applicable=applicable,
    propagate_through_inapplicable=False,
)


@dataclass(frozen=True)
class VawtChanges:
    changed_paths: frozenset[str]


def detect_changes(old: VawtProjectConfig, new: VawtProjectConfig) -> VawtChanges:
    """Changed configuration paths, plus LAYOUT when the set of sub-cases changes."""
    paths: set[str] = set(ConfigurationManager._diff_values(  # shared diff rules
        old.model_dump(mode="json"), new.model_dump(mode="json")))
    if case_layout(old) != case_layout(new):
        paths.add(LAYOUT)
    return VawtChanges(frozenset(paths))


def plan_changes(old: VawtProjectConfig, new: VawtProjectConfig) -> ExecutionPlan[Any]:
    """Which operations a change makes stale, and why (for explanation and tests)."""
    return PLANNER.plan(detect_changes(old, new), new)
