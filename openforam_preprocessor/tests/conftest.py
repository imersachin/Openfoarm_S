from __future__ import annotations

from pathlib import Path

import pytest
import trimesh


@pytest.fixture
def cube_stl(tmp_path: Path) -> Path:
    """A watertight unit cube centred on the origin, written as STL."""
    path = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(1.0, 1.0, 1.0)).export(path)
    return path
