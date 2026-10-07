from __future__ import annotations

from pathlib import Path

import pytest

from core.artifacts import MANIFEST_PATH, ArtifactStore, CacheStatus
from core.artifacts.hashing import MISSING, files_under, sha256_file


@pytest.fixture
def case(tmp_path: Path) -> Path:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.txt").write_text("alpha", encoding="utf-8")
    return tmp_path


INPUTS = {"system/dict": "abc", "tool": "t1"}


def test_identical_inputs_and_outputs_hit(case: Path) -> None:
    ArtifactStore(case).record("op", INPUTS, [case / "out" / "a.txt"])

    check = ArtifactStore(case).check("op", dict(INPUTS))  # fresh instance: persisted

    assert check.status is CacheStatus.HIT
    assert check.reusable


def test_changed_input_misses_and_names_the_input(case: Path) -> None:
    store = ArtifactStore(case)
    store.record("op", INPUTS, [case / "out" / "a.txt"])

    check = store.check("op", {**INPUTS, "tool": "t2"})

    assert check.status is CacheStatus.INPUTS_CHANGED
    assert check.paths == ("tool",)


def test_added_or_removed_input_misses(case: Path) -> None:
    store = ArtifactStore(case)
    store.record("op", INPUTS, [case / "out" / "a.txt"])

    assert store.check("op", {**INPUTS, "extra": "x"}).status is CacheStatus.INPUTS_CHANGED
    assert store.check("op", {"tool": "t1"}).status is CacheStatus.INPUTS_CHANGED


def test_missing_output_is_rejected(case: Path) -> None:
    store = ArtifactStore(case)
    store.record("op", INPUTS, [case / "out" / "a.txt"])
    (case / "out" / "a.txt").unlink()

    check = store.check("op", INPUTS)

    assert check.status is CacheStatus.OUTPUT_MISSING
    assert check.paths == ("out/a.txt",)


def test_corrupted_output_is_rejected(case: Path) -> None:
    store = ArtifactStore(case)
    store.record("op", INPUTS, [case / "out" / "a.txt"])
    (case / "out" / "a.txt").write_text("tampered", encoding="utf-8")

    assert store.check("op", INPUTS).status is CacheStatus.OUTPUT_CORRUPTED


def test_existing_file_without_record_is_not_a_cache_hit(case: Path) -> None:
    assert ArtifactStore(case).check("op", INPUTS).status is CacheStatus.NO_RECORD


def test_invalidate_removes_record_persistently(case: Path) -> None:
    store = ArtifactStore(case)
    store.record("op", INPUTS, [case / "out" / "a.txt"])

    store.invalidate("op")

    assert ArtifactStore(case).check("op", INPUTS).status is CacheStatus.NO_RECORD


@pytest.mark.parametrize(
    "content", ["{not json", '{"version": 99, "records": {}}', '{"version": 1}', "[]"]
)
def test_unreadable_manifest_reuses_nothing_and_reports(case: Path, content: str) -> None:
    (case / ".preprocessor").mkdir()
    (case / MANIFEST_PATH).write_text(content, encoding="utf-8")

    store = ArtifactStore(case)

    assert store.load_issue is not None
    assert store.load_issue.code == "ARTIFACT_MANIFEST_UNREADABLE"
    assert store.check("op", INPUTS).status is CacheStatus.NO_RECORD


def test_manifest_is_deterministic_json(case: Path) -> None:
    store = ArtifactStore(case)
    store.record("b", INPUTS, [])
    store.record("a", INPUTS, [])

    text = (case / MANIFEST_PATH).read_text(encoding="utf-8")

    assert text.index('"a"') < text.index('"b"')


def test_hashing_helpers(tmp_path: Path) -> None:
    assert sha256_file(tmp_path / "nope") == MISSING
    (tmp_path / "d" / "sets").mkdir(parents=True)
    (tmp_path / "d" / "points").write_text("p", encoding="utf-8")
    (tmp_path / "d" / "sets" / "bad").write_text("s", encoding="utf-8")

    names = [p.name for p in files_under(tmp_path / "d", exclude_dirs=frozenset({"sets"}))]

    assert names == ["points"]
