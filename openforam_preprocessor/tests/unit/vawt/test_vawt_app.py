"""V5: the VAWT Streamlit UI, headless (streamlit.testing), fake OpenFOAM."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from app.vawt import shell
from core.workflow.run_lock import LOCK_PATH, process_create_time
from mesh.estimator import SystemResources
from tests.unit.vawt.test_vawt_service import GatedRunner, write_running_status
from tests.unit.vawt.ui_helpers import (
    SECTION_KEYS,
    UiProject,
    go,
    nav_label,
    open_app,
    texts,
)
from vawt.service import Analysis, RunView, SectionState, SectionStatus, VawtService
from vawt.service import ValidationOutcome as Outcome

REPO = Path(__file__).resolve().parents[3]


def wait_until(condition: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.02)


# --- layout and navigation ---------------------------------------------------------------

def test_without_a_project_the_page_asks_for_one(ui: UiProject) -> None:
    from streamlit.testing.v1 import AppTest

    from tests.unit.vawt.ui_helpers import APP

    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()

    assert not app.exception
    assert "Open or create a project" in texts(app)


def test_every_section_renders(ui: UiProject) -> None:
    ui.save()
    app = open_app(ui)

    for key in SECTION_KEYS:
        go(app, key)
        assert app.button(key=f"nav-{key}").proto.type == "primary"


def test_only_the_active_section_runs(ui: UiProject, monkeypatch: pytest.MonkeyPatch) -> None:
    ui.save()
    calls: list[str] = []
    for _, key, _, module in shell.SECTIONS:
        original = module.render

        def spy(ctx: Any, _key: str = key, _original: Any = original) -> None:
            calls.append(_key)
            _original(ctx)

        monkeypatch.setattr(module, "render", spy)
    app = open_app(ui)

    for key in SECTION_KEYS:
        calls.clear()
        go(app, key)
        assert calls == [key], (key, calls)


def test_status_marks_come_from_the_service(ui: UiProject,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    ui.save()
    sections = {s: SectionStatus(SectionState.READY) for s in
                ("project", "geometry", "rotating_zone", "domain", "refinement", "layers")}
    sections.update(geometry=SectionStatus(SectionState.ERROR),
                    domain=SectionStatus(SectionState.STALE, (), ("domain.cell_size",)),
                    layers=SectionStatus(SectionState.INCOMPLETE))

    def analyse(self: VawtService, raw: Any = None) -> Analysis:
        return Analysis(Outcome(None, None, ()), sections)

    monkeypatch.setattr(VawtService, "analyse", analyse)
    app = open_app(ui)

    assert nav_label(app, "geometry").startswith("✕")
    assert nav_label(app, "domain").startswith("↻")
    assert nav_label(app, "layers").startswith("◐")
    assert nav_label(app, "project").startswith("●")


def test_cell_zone_never_appears_for_an_ami_project(ui: UiProject) -> None:
    ui.save()
    app = open_app(ui)

    for key in SECTION_KEYS:
        go(app, key)
        assert "CELL_ZONE" not in texts(app), key
        assert not any("interface" in str(s.label).lower() for s in app.selectbox), key


# --- draft, apply, save, revert ------------------------------------------------------------

def test_apply_changes_the_draft_and_save_writes_a_revision(ui: UiProject) -> None:
    ui.save()
    app = open_app(ui)
    assert "Saved · revision 1" in texts(app)

    app.text_input(key="cfg1:project_name").set_value("Renamed")
    next(b for b in app.button if b.label == "Apply").click().run()
    assert "Unsaved changes" in texts(app)
    assert ui.service().load().raw["project_name"] != "Renamed"  # type: ignore[index]

    app.button(key="vawt_save").click().run()

    assert "Saved · revision 2" in texts(app)
    assert ui.service().load().raw["project_name"] == "Renamed"  # type: ignore[index]


def test_revert_restores_the_saved_configuration(ui: UiProject) -> None:
    ui.save()
    app = open_app(ui)
    app.text_input(key="cfg1:project_name").set_value("Changed")
    next(b for b in app.button if b.label == "Apply").click().run()

    app.button(key="vawt_revert").click().run()

    assert "Saved · revision 1" in texts(app) and "Changed" not in texts(app)


def test_new_project_is_filled_from_a_preset(ui: UiProject) -> None:
    app = open_app(ui)  # nothing saved: a new draft
    stl = ui.draft["geometry"]["source_path"]
    go(app, "geometry")
    # The upload widget is not scriptable headless: store the file as an upload would.
    stored = ui.service().store_source_file("rotor.stl", Path(stl).read_bytes())
    app.session_state["vawt_draft"]["geometry"]["source_path"] = str(stored)
    app.session_state["vawt_draft"]["geometry"]["source_units"] = "m"
    go(app, "rotating_zone")
    app.selectbox(key="vawt_preset_axis").set_value("z")
    app.selectbox(key="vawt_preset_flow").set_value("x").run()
    app.button(key="vawt_preset_fill").click().run()
    assert not app.exception

    app.button(key="vawt_save").click().run()

    assert "Saved · revision 1" in texts(app)
    assert nav_label(app, "rotating_zone").startswith("●")


# --- D3: a project using a layout the UI does not offer -------------------------------

@pytest.mark.parametrize("layout", ["CELL_ZONE", "ROTOR_ONLY"])
def test_unoffered_layout_banner_keeps_value_and_disables_start(ui: UiProject,
                                                                layout: str) -> None:
    raw = json.loads(json.dumps(ui.draft))
    raw["rotating_zone"]["interface"] = "CELL_ZONE"
    if layout == "ROTOR_ONLY":
        raw["domain"] = None
        raw["refinement"]["wake"] = None
    ui.save(raw)
    app = go(open_app(ui), "run")

    assert "not offered in this UI" in texts(app)
    assert app.button(key="vawt_start").disabled
    app.button(key="vawt_switch_ami").click().run()
    assert not app.exception
    draft = app.session_state["vawt_draft"]
    assert draft["rotating_zone"]["interface"] == "AMI" and draft["domain"] is not None
    saved = ui.service().load().raw
    assert saved["rotating_zone"]["interface"] == "CELL_ZONE"  # type: ignore[index]

    go(app, "domain")  # Save is in the setup sections' action bar
    app.button(key="vawt_save").click().run()
    go(app, "run")
    assert not app.button(key="vawt_start").disabled


# --- runs ---------------------------------------------------------------------------

def test_start_follow_and_cancel_a_run(ui: UiProject) -> None:
    ui.runner = GatedRunner(hold="snappyHexMesh")
    ui.save()
    app = go(open_app(ui), "run")

    app.button(key="vawt_start").click().run()
    ui.runner.holding.wait(5)  # type: ignore[attr-defined]
    app.run()
    assert "RUNNING" in texts(app)
    assert app.button(key="vawt_start").disabled
    app.button(key="vawt_cancel").click().run()
    wait_until(lambda: ui.service().run_status().state is RunView.CANCELLED)
    app.run()

    assert "CANCELLED" in texts(app)
    assert not app.button(key="vawt_start").disabled


def test_finished_run_shows_results_history_and_logs(ui: UiProject) -> None:
    ui.save()
    app = go(open_app(ui), "run")
    app.button(key="vawt_start").click().run()
    wait_until(lambda: ui.service().run_status().state is RunView.SUCCEEDED)

    go(app, "mesh")
    assert "VALID" in texts(app) and "NOT_ASSESSED" in texts(app)
    go(app, "history")
    assert app.dataframe and not app.exception
    go(app, "logs")
    assert "checkMesh" in texts(app)
    go(app, "export")
    assert "snappyHexMeshDict" in texts(app)


@pytest.fixture
def sleeper() -> Any:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield process
    process.kill()
    process.wait()


def test_leftover_process_is_stopped_only_after_confirmation(ui: UiProject,
                                                             sleeper: Any) -> None:
    ui.save()
    write_running_status(ui.root, {
        "name": "snappyHexMesh", "sub_case": "rotor", "log": "logs/rotor/x.log",
        "pid": sleeper.pid, "pid_create_time": process_create_time(sleeper.pid)})
    app = go(open_app(ui), "run")
    assert "snappyHexMesh (PID" in texts(app) and app.button(key="vawt_start").disabled

    app.button(key="vawt_confirm_orphan_ask").click().run()
    assert f"Stop snappyHexMesh (PID {sleeper.pid})?" in texts(app)
    app.button(key="vawt_confirm_orphan_no").click().run()
    assert psutil.pid_exists(sleeper.pid) and sleeper.poll() is None

    app.button(key="vawt_confirm_orphan_ask").click().run()
    app.button(key="vawt_confirm_orphan_yes").click().run()

    assert sleeper.wait(10) is not None
    assert not app.exception


def test_unreadable_lock_is_removed_only_after_confirmation(ui: UiProject) -> None:
    ui.save()
    lock = ui.root / LOCK_PATH
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    os.utime(lock, (time.time() - 600, time.time() - 600))
    app = go(open_app(ui), "run")
    assert "RUN_LOCK_UNREADABLE" in texts(app) or "names no run" in texts(app)

    app.button(key="vawt_confirm_lock_ask").click().run()
    app.button(key="vawt_confirm_lock_no").click().run()
    assert lock.exists()
    app.button(key="vawt_confirm_lock_ask").click().run()
    app.button(key="vawt_confirm_lock_yes").click().run()

    assert not lock.exists() and not app.button(key="vawt_start").disabled


# --- failure paths: never an uncaught exception ------------------------------------------

def visit_all(app: Any) -> None:
    for key in SECTION_KEYS:
        go(app, key)


def test_no_saved_configuration(ui: UiProject) -> None:
    app = open_app(ui)
    visit_all(app)
    go(app, "run")
    assert app.button(key="vawt_start").disabled


def test_unreadable_project_file(ui: UiProject) -> None:
    path = ui.root / ".preprocessor/vawt/project.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    app = open_app(ui)
    assert "PROJECT_FILE_UNREADABLE" in texts(app) or "cannot be read" in texts(app)
    visit_all(app)


@pytest.mark.parametrize("fail", ["snappyHexMesh", "checkMesh"])
def test_failed_run(ui: UiProject, fail: str) -> None:
    from tests.fakes_vawt import FakeVawtRunner

    ui.runner = FakeVawtRunner(fail=fail)
    ui.save()
    app = go(open_app(ui), "run")
    app.button(key="vawt_start").click().run()
    wait_until(lambda: ui.service().run_status().state is RunView.FAILED)
    app.run()
    assert "FAILED" in texts(app)
    visit_all(app)


def test_interrupted_run(ui: UiProject) -> None:
    ui.save()
    write_running_status(ui.root)
    app = go(open_app(ui), "run")
    assert "INTERRUPTED" in texts(app)
    assert not app.button(key="vawt_start").disabled
    visit_all(app)


def test_run_held_by_another_process(ui: UiProject, sleeper: Any) -> None:
    ui.save()
    path = ui.root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    import socket
    path.write_text(json.dumps({"pid": sleeper.pid, "host": socket.gethostname(),
                                "process_create_time": process_create_time(sleeper.pid)}),
                    encoding="utf-8")
    write_running_status(ui.root)
    app = go(open_app(ui), "run")
    assert "RUNNING_ELSEWHERE" in texts(app)
    assert app.button(key="vawt_start").disabled and app.button(key="vawt_cancel").disabled
    visit_all(app)


@pytest.mark.parametrize("ram_gb,status,checkbox", [(0.25, "BLOCKED", False),
                                                    (None, "HIGH RESOURCE RISK", True)])
def test_resource_preflight_gates_start(ui: UiProject, ram_gb: float | None, status: str,
                                        checkbox: bool) -> None:
    ui.save()
    service = ui.service()
    if ram_gb is None:  # the least RAM that is not BLOCKED is HIGH RESOURCE RISK
        low, high = 1, 2**40
        while high - low > 1:
            middle = (low + high) // 2
            ui.resources = SystemResources(available_ram_bytes=middle,
                                           available_disk_bytes=10**13, cpu_count=8)
            assessment = service.preflight().assessment
            assert assessment is not None
            blocked = assessment.status.value == "BLOCKED"
            low, high = (middle, high) if blocked else (low, middle)
        ui.resources = SystemResources(available_ram_bytes=high,
                                       available_disk_bytes=10**13, cpu_count=8)
    else:
        ui.resources = SystemResources(available_ram_bytes=int(ram_gb * 2**30),
                                       available_disk_bytes=10**13, cpu_count=8)
    service = ui.service()
    assert service.preflight().assessment.status.value == status  # type: ignore[union-attr]
    app = go(open_app(ui), "review")
    assert status in texts(app)
    if checkbox:
        assert app.checkbox(key="vawt_allow_high_risk") is not None
    go(app, "run")
    assert app.button(key="vawt_start").disabled
    if checkbox:
        go(app, "review")
        app.checkbox(key="vawt_allow_high_risk").check().run()
        go(app, "run")
        assert not app.button(key="vawt_start").disabled


# --- boundaries ------------------------------------------------------------------------

def test_ui_adds_no_plotly_modules_at_start() -> None:
    # Streamlit 1.65 imports plotly itself; the VAWT UI must not add to that
    # (e.g. plotly.graph_objects through the generic visualization code).
    probe = ("import sys, streamlit; before = {m for m in sys.modules if 'plotly' in m}; "
             "import app.vawt.shell, app.vawt.state; "
             "print(sorted({m for m in sys.modules if 'plotly' in m} - before))")
    result = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True,
                            text=True, env={**os.environ, "PYTHONPATH": str(REPO)},
                            check=True)
    assert result.stdout.strip() == "[]"
