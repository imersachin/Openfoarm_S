"""VAWT mesh generator UI (Streamlit prototype; spec section 13).

Presentation only: everything it shows comes from VawtService. Start it in WSL
with scripts/run_vawt_app.sh, which sources OpenFOAM and serves the app on
127.0.0.1; open http://localhost:8501 in the Windows browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/vawt_app.py` only puts app/ on sys.path.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from app.vawt import shell  # noqa: E402

st.set_page_config(page_title="VAWT Mesh Generator", layout="wide")
shell.main()
