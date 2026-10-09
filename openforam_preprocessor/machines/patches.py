"""Patch typing (docs/rotating_machinery.md section 7).

Every patch has one type. Meshing writes wall types as OpenFOAM `wall` and
every other type as `patch`, as the VAWT workflow does. Draft patch tables
fill the faces of a generated domain; imported regions are never typed
automatically (decision E5): a region named like a type is only suggested.
"""

from __future__ import annotations

from collections.abc import Iterable

from machines.config import (
    BoxDomain,
    CylinderDomain,
    PatchConfig,
    PatchSource,
    PatchType,
    SourceKind,
    box_face,
)
from vawt.config import Axis

_WALLS = frozenset({PatchType.WALL, PatchType.ROTATING_WALL})


def openfoam_type(kind: PatchType) -> str:
    """The OpenFOAM boundary type a patch is meshed with."""
    return "wall" if kind in _WALLS else "patch"


def _face_patch(name: str, kind: PatchType, face: str) -> PatchConfig:
    return PatchConfig(name=name, type=kind,
                       source=PatchSource(kind=SourceKind.DOMAIN_FACE, ref=face))


def draft_box_patches(flow_axis: Axis) -> tuple[PatchConfig, ...]:
    """Inlet on the minimum face of the flow axis, outlet on the maximum, the
    other four faces SLIP and named after their face."""
    patches = [_face_patch("inlet", PatchType.INLET, box_face(flow_axis, False)),
               _face_patch("outlet", PatchType.OUTLET, box_face(flow_axis, True))]
    patches += [_face_patch(box_face(axis, maximum), PatchType.SLIP, box_face(axis, maximum))
                for axis in Axis if axis is not flow_axis for maximum in (False, True)]
    return tuple(patches)


def draft_cylinder_patches() -> tuple[PatchConfig, ...]:
    """Inlet on the axis_min end, outlet on the axis_max end, the side SLIP."""
    return (_face_patch("inlet", PatchType.INLET, "axis_min"),
            _face_patch("outlet", PatchType.OUTLET, "axis_max"),
            _face_patch("side", PatchType.SLIP, "side"))


def draft_domain_patches(domain: BoxDomain | CylinderDomain,
                         flow_axis: Axis | None) -> tuple[PatchConfig, ...]:
    if isinstance(domain, CylinderDomain):
        return draft_cylinder_patches()
    if flow_axis is None:
        raise ValueError("A box domain's draft patches need the flow axis.")
    return draft_box_patches(flow_axis)


_SUGGESTIONS = {
    "inlet": PatchType.INLET, "outlet": PatchType.OUTLET, "wall": PatchType.WALL,
    "walls": PatchType.WALL, "slip": PatchType.SLIP, "symmetry": PatchType.SLIP,
}


def suggest_region_types(regions: Iterable[str]) -> dict[str, PatchType]:
    """Types suggested by region names (inlet, outlet, wall...). Only a
    suggestion for the user to confirm; never applied."""
    return {name: _SUGGESTIONS[name.lower()] for name in regions
            if name.lower() in _SUGGESTIONS}
