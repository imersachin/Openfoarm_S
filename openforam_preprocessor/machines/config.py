"""Declarative rotating-machinery configuration (docs/rotating_machinery.md section 6).

Built like vawt.config: every model is frozen and rejects unknown keys and
non-finite numbers. Lengths are in metres after unit conversion. VAWT models
are reused where they already describe the setting (Vec3, Box, layers, wake,
quality).

Cylinders (zones and the cylinder domain) are aligned with a global axis and
use the VAWT plane coordinates: (u, v) are the two axes other than the
cylinder axis, in x -> y -> z order (vawt.config.plane_axes).

Cross-field rules that need a named finding (patch names, inlet and outlet,
references between sections, geometry) are checks in machines.validation,
not model errors.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from core.config.models import LengthUnit, OpenFOAMProfile, PositiveFloat, PositiveInt
from vawt.config import (
    SCHEMA_VERSION_INVALID,
    SCHEMA_VERSION_NEWER,
    Axis,
    Box,
    InterfaceType,
    LayersConfig,
    StrictMeshQualityLimits,
    StrictSnappyQualityControls,
    Vec3,
    WakeConfig,
)

MACHINE_SCHEMA_VERSION = 1

_STRICT = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
# A name OpenFOAM accepts as a patch, zone or region name.
Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)]


class MachineType(StrEnum):
    VAWT = "VAWT"
    VAWT_POLE = "VAWT_POLE"
    HAWT = "HAWT"
    FRANCIS = "FRANCIS"
    CUSTOM = "CUSTOM"


class Motion(StrEnum):
    STATIONARY = "STATIONARY"
    ROTATING = "ROTATING"  # entirely inside its rotating zone
    SPLIT = "SPLIT"  # split by its zone's interface; the part inside rotates


class PatchType(StrEnum):
    """Section 7. Interface patches are created by the pipeline, never listed."""

    INLET = "INLET"
    OUTLET = "OUTLET"
    WALL = "WALL"
    ROTATING_WALL = "ROTATING_WALL"
    SLIP = "SLIP"  # symmetry or slip


class StlFormat(StrEnum):
    """Section 5.3: where an imported surface's region names come from."""

    NAMED_REGIONS = "NAMED_REGIONS"  # `solid <name>` blocks of one ASCII STL
    ONE_FILE_PER_PATCH = "ONE_FILE_PER_PATCH"  # the file name: inlet.stl -> inlet


class DomainKind(StrEnum):
    BOX = "BOX"
    CYLINDER = "CYLINDER"
    IMPORTED = "IMPORTED"


class ZoneShape(StrEnum):
    CYLINDER = "CYLINDER"
    IMPORTED = "IMPORTED"


class SourceKind(StrEnum):
    BODY = "BODY"  # the surface of a body
    DOMAIN_FACE = "DOMAIN_FACE"  # a face of a generated box or cylinder domain
    REGION = "REGION"  # a region (or per-patch file) of an imported surface


BOX_FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
CYLINDER_FACES = ("axis_min", "axis_max", "side")


def box_face(axis: Axis, maximum: bool) -> str:
    return f"{axis.value}_{'max' if maximum else 'min'}"


# --- surfaces -----------------------------------------------------------------------

class StlSource(BaseModel):
    """An uploaded STL and its transform (core.config.models.GeometryConfig order:
    unit conversion -> scale -> rotation -> translation, about the origin)."""

    model_config = _STRICT

    source_path: Path
    source_units: LengthUnit  # required: never assumed
    scale: PositiveFloat = 1.0
    rotation_deg: Vec3 = Field(default_factory=lambda: Vec3(x=0, y=0, z=0))
    translation: Vec3 = Field(default_factory=lambda: Vec3(x=0, y=0, z=0))

    @model_validator(mode="after")
    def validate_stl(self) -> StlSource:
        if self.source_path.suffix.lower() != ".stl":
            raise ValueError("Only STL files are accepted.")
        return self


