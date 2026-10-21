"""Fixes after the M2a testing review (PASS WITH FINDINGS; no HIGH).

MEDIUM 1: the VTK sets of an earlier check are removed before checkMesh
(it writes a set only when it has faces). MEDIUM 2: the step preflight
uses a checkMesh log only when the import report says it is of the same
file. MEDIUM 3: the free RAM is measured once the heavy-step lock is held.
LOW 4: a copy never replaces another file of the same name. LOW 5: the
unnamed-zones INFO for a binary Fluent file. LOW 6: why a background check
did not finish is shown. LOW 7: the box conversion takes the heavy lock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from machines import disk, heavy_lock
from machines.heavy_lock import HeavyLock
from machines.msh_import.cases import MERGED_CASE
from machines.msh_import.detect import inspect_mesh_file
from machines.msh_import.pipeline import IMPORT_REPORT, STEP_RESOURCES
from tests.fakes_msh import FakeMshRunner
from tests.fixtures.msh import configs
from tests.fixtures.msh.configs import M1
from tests.unit.mesh_import.test_m2a_disk import FLUENT_UNNAMED
from tests.unit.mesh_import.test_m2a_quality import SC_LIKE
from tests.unit.mesh_import.test_m2a_resources import Setup


def test_the_sets_of_an_earlier_check_do_not_stay(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    bad = Setup(tmp_path, monkeypatch, runner=FakeMshRunner(failed_checks=2, check_text=SC_LIKE))
    data = bad.data()
    assert bad.run(data).succeeded
    sets = bad.root / "cases" / MERGED_CASE / "postProcessing/constant"
    assert list(sets.glob("*/*.vtp"))
    good = FakeMshRunner()
    bad.service.pipeline.runner = good
    config = bad.service.saved_config()
    assert config is not None
    result = bad.service.pipeline.run_sync(bad.root, config, force=True)  # a mesh that passes
    assert result.succeeded and "checkMesh" in good.names()
    assert not list(sets.glob("*/*.vtp"))


def test_the_preflight_does_not_use_the_cells_of_another_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    assert setup.run().succeeded
    log = setup.root / "logs" / MERGED_CASE / "checkMesh.log"
    log.write_text(log.read_text("utf-8").replace("cells:            100",
                                                  "cells:            500000"), "utf-8")
    report = json.loads((setup.root / IMPORT_REPORT).read_text("utf-8"))
    report["sha256"] = "0" * 64  # the last check was of another file
    (setup.root / IMPORT_REPORT).write_text(json.dumps(report), "utf-8")
    data = configs.rotating(setup.service.store_mesh_file(
        "fluent_rotating.msh", (M1 / "fluent_rotating.msh").read_bytes()))
    assert setup.run(data).succeeded
    found = json.loads((setup.root / STEP_RESOURCES).read_text("utf-8"))
    assert found["cells"] == 3383  # the Fluent file's declared count, not 500,000


def test_the_free_ram_is_measured_after_the_heavy_lock_is_taken(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heavy_lock, "POLL_S", 0.02)
    setup = Setup(tmp_path, monkeypatch)
    order: list[str] = []
    probe = setup.service.pipeline.system_probe

    def watched(root: Path) -> object:
        order.append("held" if (tmp_path / "heavy.lock").exists() else "free")
        return probe(root)

    setup.service.pipeline.system_probe = watched  # type: ignore[method-assign]
    assert setup.run().succeeded
    assert order and order[0] == "held"


def test_a_copy_never_replaces_another_file_of_the_same_name(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    first, second = tmp_path / "A" / "mesh.msh", tmp_path / "B" / "mesh.msh"
    for path, body in ((first, b"(0 a)"), (second, b"(0 b)")):
        path.parent.mkdir()
        path.write_bytes(body)
    one, _ = setup.service.start_mesh_copy(first)
    disk.JOBS.wait(one, 10)
    two, _ = setup.service.start_mesh_copy(second)
    disk.JOBS.wait(two, 10)
    assert one.name == "mesh.msh" and two.name == "mesh-2.msh"
    assert one.read_bytes() == b"(0 a)" and two.read_bytes() == b"(0 b)"
    again, _ = setup.service.start_mesh_copy(first)  # the same file: the same target
    disk.JOBS.wait(again, 10)
    assert again == one


def test_a_binary_fluent_file_still_says_its_zones_have_no_name(tmp_path: Path) -> None:
    path = tmp_path / "binary.msh"
    path.write_bytes(FLUENT_UNNAMED.replace(b"(13 (9 1 1 2 4)(", b"(2013 (9 1 1 2 4)("))
    info = inspect_mesh_file(path)
    codes = {i.code for i in info.issues}
    assert {"FLUENT_BINARY_UNTESTED", "MSH_UNNAMED_ZONES"} <= codes


def test_why_a_background_check_did_not_finish_is_shown_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.workflow.run_lock import RunLock

    setup = Setup(tmp_path, monkeypatch)
    assert setup.run().succeeded
    lock = RunLock(setup.root)
    assert lock.acquire() is None  # a mesh run of this project holds it
    try:
        assert setup.service.start_revolution(8).started
        active = setup.service.revolutions.latest(setup.root)
        assert active is not None and active.finished.wait(10)
    finally:
        lock.release()
    assert [i.code for i in setup.service.revolution_messages()] == ["RUN_IN_PROGRESS"]
    assert setup.service.revolution_messages() == ()


def test_the_box_conversion_waits_for_no_other_heavy_step(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    other = HeavyLock(tmp_path / "heavy.lock")
    assert other.acquire() is None
    other.describe(tmp_path / "francis", "checkMesh", None)
    try:
        path = setup.service.store_mesh_file("box_22.msh", (M1 / "box_22.msh").read_bytes())
        result = setup.service.convert_for_box(path)
    finally:
        other.release()
    assert [i.code for i in result.issues] == ["HEAVY_STEP_RUNNING"]
    assert "francis: checkMesh" in result.issues[0].message and setup.runner.calls == []


def test_the_box_conversion_releases_the_heavy_lock(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup(tmp_path, monkeypatch)
    path = setup.service.store_mesh_file("box_22.msh", (M1 / "box_22.msh").read_bytes())
    assert setup.service.convert_for_box(path).issues == ()
    assert not (tmp_path / "heavy.lock").exists()
