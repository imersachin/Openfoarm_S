from pathlib import Path

from mesh.generator import OpenFOAMMeshCaseGenerator
from tests.helpers import build_config


def test_dictionaries_are_written_under_system(tmp_path: Path) -> None:
    OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    for name in ("controlDict", "blockMeshDict", "snappyHexMeshDict", "meshQualityDict"):
        assert (tmp_path / "system" / name).is_file(), name
    assert not (tmp_path / "constant" / "polyMesh" / "blockMeshDict").exists()


def test_block_mesh_dict_has_expected_cell_counts(tmp_path: Path) -> None:
    OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    text = (tmp_path / "system" / "blockMeshDict").read_text(encoding="utf-8")
    # Domain length 4 / base cell size 0.5 = 8 cells per axis.
    assert "(8 8 8)" in text
    assert "object      blockMeshDict;" in text


def test_control_dict_has_required_time_controls(tmp_path: Path) -> None:
    OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    text = (tmp_path / "system" / "controlDict").read_text(encoding="utf-8")
    for key in ("startFrom", "startTime", "stopAt", "endTime", "deltaT", "writeControl"):
        assert f"\n{key} " in text, key


def test_snappy_does_not_reference_emesh_without_feature_extraction(tmp_path: Path) -> None:
    OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    text = (tmp_path / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert ".eMesh" not in text
    assert "explicitFeatureSnap false;" in text
    assert "part.stl" in text


def test_snappy_references_emesh_when_feature_extraction_enabled(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"), extract_features=True, feature_refinement_level=4)
    OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    text = (tmp_path / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert 'file "part.eMesh";' in text
    assert "level 4;" in text
    assert "explicitFeatureSnap true;" in text


def test_generation_is_deterministic_and_write_if_changed(tmp_path: Path) -> None:
    generator = OpenFOAMMeshCaseGenerator()
    config = build_config(Path("part.stl"))

    first = generator.generate(tmp_path, config)
    snapshot = {p.name: p.read_bytes() for p in (tmp_path / "system").iterdir()}
    second = generator.generate(tmp_path, config)

    assert all(vars(first).values())
    assert not any(vars(second).values())
    assert snapshot == {p.name: p.read_bytes() for p in (tmp_path / "system").iterdir()}
