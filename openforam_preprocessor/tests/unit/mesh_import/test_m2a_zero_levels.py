"""Tet quality and cell determinant by value (owner, after M2a): a cell
determinant at or below 0 is BLOCKING; tet quality at or below 0 is a WARNING
saying it affects particle tracking and point interpolation, not the flow
solver used here (GV and SC, with 122 and 575 such faces, ran pimpleFoam:
docs/msh_import_notes.md 13.8).

checkMesh prints no minimum, and each -meshQuality row counts only the faces
the rows before it left, so a second pass (quality.ZERO_QUALITY_DICT) counts
the faces at or below 0 whenever the table has a face in error. The wording is
v2512's own: tests/fixtures/msh/m2a/checkmesh/logs (run.sh), the cube stretched
5000 times (tet quality and determinant below their limits, none at or below
0) and the cube with a point pushed through a face (12 tet faces below 0).
"""

from __future__ import annotations

from pathlib import Path

from machines.msh_import import quality
from machines.msh_import.cases import MERGED_CASE
from machines.msh_import.pipeline import ZERO_CASE
from machines.pipeline import CHECK_MESH_LOG, MESH_REPORT
from tests.fakes_msh import FakeMshRunner
from tests.unit.mesh_import.test_m2a_quality import LOGS, SC_LIKE, Setup, log

B, W = quality.BLOCKING, quality.WARNING


def zero_log(name: str) -> str:
    return (LOGS / f"{name}_zero_checkMesh.log").read_text("utf-8")


def found(text: str, zero: str | None) -> list[tuple[str, str]]:
    return [(f.level, f.kind) for f in quality.classify(text, zero)]


# --- the classes ---------------------------------------------------------------------------

def test_the_second_pass_counts_v2512s_rows() -> None:
    assert quality.zero_counts(zero_log("aspect")) == {"tet quality": 0, "cell determinant": 0}
    assert quality.zero_counts(zero_log("pushpoint")) == {"tet quality": 12,
                                                          "cell determinant": 0}
    assert quality.zero_counts("checkMesh stopped before its table") is None


def test_small_but_positive_values_stay_warnings_and_say_so() -> None:
    # The stretched cube: 160 tet faces below 1e-15, 80 below determinant 0.001.
    findings = quality.classify(log("aspect"), zero_log("aspect"))
    assert [(f.level, f.kind) for f in findings] == [
        (W, "aspect ratio"), (W, "tet quality"), (W, "cell determinant")]
    assert findings[1].text.endswith("tet quality < 1e-15: 160 (minimum above 0)")
    assert findings[2].text.endswith("(minimum above 0)")
    assert not findings[0].text.endswith("(minimum above 0)")


def test_tet_quality_at_or_below_zero_is_a_warning_that_says_what_it_affects() -> None:
    # The import pass shows no tet row (earlier rows took those faces); the
    # second pass finds 12 faces below 0.
    assert (W, "tet quality") not in found(log("pushpoint"), None)
    findings = quality.classify(log("pushpoint"), zero_log("pushpoint"))
    zero = [f for f in findings if f.kind == "tet quality at or below 0"]
    assert [(f.level, f.count) for f in zero] == [(W, 12)]
    assert zero[0].text == ("faces with tet quality at or below 0: 12; affects particle "
                            "tracking and point interpolation, not the flow solver used here")


def test_a_determinant_at_or_below_zero_is_blocking() -> None:
    zero = ("Checking faces in error :\n"
            "    faces with face-decomposition tet quality < 0          : 0\n"
            "    faces on cells with determinant < 0                    : 4\n"
            "  <<Writing 4 faces in error to set meshQualityFaces\n")
    findings = quality.classify(log("aspect"), zero)
    assert (B, "cell determinant at or below 0") in [(f.level, f.kind) for f in findings]
    tet = next(f for f in findings if f.kind == "tet quality")
    assert tet.level == W and tet.text.endswith("(minimum above 0)")


def test_a_second_pass_that_wrote_nothing_is_blocking() -> None:
    assert (B, "unmeasured") in found(log("aspect"), "")


