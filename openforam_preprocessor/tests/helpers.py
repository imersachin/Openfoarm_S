from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config.models import (
    BackgroundMeshConfig,
    Bounds,
    GeometryConfig,
    MeshConfig,
    ProjectConfig,
    Vector3,
)


def build_config(source_path: Path, **surface: Any) -> ProjectConfig:
    return ProjectConfig(
        project_name="Demo",
        geometry=GeometryConfig(source_path=source_path, patch_name="part"),
        mesh=MeshConfig(
            background=BackgroundMeshConfig(
                domain=Bounds(
                    minimum=Vector3(x=-2, y=-2, z=-2),
                    maximum=Vector3(x=2, y=2, z=2),
                ),
                base_cell_size=0.5,
            ),
            location_in_mesh=Vector3(x=1.5, y=0, z=0),
            surface=surface,
        ),
    )
