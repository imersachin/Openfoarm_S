"""History: run records, newest first."""

from __future__ import annotations

import streamlit as st

from app.vawt_ui import state


def render(ctx: state.Context) -> None:
    st.subheader("History")
    records = ctx.service.runs()
    if not records:
        st.info("No runs yet.")
        return
    st.dataframe([{
        "started": r.get("started_at"), "state": r.get("state"),
        "seconds": round(float(r.get("duration_seconds") or 0.0), 1),
        "ran": len(r.get("executed") or []), "reused": len(r.get("reused") or []),
        "message": r.get("message"), "run": str(r.get("run_id", ""))[:8],
    } for r in records], hide_index=True, use_container_width=True)
    with st.expander("Full records"):
        st.json(records)
