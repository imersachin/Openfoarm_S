"""VAWT mesh generator UI (Streamlit prototype; spec section 13).

Presentation only: everything it shows comes from VawtService. Start it in WSL
with scripts/run_vawt_app.sh, which sources OpenFOAM and serves the app on
127.0.0.1; open http://localhost:8501 in the Windows browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/vawt_app.py` puts app/ first on sys.path; the repository
# root is there only with `python -m streamlit`. So nothing in app/ may be
# named like a top-level package (app/vawt_ui, not app/vawt; tested in
# tests/integration/test_streamlit_run_real.py).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from app.vawt_ui import shell  # noqa: E402

st.set_page_config(page_title="VAWT Mesh Generator", layout="wide")
shell.main()
