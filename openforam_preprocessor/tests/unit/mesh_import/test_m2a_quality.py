"""M2a commit 2: checkMesh in two levels for an imported mesh (owner decision;
machines.msh_import.quality), the quality per cell zone, the bad cells for
ParaView, and where an AMI pair's weights fall short.

The wording is checkMesh v2512's own: tests/fixtures/msh/m2a/checkmesh/logs,
made by its run.sh from cubes with one defect each (M2a evidence 1). STL and
VAWT meshes keep the one-level rule (tests/unit/test_mesh_validator.py and
the VAWT pipeline tests are unchanged).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from machines.msh_import import quality
from machines.msh_import.cases import MERGED_CASE
from machines.msh_import.pipeline import (
    ASSEMBLE_EXECUTABLES,
    CHECK_EXECUTABLES,
    IMPORT_EXECUTABLES,
    QUALITY_REPORT,
)
from machines.pipeline import MESH_REPORT, MachinePipeline
from machines.service import MachineService, _CheckCache
from tests import foam_binary
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from vawt.service import PreviewView

LOGS = Path(__file__).parents[2] / "fixtures" / "msh" / "m2a" / "checkmesh" / "logs"
TOOLS = (*IMPORT_EXECUTABLES, *ASSEMBLE_EXECUTABLES, *CHECK_EXECUTABLES)
B, W = quality.BLOCKING, quality.WARNING


def log(name: str) -> str:
    return (LOGS / f"{name}_checkMesh.log").read_text("utf-8")


def levels(text: str) -> list[tuple[str, str]]:
    return [(f.level, f.kind) for f in quality.classify(text)]


# --- the classes, from checkMesh's own wording -----------------------------------------------

def test_a_mesh_that_passes_has_no_finding() -> None:
    assert quality.classify(log("ok")) == ()


@pytest.mark.parametrize("name, expected", [
    ("negvol", [(W, "aspect ratio"), (B, "negative or zero cell volumes"),
                (B, "faces over 90 deg non-orthogonal"), (B, "wrongly oriented faces"),
                (W, "non-orthogonality"), (B, "face pyramid volume")]),
    ("pushpoint", [(B, "faces over 90 deg non-orthogonal"), (B, "wrongly oriented faces"),
                   (W, "non-orthogonality"), (B, "face pyramid volume")]),
    ("flipface", [(B, "open cells"), (B, "faces over 90 deg non-orthogonal"),
                  (B, "wrongly oriented faces"), (W, "non-orthogonality")]),
    ("openboundary", [(B, "open cells"), (B, "open boundary"), (B, "open cells")]),
    ("nonortho", [(W, "skewness"), (W, "non-orthogonality")]),
    ("aspect", [(W, "aspect ratio"), (W, "tet quality"), (W, "cell determinant")]),
])
def test_each_defect_is_classified_as_the_owner_decided(name: str,
                                                        expected: list[tuple[str, str]]) -> None:
    assert levels(log(name)) == expected


def test_an_unknown_failure_line_is_blocking() -> None:
    text = log("nonortho").replace("***Max skewness", "***Something new: 3 faces; Max skew")
    found = quality.classify(text)
    assert (B, "unclassified") in [(f.level, f.kind) for f in found]


def test_a_failure_count_the_lines_do_not_explain_is_blocking() -> None:
    text = log("nonortho").replace("Failed 2 mesh checks", "Failed 3 mesh checks")
    found = quality.classify(text)
    assert found[-1].level == B and found[-1].kind == "unclassified"
    assert "3 failed checks; 2 are explained" in found[-1].text


def test_a_failed_check_count_without_any_line_is_blocking() -> None:
    """The fake's plain "Failed N mesh checks" (and any log the rules miss)."""
    assert levels("Mesh stats\n\nFailed 1 mesh checks.\n") == [(B, "unclassified")]


# --- fields and zones --------------------------------------------------------------------------

def test_binary_and_text_fields_and_zones_are_read(tmp_path: Path) -> None:
    values = np.array([1.0, 5.0, 2.5])
    foam_binary.write(tmp_path / "skewness", "volScalarField", values)
    text = (tmp_path / "skewness").read_bytes().replace(
        b"3\n(", b"internalField   nonuniform List<scalar> 3\n(")
    (tmp_path / "skewness").write_bytes(text)
    assert quality.read_cell_field(tmp_path / "skewness").tolist() == [1.0, 5.0, 2.5]  # type: ignore[union-attr]
    (tmp_path / "uniform").write_text("FoamFile\n{\n    format ascii;\n}\n"
                                      "internalField   uniform 0.5;\n", "utf-8")
    assert quality.read_cell_field(tmp_path / "uniform") == 0.5
    (tmp_path / "cellZones").write_text(
        "FoamFile\n{\n    format ascii;\n}\n2\n(\n    a\n    {\n        type cellZone;\n"
        "        cellLabels List<label> 2(0 2);\n    }\n    b\n    {\n        type cellZone;"
        "\n        cellLabels List<label> 1(1);\n    }\n)\n", "utf-8")
    zones = quality.read_cell_zones(tmp_path / "cellZones")
    assert {k: v.tolist() for k, v in zones.items()} == {"a": [0, 2], "b": [1]}


# --- through a run (fake OpenFOAM) -----------------------------------------------------------

