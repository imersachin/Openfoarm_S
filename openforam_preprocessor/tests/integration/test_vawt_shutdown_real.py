"""V5 on real OpenFOAM v2512: Ctrl+C (SIGINT) on an app process with an
active run cancels the run on the way out: its OpenFOAM command is terminated
and the status ends as CANCELLED, not as an interrupted run.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_vawt_shutdown_real.py -v
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from tests.fakes_vawt import VAWT_TOOLS
from vawt.service import RunView, VawtService
from vawt.status import read_status

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in VAWT_TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]
REPO = Path(__file__).resolve().parents[2]

APP_PROCESS = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from tests.fixtures.vawt.drafts import preset_draft
    from vawt.service import VawtService

    root = Path(sys.argv[1])
    service = VawtService(root)
    assert service.save(preset_draft(root.parent)).saved
    assert service.start_run().started
    while True:  # like the app's server loop, until Ctrl+C
        time.sleep(0.1)
""")


def test_ctrl_c_cancels_the_active_run(tmp_path: Path) -> None:
    root = tmp_path / "project"
    app = subprocess.Popen([sys.executable, "-c", APP_PROCESS, str(root)], cwd=REPO,
                           env={**os.environ, "PYTHONPATH": str(REPO)})
    try:
        deadline = time.monotonic() + 300
        command: dict[str, object] = {}
        while time.monotonic() < deadline:
            command = (read_status(root) or {}).get("command") or {}
            if command.get("name") == "snappyHexMesh" and "pid" in command:
                break
            assert app.poll() is None, "the app process ended early"
            time.sleep(0.2)
        pid = command["pid"]
        assert isinstance(pid, int) and psutil.pid_exists(pid)

        app.send_signal(signal.SIGINT)
        app.wait(timeout=60)
    finally:
        if app.poll() is None:
            app.kill()

    assert read_status(root)["state"] == "CANCELLED"  # type: ignore[index]
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert VawtService(root).run_status().state is RunView.CANCELLED