class ImportedSurface(BaseModel):
    """A closed surface made of one STL with named regions, or one STL per region."""

    model_config = _STRICT

    format: StlFormat
    # At least one: checked after the files, not as a tuple min_length (with it,
    # pydantic also reports "too short" whenever one file is invalid).
    files: tuple[StlSource, ...]

    def file_regions(self) -> tuple[str, ...] | None:
        """Region names known without reading the files (the file stems), or None."""
        if self.format is StlFormat.ONE_FILE_PER_PATCH:
            return tuple(f.source_path.stem for f in self.files)
        return None

    @model_validator(mode="after")
    def validate_files(self) -> ImportedSurface:
        if not self.files:
            raise ValueError("At least one STL file is needed.")
        if self.format is StlFormat.NAMED_REGIONS and len(self.files) != 1:
            raise ValueError("NAMED_REGIONS takes exactly one STL file.")
        return self


class CylinderGeometry(BaseModel):
    """A cylinder along a global axis, with an optional coaxial hole (annulus)."""

    model_config = _STRICT

    centre_u: float
    centre_v: float
    axis_min: float
    axis_max: float
    diameter: PositiveFloat

    @model_validator(mode="after")
    def validate_extent(self) -> CylinderGeometry:
        if self.axis_min >= self.axis_max:
            raise ValueError("axis_min must be less than axis_max.")
        return self


# --- bodies -------------------------------------------------------------------------

class BodyRefinement(BaseModel):
    model_config = _STRICT

    min_level: int = Field(default=1, ge=0, le=10)
    max_level: int = Field(default=2, ge=0, le=10)
    extract_features: bool = False
    feature_level: int = Field(default=2, ge=0, le=10)
    feature_angle_deg: float = Field(default=30.0, gt=0, lt=180)

    @model_validator(mode="after")
    def validate_levels(self) -> BodyRefinement:
        if self.max_level < self.min_level:
            raise ValueError("max_level must be >= min_level.")
        return self


class BodyConfig(BaseModel):
    """A solid surface: rotor, runner, blade, pole, hub, tower (section 3)."""

    model_config = _STRICT

    name: Name
    source: StlSource
    motion: Motion  # required: never assumed
    zone: str | None = None  # the rotating zone of a ROTATING or SPLIT body
    refinement: BodyRefinement = Field(default_factory=BodyRefinement)
    layers: LayersConfig = Field(default_factory=LayersConfig)

    @model_validator(mode="after")
    def validate_zone(self) -> BodyConfig:
        if self.motion is Motion.STATIONARY and self.zone is not None:
            raise ValueError("A STATIONARY body belongs to no rotating zone.")
        if self.motion is not Motion.STATIONARY and self.zone is None:
            raise ValueError(f"A {self.motion.value} body must name its rotating zone.")
        return self


# --- rotating zones -----------------------------------------------------------------

class CylinderZone(CylinderGeometry):
    kind: Literal["CYLINDER"]
    # Annular zone: a stationary body (pole) passes through the hole.
    hole_diameter: PositiveFloat | None = None
    # Facets around the generated zone surface (decision F4; as the cylinder domain).
    segments: int = Field(default=96, ge=24, le=4096)
    # Refinement of the hole's interface in the zone mesh; the domain side gets the
    # same cell size. G0 R5 used 2: coarser left AMI weights near 0.24 (G3 test).
    hole_level: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_hole(self) -> CylinderZone:
        if self.hole_diameter is not None and self.hole_diameter >= self.diameter:
            raise ValueError("hole_diameter must be less than diameter.")
        return self


class ImportedZone(ImportedSurface):
    kind: Literal["IMPORTED"]
    origin: Vec3  # a point on the rotation axis


class RotatingZone(BaseModel):
    model_config = _STRICT

    name: Name
    axis: Axis  # rotation axis (direction); required
    shape: Annotated[CylinderZone | ImportedZone, Field(discriminator="kind")]
    cell_size: PositiveFloat
    location_in_mesh: Vec3  # inside the zone, outside every body
    interface: InterfaceType = InterfaceType.AMI  # CELL_ZONE: VAWT preset only
    interface_level: int = Field(default=1, ge=0, le=10)


# --- domain -------------------------------------------------------------------------

class BoxDomain(BaseModel):
    model_config = _STRICT

    kind: Literal["BOX"]
    bounds: Box
    cell_size: PositiveFloat
    location_in_mesh: Vec3  # inside the domain, outside every zone and body


