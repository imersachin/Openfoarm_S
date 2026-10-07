from pathlib import Path

import pytest

from core.config.models import ProjectConfig
from core.issues import IssueSeverity
from mesh.generator import OpenFOAMMeshCaseGenerator
from tests.helpers import build_config


def with_mesh(config: ProjectConfig, **mesh_updates: object) -> ProjectConfig:
    """Replace mesh sub-models, re-validating the result."""
    data = config.model_dump(mode="json")
    for key, value in mesh_updates.items():
        data["mesh"][key] = value
    return ProjectConfig.model_validate(data)


def system_text(case: Path, name: str) -> str:
    return (case / "system" / name).read_text(encoding="utf-8")


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

    flags = ("control_dict_changed", "block_mesh_changed", "snappy_changed",
             "mesh_quality_changed", "feature_dict_changed")
    assert [getattr(first, f) for f in flags] == [True, True, True, True, False]
    assert not any(getattr(second, f) for f in flags)
    assert snapshot == {p.name: p.read_bytes() for p in (tmp_path / "system").iterdir()}


def test_coordinates_keep_full_precision(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"))
    background = config.mesh.background.model_dump(mode="json")
    background["domain"]["minimum"]["x"] = -2.123456789012
    config = with_mesh(config, background=background)

    OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    assert "(-2.123456789012 -2.0 -2.0)" in system_text(tmp_path, "blockMeshDict")


def test_cell_cap_produces_warning_with_effective_size(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"))
    background = config.mesh.background.model_dump(mode="json")
    background.update(base_cell_size=0.001, max_cells_per_axis=1000)
    config = with_mesh(config, background=background)

    files = OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    assert "(1000 1000 1000)" in system_text(tmp_path, "blockMeshDict")
    issue = next(i for i in files.issues if i.code == "BACKGROUND_CELLS_CAPPED")
    assert issue.severity is IssueSeverity.WARNING
    assert issue.details["axes"]["x"] == {
        "requested_cells": 4000, "effective_cells": 1000, "effective_cell_size": 0.004,
    }


def test_no_cap_no_issues(tmp_path: Path) -> None:
    files = OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))
    assert files.issues == ()


@pytest.mark.parametrize(("feature_angle", "included"), [(30.0, "150.0"), (45.0, "135.0")])
def test_feature_extract_dict_references_artifact_and_angle(
    tmp_path: Path, feature_angle: float, included: str
) -> None:
    config = build_config(Path("part.stl"), extract_features=True, feature_angle_deg=feature_angle)

    OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    text = system_text(tmp_path, "surfaceFeatureExtractDict")
    assert "\npart.stl\n{" in text
    assert "extractionMethod    extractFromSurface;" in text
    assert f"includedAngle   {included};" in text
    assert "object      surfaceFeatureExtractDict;" in text


def test_geometry_references_are_consistent(tmp_path: Path) -> None:
    OpenFOAMMeshCaseGenerator().generate(
        tmp_path, build_config(Path("source/anything.stl"), extract_features=True)
    )

    snappy = system_text(tmp_path, "snappyHexMeshDict")
    features = system_text(tmp_path, "surfaceFeatureExtractDict")
    # Dictionaries reference the transformed artifact name, never the source.
    assert "anything" not in snappy + features
    assert "    part.stl\n    {\n        type triSurfaceMesh;\n        name part;" in snappy
    assert 'file "part.eMesh";' in snappy
    assert "\npart.stl\n" in features


def test_stale_feature_dict_is_removed_when_extraction_disabled(tmp_path: Path) -> None:
    generator = OpenFOAMMeshCaseGenerator()
    generator.generate(tmp_path, build_config(Path("part.stl"), extract_features=True))
    assert (tmp_path / "system" / "surfaceFeatureExtractDict").is_file()

    files = generator.generate(tmp_path, build_config(Path("part.stl")))

    assert files.feature_dict_changed is True
    assert not (tmp_path / "system" / "surfaceFeatureExtractDict").exists()


def test_user_authored_feature_dict_is_not_removed(tmp_path: Path) -> None:
    path = tmp_path / "system" / "surfaceFeatureExtractDict"
    path.parent.mkdir(parents=True)
    path.write_text("// hand written\n", encoding="utf-8")

    OpenFOAMMeshCaseGenerator().generate(tmp_path, build_config(Path("part.stl")))

    assert path.read_text(encoding="utf-8") == "// hand written\n"


def test_foundation_profile_feature_extraction_is_blocking(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"), profile="openfoam_foundation", extract_features=True)

    files = OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    assert not (tmp_path / "system" / "surfaceFeatureExtractDict").exists()
    assert [i.code for i in files.issues] == ["FEATURE_EXTRACTION_UNSUPPORTED_PROFILE"]
    assert files.issues[0].severity is IssueSeverity.BLOCKING


def test_snappy_refinement_and_location_values(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"), minimum_level=1, maximum_level=4)

    OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    text = system_text(tmp_path, "snappyHexMeshDict")
    assert "level (1 4);" in text
    assert "locationInMesh      (1.5 0.0 0.0);" in text
    assert "resolveFeatureAngle 30.0;" in text
    assert "maxGlobalCells      2000000;" in text
    assert "refinementRegions" in text
    assert "addLayers       false;" in text


def test_snappy_layer_controls_are_complete(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"))
    config = with_mesh(config, layers={"enabled": True, "number_of_layers": 5})

    OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    text = system_text(tmp_path, "snappyHexMeshDict")
    assert "addLayers       true;" in text
    assert "        part\n        {\n            nSurfaceLayers 5;\n        }" in text
    for key in (
        "relativeSizes", "expansionRatio", "finalLayerThickness", "minThickness", "nGrow",
        "featureAngle", "nRelaxIter", "nSmoothSurfaceNormals", "nSmoothNormals",
        "nSmoothThickness", "maxFaceThicknessRatio", "maxThicknessToMedialRatio",
        "minMedialAxisAngle", "nBufferCellsNoExtrude", "nLayerIter",
    ):
        assert f"\n    {key} " in text, key


def test_mesh_quality_dict_uses_configured_limits(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"))
    config = with_mesh(config, quality={"max_non_orthogonality": 70.5, "min_volume": 1e-15})

    OpenFOAMMeshCaseGenerator().generate(tmp_path, config)

    text = system_text(tmp_path, "meshQualityDict")
    assert "maxNonOrtho             70.5;" in text
    assert "minVol                  1e-15;" in text
    assert '#include "meshQualityDict"' in system_text(tmp_path, "snappyHexMeshDict")


def test_output_is_identical_across_case_directories(tmp_path: Path) -> None:
    config = build_config(Path("part.stl"), extract_features=True)
    generator = OpenFOAMMeshCaseGenerator()

    generator.generate(tmp_path / "a", config)
    generator.generate(tmp_path / "b", config)

    for name in ("controlDict", "blockMeshDict", "surfaceFeatureExtractDict",
                 "snappyHexMeshDict", "meshQualityDict"):
        assert system_text(tmp_path / "a", name) == system_text(tmp_path / "b", name), name