def test_no_second_pass_is_needed_when_the_table_has_no_face_in_error() -> None:
    assert not quality.needs_zero_pass(log("ok"))
    assert not quality.needs_zero_pass("no table")
    assert quality.needs_zero_pass(log("aspect"))
    # A failure outside the table (open boundary): nothing to measure, nothing added.
    assert found(log("openboundary"), "") == found(log("openboundary"), None)


def test_without_the_second_pass_the_m2a_rule_holds() -> None:
    assert found(log("aspect"), None) == [
        (W, "aspect ratio"), (W, "tet quality"), (W, "cell determinant")]


# --- through the import ----------------------------------------------------------------------

def checkmesh_runs(runner: FakeMshRunner) -> list[str]:
    return [case for name, case in runner.calls if name == "checkMesh"]


def test_an_sc_like_mesh_with_tet_faces_below_zero_imports_with_a_warning(
        tmp_path: Path) -> None:
    runner = FakeMshRunner(failed_checks=2, check_text=SC_LIKE, zero=(575, 0))
    setup = Setup(tmp_path, runner)
    result = setup.run()

    assert result.succeeded, result.message
    report = setup.json(MESH_REPORT)
    assert report["assessment"]["mesh_validity"] == "VALID"
    warning = next(i for i in report["issues"] if i["code"] == "MESH_QUALITY_WARNING"
                   and i["details"]["kind"] == "tet quality at or below 0")
    assert "575; affects particle tracking and point interpolation, not the flow solver " \
        "used here" in warning["message"]
    assert len(checkmesh_runs(runner)) == 2
    assert not (setup.root / "cases" / ZERO_CASE).exists()  # removed; the log stays
    assert "tet quality < 0          : 575" in (
        setup.root / "logs" / ZERO_CASE / CHECK_MESH_LOG).read_text("utf-8")
    zero = [f for f in report["checkmesh_findings"] if f["kind"] == "tet quality at or below 0"]
    assert [(f["level"], f["count"]) for f in zero] == [(W, 575)]


def test_a_mesh_with_a_determinant_at_or_below_zero_is_not_valid(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(failed_checks=2, check_text=SC_LIKE, zero=(575, 3)))
    assert not setup.run().succeeded
    report = setup.json(MESH_REPORT)
    assert report["assessment"]["mesh_validity"] == "INVALID"
    failure = next(i for i in report["issues"] if i["code"] == "CHECKMESH_FAILURE")
    assert failure["message"].endswith("BLOCKING: cell determinant at or below 0.")


def test_an_sc_like_mesh_with_nothing_below_zero_stays_a_warning(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(failed_checks=2, check_text=SC_LIKE))
    result = setup.run()

    assert result.succeeded, result.message
    report = setup.json(MESH_REPORT)
    assert report["assessment"]["mesh_validity"] == "VALID"
    tet = next(i for i in report["issues"] if i["code"] == "MESH_QUALITY_WARNING"
               and i["details"]["kind"] == "tet quality")
    assert "tet quality < 1e-15: 550 (minimum above 0)" in tet["message"]


def test_a_second_pass_without_a_table_stops_the_mesh(tmp_path: Path) -> None:
    setup = Setup(tmp_path, FakeMshRunner(failed_checks=2, check_text=SC_LIKE, zero=None))
    assert not setup.run().succeeded
    failure = next(i for i in setup.json(MESH_REPORT)["issues"]
                   if i["code"] == "CHECKMESH_FAILURE")
    assert "unmeasured" in failure["message"]


def test_a_mesh_that_passes_runs_one_checkmesh_and_leaves_no_old_second_pass(
        tmp_path: Path) -> None:
    bad = Setup(tmp_path, FakeMshRunner(failed_checks=2, check_text=SC_LIKE))
    assert bad.run().succeeded
    zero = bad.root / "logs" / ZERO_CASE / CHECK_MESH_LOG
    assert zero.is_file()
    good = FakeMshRunner()
    bad.service.pipeline.runner = good
    config = bad.service.saved_config()
    assert config is not None
    assert bad.service.pipeline.run_sync(bad.root, config, force=True).succeeded
    assert checkmesh_runs(good) == [MERGED_CASE]
    assert not zero.exists()
