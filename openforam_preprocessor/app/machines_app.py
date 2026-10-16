"""Rotating-machinery UI (Streamlit prototype; G7, docs/rotating_machinery.md
sections 13 and 27): VAWT, HAWT, Francis and Custom, each with its own sections.

Presentation only: everything it shows comes from MachineService. Start it in
WSL with scripts/run_machines_app.sh, which sources OpenFOAM and serves the
app on 127.0.0.1; open http://localhost:8502 in the Windows browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/machines_app.py` puts app/ first on sys.path; the repository
# root is there only with `python -m streamlit`. So nothing in app/ may be
# named like a top-level package (app/machines_ui, not app/machines).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from app.machines_ui import shell  # noqa: E402

st.set_page_config(page_title="Rotating Machines", layout="wide")
shell.main()
