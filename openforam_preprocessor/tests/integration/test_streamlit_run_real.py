"""The apps under a real `streamlit run` server, driven over the browser's
websocket protocol, so the entry scripts execute as they do in a browser
(Streamlit puts app/ first on sys.path there, which the in-process AppTest
does not reproduce). Any uncaught exception fails the test.

Needs no OpenFOAM; runs in the default suite.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from websockets.sync.client import ClientConnection, connect

from tests.fixtures.vawt.drafts import preset_draft
from tests.unit.vawt.ui_helpers import SECTION_KEYS
from vawt.service import VawtService

REPO = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@dataclass
class Page:
    """The elements one completed script run sent, as a browser receives them."""

    elements: list[tuple[str, str, str]] = field(default_factory=list)  # (type, id, text)
    exceptions: list[str] = field(default_factory=list)
    primary: set[str] = field(default_factory=set)  # ids of primary buttons

    def widget_id(self, kind: str, label: str) -> str:
        return next(i for t, i, text in self.elements if t == kind and text == label)

    def text(self) -> str:
        return "\n".join(text for _, _, text in self.elements)


class Browser:
    """A minimal browser session: reruns with widget values, like the frontend."""

    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.values: dict[str, str] = {}

    def run(self, *, text: dict[str, str] | None = None, click: str | None = None) -> Page:
        self.values.update(text or {})
        msg = BackMsg()
        msg.rerun_script.query_string = ""  # marks the message as a rerun request
        for widget, value in self.values.items():
            msg.rerun_script.widget_states.widgets.add(id=widget, string_value=value)
        if click is not None:
            msg.rerun_script.widget_states.widgets.add(id=click, trigger_value=True)
        self.ws.send(msg.SerializeToString())
        page = Page()
        while True:
            forward = ForwardMsg()
            forward.ParseFromString(self.ws.recv(timeout=60))
            kind = forward.WhichOneof("type")
            if kind == "new_session":  # a script run (or st.rerun) starts
                page = Page()
            elif kind == "delta" and forward.delta.WhichOneof("type") == "new_element":
                element = forward.delta.new_element
                etype = element.WhichOneof("type") or ""
                if etype == "exception":
                    page.exceptions.append(f"{element.exception.type}: "
                                           f"{element.exception.message}")
                body = getattr(element, etype)
                text = next((str(getattr(body, f)) for f in ("label", "body")
                             if hasattr(body, f)), "")
                page.elements.append((etype, str(getattr(body, "id", "")), text))
                if etype == "button" and body.type == "primary":
                    page.primary.add(body.id)
            elif kind == "script_finished":
                status = forward.script_finished
                if status in (ForwardMsg.FINISHED_EARLY_FOR_RERUN,
                              ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY):
                    continue
                assert status != ForwardMsg.FINISHED_WITH_COMPILE_ERROR
                return page


class Server:
    """`streamlit run` started like scripts/run_vawt_app.sh (no PYTHONPATH),
    plus a browser connection to it."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.projects = tmp_path / "projects"
        self.projects.mkdir()
        self.port = free_port()
        self.log = tmp_path / "server.log"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, script: str) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["VAWT_PROJECTS_DIR"] = str(self.projects)
        with self.log.open("wb") as out:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", script,
                 "--server.address", "127.0.0.1", "--server.port", str(self.port),
                 "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                cwd=REPO, env=env, stdout=out, stderr=subprocess.STDOUT)

    def connect(self) -> ClientConnection:
        deadline = time.monotonic() + 60
        while True:
            try:  # proxy=None: never route localhost through an HTTP(S)_PROXY
                return connect(f"ws://127.0.0.1:{self.port}/_stcore/stream",
                               subprotocols=["streamlit"],  # type: ignore[list-item]
                               open_timeout=5, proxy=None)
            except OSError:
                assert self.process is not None and self.process.poll() is None, self.output()
                assert time.monotonic() < deadline, self.output()
                time.sleep(0.2)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    srv = Server(tmp_path)
    try:
        yield srv
    finally:
        srv.stop()
    assert "Traceback" not in srv.output(), srv.output()


def test_dashboard_renders(server: Server) -> None:
    server.start("app/dashboard.py")
    with server.connect() as ws:
        page = Browser(ws).run()

    assert page.exceptions == []
    assert page.elements


def test_vawt_app_opens_a_project_and_renders_every_section(server: Server) -> None:
    root = server.projects / "rotor"
    assert VawtService(root).save(preset_draft(server.tmp)).saved

    server.start("app/vawt_app.py")
    with server.connect() as ws:
        browser = Browser(ws)
        start = browser.run()
        assert start.exceptions == []
        assert "VAWT mesh generator" in start.text()

        folder = start.widget_id("text_input", "Project folder (inside WSL)")
        browser.run(text={folder: str(root)})
        page = browser.run(click=start.widget_id("button", "Open folder"))
        assert page.exceptions == []
        assert "### Fixture" in page.text()

        for key in SECTION_KEYS:
            nav = next(i for t, i, _ in page.elements if t == "button"
                       and i.endswith(f"-nav-{key}"))
            page = browser.run(click=nav)
            assert page.exceptions == [], key
            assert nav in page.primary, key  # the section is now the active one


def test_no_name_in_app_shadows_an_importable_module() -> None:
    # Under `streamlit run`, app/ is first on sys.path: a package or module
    # there named like a top-level one (e.g. app/vawt vs vawt) hides it.
    names = {p.stem for p in (REPO / "app").iterdir()
             if (p.suffix == ".py" and p.stem != "__init__") or (p / "__init__.py").is_file()}
    assert names

    shadowing = sorted(n for n in names if importlib.util.find_spec(n) is not None)

    assert shadowing == []