class Setup:
    def __init__(self, tmp_path: Path, runner: FakeMshRunner) -> None:
        self.env = openfoam_env(tmp_path, "v2512", TOOLS)
        self.runner = runner
        self.service = MachineService(
            tmp_path / "project", env=self.env, system_probe=lambda _: PLENTY,
            pipeline=MachinePipeline(runner, environment=self.env,
                                     system_probe=lambda _: PLENTY),
            check_cache=_CheckCache())
        self.root = self.service.root

    def run(self, data: dict[str, Any] | None = None) -> Any:
        if data is None:
            path = self.service.store_mesh_file("rotating_41.msh",
                                                (M1 / "rotating_41.msh").read_bytes())
            data = configs.rotating(path)
        self.service.save(copy.deepcopy(data))
        config = self.service.saved_config()
        assert config is not None
        return self.service.pipeline.run_sync(self.root, config)

    def json(self, relative: str) -> Any:
        return json.loads((self.root / relative).read_text("utf-8"))


# The SC-SV casing's failures (E0), in checkMesh's wording: quality only.
SC_LIKE = (" ***Max skewness = 8.13, 384 highly skew faces detected which may impair the "
           "quality of the results\nChecking faces in error :\n"
           "    non-orthogonality > 65  degrees                        : 42291\n"
           "    faces with face pyramid volume < 1e-13                 : 0\n"
           "    faces with face-decomposition tet quality < 1e-15      : 550\n"
           "  <<Writing 42841 faces in error to set meshQualityFaces\n")


def skewed(cells: int, bad: list[int]) -> list[float]:
    values = [0.5] * cells
    for i in bad:
        values[i] = 8.13
    return values


def test_a_quality_only_failure_is_a_warning_with_the_cells_per_zone(tmp_path: Path) -> None:
    """The fake's rotating_41 mesh: 2 zones of the file's cells (fakes_msh)."""
    runner = FakeMshRunner(cells=100, zones={"stationary": 60, "rotating": 40},
                           failed_checks=2, check_text=SC_LIKE,
                           fields={"skewness": skewed(100, [3, 70, 71])})
    setup = Setup(tmp_path, runner)
    result = setup.run()

    assert result.succeeded, result.message
    report = setup.json(MESH_REPORT)
    assert report["assessment"]["mesh_validity"] == "VALID"
    assert report["assessment"]["mesh_quality"] == "REVIEW" and report["status"] == "review"
    found = {i["code"]: i for i in report["issues"]}
    assert "CHECKMESH_FAILURE" not in found
    warnings = [i for i in report["issues"] if i["code"] == "MESH_QUALITY_WARNING"]
    assert [w["details"]["kind"] for w in warnings] == [
        "skewness", "non-orthogonality", "tet quality"]
    skew = warnings[0]["message"]
    assert "By cell zone: rotating 2 of 40 cells (5 %), worst 8.13; stationary 1 of 60" \
        in skew
    assert "No per-zone field for this check" in warnings[2]["message"]
    assert report["quality_by_zone"]["skewness"][0]["zone"] == "rotating"
    zones = setup.json(QUALITY_REPORT)
    assert zones["limits"]["skewness"] == {"past": "above", "limit": 4.0}
    merged = setup.root / "cases" / MERGED_CASE
    assert not (merged / "constant/skewness").exists()  # read, then removed
    assert "constant/skewness" in zones["removed"]
    assert (merged / "postProcessing/constant/skewFaces/skewFaces.vtp").is_file()


def test_a_blocking_failure_still_fails_the_mesh_and_names_the_class(tmp_path: Path) -> None:
    text = log("negvol")
    body = text[text.index("Checking geometry"):text.index("Failed")]
    runner = FakeMshRunner(failed_checks=5, check_text=body)
    setup = Setup(tmp_path, runner)
    result = setup.run()

    assert not result.succeeded
    report = setup.json(MESH_REPORT)
    assert report["assessment"]["mesh_validity"] == "INVALID"
    failure = next(i for i in report["issues"] if i["code"] == "CHECKMESH_FAILURE")
    assert failure["message"] == (
        "checkMesh reported 5 failed mesh check(s); BLOCKING: negative or zero cell "
        "volumes, faces over 90 deg non-orthogonal, wrongly oriented faces, face pyramid "
        "volume.")
    assert {i["code"] for i in report["issues"]} >= {"MESH_QUALITY_WARNING"}  # aspect ratio


def test_the_check_writes_the_sets_and_fields_in_one_pass(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner())
    assert setup.run().succeeded
    check = next(a for a in setup.runner.argv if a[0] == "checkMesh")
    assert check[-4:] == ("-writeSets", "vtk", "-writeFields",
                          "(nonOrthoAngle skewness faceWeight aspectRatio)")


def test_the_mesh_view_shows_the_bad_faces(tmp_path: Path) -> None:
    runner = FakeMshRunner(failed_checks=2, check_text=SC_LIKE)
    setup = Setup(tmp_path, runner)
    assert setup.run().succeeded
    data = setup.service.preview(PreviewView.MESH)
    assert data.scene is not None
    assert [m.name for m in data.scene.markers] == ["checkMesh: skewFaces"]


# --- AMI weights: where they fall short ------------------------------------------------------

def test_a_weights_warning_says_where_each_side_falls_short(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(weights=(0.65, 1.0)))
    result = setup.run()

    assert result.succeeded
    issue = next(i for i in result.issues if i.code == "AMI_WEIGHTS_OUT_OF_RANGE")
    sides = issue.details["sides"]
    assert [s["patch"] for s in sides] == ["ami_stat", "ami_rot"]
    # The fake: 10 faces a side, the first at 0.65; all the same area.
    assert sides[0]["faces_below"] == 1 and sides[0]["percent_below"] == pytest.approx(10.0)
    assert "ami_stat: 10 % of its area (1 of 10 faces) below 0.95, at r" in issue.message
    assert "the side spans r" in issue.message
    dictionary = (setup.root / f"cases/{MERGED_CASE}/system/amiWeightsDict").read_text()
    assert "writeFields     true;" in dictionary
