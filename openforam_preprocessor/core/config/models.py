from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveFloat = Annotated[float, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]


class OpenFOAMProfile(StrEnum):
    OPENCFD = "openfoam_com"
    FOUNDATION = "openfoam_foundation"


class Vector3(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    z: float

    def as_openfoam(self) -> str:
        return f"({self.x:g} {self.y:g} {self.z:g})"


class Bounds(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum: Vector3
    maximum: Vector3

    @model_validator(mode="after")
    def validate_extents(self) -> Bounds:
        if (
            self.minimum.x >= self.maximum.x
            or self.minimum.y >= self.maximum.y
            or self.minimum.z >= self.maximum.z
        ):
            raise ValueError("Each domain minimum coordinate must be less than its maximum.")
        return self

    @property
    def lengths(self) -> Vector3:
        return Vector3(
            x=self.maximum.x - self.minimum.x,
            y=self.maximum.y - self.minimum.y,
            z=self.maximum.z - self.minimum.z,
        )


class LengthUnit(StrEnum):
    """Source STL length units. Generated geometry is always in metres."""

    METRE = "m"
    CENTIMETRE = "cm"
    MILLIMETRE = "mm"
    MICROMETRE = "um"
    INCH = "in"
    FOOT = "ft"

    @property
    def to_metres(self) -> float:
        return _METRES_PER_UNIT[self]


_METRES_PER_UNIT: dict[LengthUnit, float] = {
    LengthUnit.METRE: 1.0,
    LengthUnit.CENTIMETRE: 1e-2,
    LengthUnit.MILLIMETRE: 1e-3,
    LengthUnit.MICROMETRE: 1e-6,
    LengthUnit.INCH: 0.0254,
    LengthUnit.FOOT: 0.3048,
}


class GeometryConfig(BaseModel):
    """Source STL plus the explicit transformation that produces the meshed artifact.

    Transformation order (contract): unit conversion -> scale -> rotation ->
    translation. Scale and rotation act about the origin (0, 0, 0). Rotation
    angles are applied about the fixed global axes in the order X, then Y,
    then Z. Translation is applied after unit conversion and is in metres.
    The source path is never referenced by OpenFOAM; only the transformed
    artifact is.
    """

    model_config = ConfigDict(frozen=True)

    source_path: Path
    source_units: LengthUnit  # required: STL does not reliably encode units
    scale: PositiveFloat = 1.0
    rotation_deg: Vector3 = Field(default_factory=lambda: Vector3(x=0, y=0, z=0))
    translation: Vector3 = Field(default_factory=lambda: Vector3(x=0, y=0, z=0))
    patch_name: str = Field(
        default="geometry",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_stl_source(self) -> GeometryConfig:
        if self.source_path.suffix.lower() != ".stl":
            raise ValueError("The MVP accepts STL geometry only.")
        return self


class BackgroundMeshConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain: Bounds
    base_cell_size: PositiveFloat = 0.1
    max_cells_per_axis: PositiveInt = 1_000
    expansion_ratio: PositiveFloat = 1.0


class SurfaceRefinementConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_level: int = Field(default=2, ge=0, le=10)
    maximum_level: int = Field(default=3, ge=0, le=10)
    feature_angle_deg: float = Field(default=30.0, gt=0, lt=180)
    feature_refinement_level: int = Field(default=3, ge=0, le=10)
    # Explicit feature edges require a surfaceFeatureExtract .eMesh. When False,
    # snappyHexMesh is generated without edge-mesh references or explicit
    # feature snapping, and feature extraction is not part of the plan.
    extract_features: bool = False

    @model_validator(mode="after")
    def validate_levels(self) -> SurfaceRefinementConfig:
        if self.maximum_level < self.minimum_level:
            raise ValueError("maximum_level must be >= minimum_level.")
        return self


class BoundaryLayerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    number_of_layers: int = Field(default=3, ge=1, le=20)
    expansion_ratio: float = Field(default=1.2, ge=1.0, le=2.0)
    final_layer_thickness: float = Field(default=0.3, gt=0, le=1.0)
    min_thickness: float = Field(default=0.1, gt=0, le=1.0)


class MeshQualityLimits(BaseModel):
    """Explicit engineering policy, separate from OpenFOAM defaults."""

    model_config = ConfigDict(frozen=True)

    max_non_orthogonality: float = Field(default=65.0, gt=0, le=180)
    max_boundary_skewness: float = Field(default=20.0, gt=0)
    max_internal_skewness: float = Field(default=4.0, gt=0)
    min_volume: NonNegativeFloat = 1e-13
    min_determinant: float = Field(default=0.001, ge=0)


class MeshConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    background: BackgroundMeshConfig
    surface: SurfaceRefinementConfig = Field(default_factory=SurfaceRefinementConfig)
    layers: BoundaryLayerConfig = Field(default_factory=BoundaryLayerConfig)
    quality: MeshQualityLimits = Field(default_factory=MeshQualityLimits)
    location_in_mesh: Vector3
    max_global_cells: PositiveInt = 2_000_000
    overwrite_existing_mesh: bool = True

    @model_validator(mode="after")
    def validate_location_in_mesh(self) -> MeshConfig:
        # snappyHexMesh keeps the mesh region containing locationInMesh; a point
        # outside (or on the boundary of) the background domain cannot select a
        # region, so reject it before any expensive meshing starts.
        point, domain = self.location_in_mesh, self.background.domain
        lo, hi = domain.minimum, domain.maximum
        if not (lo.x < point.x < hi.x and lo.y < point.y < hi.y and lo.z < point.z < hi.z):
            raise ValueError(
                "location_in_mesh must lie strictly inside the background domain "
                f"{lo.as_openfoam()} - {hi.as_openfoam()}; got {point.as_openfoam()}."
            )
        return self


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    project_name: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$",
    )
    openfoam_profile: OpenFOAMProfile = OpenFOAMProfile.OPENCFD
    geometry: GeometryConfig
    mesh: MeshConfig
