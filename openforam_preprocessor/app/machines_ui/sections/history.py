"""History: run records, newest first, with the mesh and case-setup results."""

from __future__ import annotations

import streamlit as st

from app.machines_ui import state


def render(ctx: state.Context) -> None:
    st.subheader("History")
    records = ctx.service.runs()
    if not records:
        st.info("No runs yet.")
        return
    st.dataframe([{
        "started": r.get("started_at"), "state": r.get("state"),
        "mesh": (r.get("mesh") or {}).get("status"),
        "case setup": (r.get("case_setup") or {}).get("status"),
        "seconds": round(float(r.get("duration_seconds") or 0.0), 1),
        "ran": len(r.get("executed") or []), "reused": len(r.get("reused") or []),
        "message": r.get("message"), "run": str(r.get("run_id", ""))[:8],
    } for r in records], hide_index=True, width="stretch")
    with st.expander("Full records"):
        st.json(records)