class CylinderDomain(CylinderGeometry):
    """Meshed as a box cut by snappyHexMesh (section 5.2, method 2)."""

    kind: Literal["CYLINDER"]
    axis: Axis
    cell_size: PositiveFloat
    location_in_mesh: Vec3
    # Facets around the generated surface (decision E2; G0 R1 used 96).
    segments: int = Field(default=96, ge=24, le=4096)


class DomainPart(ImportedSurface):
    """One stationary, separately meshed part of an imported fluid domain."""

    name: Name
    cell_size: PositiveFloat
    location_in_mesh: Vec3


class ImportedDomain(BaseModel):
    model_config = _STRICT

    kind: Literal["IMPORTED"]
    parts: tuple[DomainPart, ...]  # at least one (see ImportedSurface.files)

    @model_validator(mode="after")
    def validate_parts(self) -> ImportedDomain:
        if not self.parts:
            raise ValueError("An imported domain needs at least one part.")
        return self


Domain = Annotated[BoxDomain | CylinderDomain | ImportedDomain, Field(discriminator="kind")]


# --- patches and joints ---------------------------------------------------------------

class PatchSource(BaseModel):
    model_config = _STRICT

    kind: SourceKind
    ref: str = Field(min_length=1, max_length=64)  # body name, face name or region name


class PatchConfig(BaseModel):
    model_config = _STRICT

    name: Name
    type: PatchType  # required: every patch has exactly one type
    source: PatchSource


class JointConfig(BaseModel):
    """Two coincident regions of separately meshed imported parts; meshed as a
    cyclicAMI pair (section 11), never stitched."""

    model_config = _STRICT

    first: Name
    second: Name


class ExportConfig(BaseModel):
    model_config = _STRICT

    fluent_msh: bool = False  # VAWT preset only


# --- project ------------------------------------------------------------------------

class MachineProjectConfig(BaseModel):
    model_config = _STRICT

    schema_version: int = MACHINE_SCHEMA_VERSION
    project_name: str = Field(min_length=1, max_length=80,
                              pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    openfoam_profile: OpenFOAMProfile = OpenFOAMProfile.OPENCFD
    machine: MachineType  # required
    # Main flow direction, from the axis minimum to its maximum. Required for
    # VAWT machines (box faces and presets depend on it).
    flow_axis: Axis | None = None
    # Bodies uploaded as their own STL. Surfaces inside imported parts (a
    # Francis runner's blades) are regions with a patch instead.
    bodies: tuple[BodyConfig, ...] = ()
    rotating_zones: tuple[RotatingZone, ...]  # at least one (see ImportedSurface.files)
    domain: Domain | None = None  # absent: rotating zones only (VAWT preset only)
    patches: tuple[PatchConfig, ...] = ()
    joints: tuple[JointConfig, ...] = ()
    wake: WakeConfig | None = None  # VAWT preset only
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
        version = data.get("schema_version", MACHINE_SCHEMA_VERSION)
        # As VawtProjectConfig: only a whole number >= 1 is a version.
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise PydanticCustomError(
                SCHEMA_VERSION_INVALID,
                "schema_version must be a whole number of 1 or more, got {version!r}.",
                {"version": version},
            )
        if version > MACHINE_SCHEMA_VERSION:
            raise PydanticCustomError(
                SCHEMA_VERSION_NEWER,
                "schema_version {version} was written by a newer version of this "
                "application (supported: {supported}); upgrade the application.",
                {"version": version, "supported": MACHINE_SCHEMA_VERSION},
            )
        return {**data, "schema_version": MACHINE_SCHEMA_VERSION}

    @model_validator(mode="after")
    def validate_zones(self) -> MachineProjectConfig:
        if not self.rotating_zones:
            raise ValueError("At least one rotating zone is needed.")
        return self

    def body(self, name: str) -> BodyConfig | None:
        return next((b for b in self.bodies if b.name == name), None)

    def zone(self, name: str) -> RotatingZone | None:
        return next((z for z in self.rotating_zones if z.name == name), None)

    def patch_for(self, kind: SourceKind, ref: str) -> PatchConfig | None:
        return next((p for p in self.patches
                     if p.source.kind is kind and p.source.ref == ref), None)
