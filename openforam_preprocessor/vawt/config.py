"""Declarative VAWT configuration (docs/vawt_mesh_generator.md section 6).

All models are frozen, reject unknown keys and non-finite numbers. Lengths are
in metres after unit conversion. Reused engine models get strict subclasses
here; their behaviour in the generic workflow is unchanged.

Plane coordinates: (u, v) are the two axes other than the rotor axis, in
x -> y -> z order (axis z: u = x, v = y; axis y: u = x, v = z; axis x:
u = y, v = z).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from core.config.models import (
    Bounds,
    GeometryConfig,
    MeshQualityLimits,
    OpenFOAMProfile,
    PositiveFloat,
    PositiveInt,
    SnappyQualityControls,
    Vector3,
)

VAWT_SCHEMA_VERSION = 1
# Error types raised for an unusable schema_version (see parse_config).
SCHEMA_VERSION_INVALID = "schema_version_invalid"
SCHEMA_VERSION_NEWER = "schema_version_newer"

_STRICT = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
_PATCH_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"


class Axis(StrEnum):
    X = "x"
    Y = "y"
    Z = "z"

    @property
    def position(self) -> int:
        """0, 1, 2 for x, y, z (array column)."""
        return "xyz".index(self.value)


def plane_axes(axis: Axis) -> tuple[Axis, Axis]:
    """(u, v): the two axes normal to `axis`, in x -> y -> z order."""
    u, v = (a for a in Axis if a is not axis)
    return u, v


def third_axis(first: Axis, second: Axis) -> Axis:
    return next(a for a in Axis if a not in (first, second))


class InterfaceType(StrEnum):
    AMI = "AMI"  # sliding interface between rotor and outer mesh
    CELL_ZONE = "CELL_ZONE"  # single mesh with a rotating cell zone


class LayerSizing(StrEnum):
    RELATIVE = "RELATIVE"
    ABSOLUTE = "ABSOLUTE"


# --- strict versions of reused engine models ----------------------------------

class Vec3(Vector3):
    model_config = _STRICT


class Box(Bounds):
    model_config = _STRICT

    minimum: Vec3
    maximum: Vec3

    def contains_box(self, other: Box) -> bool:
        lo, hi, olo, ohi = self.minimum, self.maximum, other.minimum, other.maximum
        return (lo.x <= olo.x and lo.y <= olo.y and lo.z <= olo.z
                and ohi.x <= hi.x and ohi.y <= hi.y and ohi.z <= hi.z)

    def strictly_contains(self, point: Vector3) -> bool:
        lo, hi = self.minimum, self.maximum
        return lo.x < point.x < hi.x and lo.y < point.y < hi.y and lo.z < point.z < hi.z


class RotorGeometryConfig(GeometryConfig):
    model_config = _STRICT

    rotation_deg: Vec3 = Field(default_factory=lambda: Vec3(x=0, y=0, z=0))
    translation: Vec3 = Field(default_factory=lambda: Vec3(x=0, y=0, z=0))
    patch_name: str = Field(default="rotor", pattern=_PATCH_NAME, max_length=64)


class StrictSnappyQualityControls(SnappyQualityControls):
    model_config = _STRICT


class StrictMeshQualityLimits(MeshQualityLimits):
    model_config = _STRICT


# --- VAWT models ------------------------------------------------------------------

class RotorAxes(BaseModel):
    """Both axes are required: a suggestion is offered, never applied silently."""

    model_config = _STRICT

    axis: Axis
    flow_axis: Axis

    @model_validator(mode="after")
    def validate_distinct(self) -> RotorAxes:
        if self.flow_axis is self.axis:
            raise ValueError("flow_axis must differ from the rotor axis.")
        return self

    @property
    def lateral_axis(self) -> Axis:
        return third_axis(self.axis, self.flow_axis)


class RotatingZoneConfig(BaseModel):
    model_config = _STRICT

    centre_u: float
    centre_v: float
    axis_min: float
    axis_max: float
    diameter: PositiveFloat
    interface: InterfaceType = InterfaceType.AMI
    cell_size: PositiveFloat
    location_in_mesh: Vec3  # inside the cylinder, outside the rotor solid

    @model_validator(mode="after")
    def validate_extent(self) -> RotatingZoneConfig:
        if self.axis_min >= self.axis_max:
            raise ValueError("axis_min must be less than axis_max.")
        return self


class DomainPatches(BaseModel):
    """Inlet on the minimum face of the flow axis, outlet on the maximum."""

    model_config = _STRICT

    inlet: str = Field(default="inlet", pattern=_PATCH_NAME, max_length=64)
    outlet: str = Field(default="outlet", pattern=_PATCH_NAME, max_length=64)
    lateral_min: str = Field(default="lateral_min", pattern=_PATCH_NAME, max_length=64)
    lateral_max: str = Field(default="lateral_max", pattern=_PATCH_NAME, max_length=64)
    axial_min: str = Field(default="axial_min", pattern=_PATCH_NAME, max_length=64)
    axial_max: str = Field(default="axial_max", pattern=_PATCH_NAME, max_length=64)

    def names(self) -> tuple[str, ...]:
        return (self.inlet, self.outlet, self.lateral_min, self.lateral_max,
                self.axial_min, self.axial_max)

    @model_validator(mode="after")
    def validate_unique(self) -> DomainPatches:
        if len(set(self.names())) != len(self.names()):
            raise ValueError("Domain patch names must be unique.")
        return self


class DomainConfig(BaseModel):
    model_config = _STRICT

    bounds: Box
    cell_size: PositiveFloat
    patches: DomainPatches = Field(default_factory=DomainPatches)
    location_in_mesh: Vec3  # inside the domain, outside the cylinder


class WakeConfig(BaseModel):
    model_config = _STRICT

    box: Box
    level: int = Field(default=1, ge=0, le=10)


class RefinementConfig(BaseModel):
    model_config = _STRICT

    blade_min_level: int = Field(default=1, ge=0, le=10)
    blade_max_level: int = Field(default=2, ge=0, le=10)
    interface_level: int = Field(default=1, ge=0, le=10)
    wake: WakeConfig | None = None
    extract_features: bool = False
    feature_level: int = Field(default=2, ge=0, le=10)
    feature_angle_deg: float = Field(default=30.0, gt=0, lt=180)

    @model_validator(mode="after")
    def validate_levels(self) -> RefinementConfig:
        if self.blade_max_level < self.blade_min_level:
            raise ValueError("blade_max_level must be >= blade_min_level.")
        return self


class LayersConfig(BaseModel):
    model_config = _STRICT

    enabled: bool = False
    count: int = Field(default=3, ge=1, le=20)
    expansion_ratio: float = Field(default=1.2, ge=1.0, le=2.0)
    sizing: LayerSizing = LayerSizing.RELATIVE
    # RELATIVE: fractions of the local cell size.
    final_layer_thickness: float = Field(default=0.3, gt=0, le=1.0)
    min_thickness: float = Field(default=0.1, gt=0, le=1.0)
    # ABSOLUTE: metres.
    first_layer_thickness: PositiveFloat | None = None
    min_thickness_m: PositiveFloat | None = None

    @model_validator(mode="after")
    def validate_absolute(self) -> LayersConfig:
        if self.enabled and self.sizing is LayerSizing.ABSOLUTE and (
            self.first_layer_thickness is None or self.min_thickness_m is None
        ):
            raise ValueError(
                "ABSOLUTE layer sizing requires first_layer_thickness and min_thickness_m."
            )
        return self


class ExportConfig(BaseModel):
    model_config = _STRICT

    fluent_msh: bool = True


class VawtProjectConfig(BaseModel):
    model_config = _STRICT

    schema_version: int = VAWT_SCHEMA_VERSION
    project_name: str = Field(min_length=1, max_length=80,
                              pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    openfoam_profile: OpenFOAMProfile = OpenFOAMProfile.OPENCFD
    geometry: RotorGeometryConfig
    rotor: RotorAxes
    rotating_zone: RotatingZoneConfig
    domain: DomainConfig | None = None  # absent: rotating zone only
    refinement: RefinementConfig = Field(default_factory=RefinementConfig)
    layers: LayersConfig = Field(default_factory=LayersConfig)
    snappy_quality: StrictSnappyQualityControls = Field(
        default_factory=StrictSnappyQualityControls
    )
    quality: StrictMeshQualityLimits = Field(default_factory=StrictMeshQualityLimits)
    max_global_cells: PositiveInt = 2_000_000
    export: ExportConfig = Field(default_factory=ExportConfig)

    @model_validator(mode="before")
    @classmethod
    def migrate_schema(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        version = data.get("schema_version", VAWT_SCHEMA_VERSION)
        # Only a whole number >= 1 is a version; anything else is rejected, never
        # overwritten (bool is excluded: it is an int subclass).
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise PydanticCustomError(
                SCHEMA_VERSION_INVALID,
                "schema_version must be a whole number of 1 or more, got {version!r}.",
                {"version": version},
            )
        if version > VAWT_SCHEMA_VERSION:
            raise PydanticCustomError(
                SCHEMA_VERSION_NEWER,
                "schema_version {version} was written by a newer version of this "
                "application (supported: {supported}); upgrade the application.",
                {"version": version, "supported": VAWT_SCHEMA_VERSION},
            )
        return {**data, "schema_version": VAWT_SCHEMA_VERSION}

    @model_validator(mode="after")
    def validate_patch_names(self) -> VawtProjectConfig:
        if self.domain is not None and self.geometry.patch_name in self.domain.patches.names():
            raise ValueError(
                f"The rotor patch name '{self.geometry.patch_name}' is also used as a "
                "domain patch name."
            )
        return self
