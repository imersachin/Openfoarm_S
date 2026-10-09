"""Region names of imported STL files (machines.domains), on the G0 duct set."""

from __future__ import annotations

from pathlib import Path

from machines.domains import is_binary_stl, stl_region_names
from tests.fixtures.machines.g0.common import make_geometry


def test_region_names(tmp_path: Path) -> None:
    make_geometry.duct(tmp_path)

    assert stl_region_names(tmp_path / "duct_named.stl") == ("inlet", "outlet", "wall")
    assert stl_region_names(tmp_path / "inlet.stl") == ("inlet",)


def test_binary_stl_has_no_region_names(tmp_path: Path) -> None:
    make_geometry.duct(tmp_path)
    binary = tmp_path / "duct_binary.stl"
    # A binary header starting with "solid" must not pass for ASCII.
    disguised = tmp_path / "disguised.stl"
    disguised.write_bytes(b"solid duct".ljust(80, b" ") + binary.read_bytes()[80:])

    assert is_binary_stl(binary) and is_binary_stl(disguised)
    assert not is_binary_stl(tmp_path / "duct_named.stl")
    assert stl_region_names(binary) is None
    assert stl_region_names(disguised) is None


def test_missing_file_has_no_region_names(tmp_path: Path) -> None:
    assert stl_region_names(tmp_path / "missing.stl") is None
