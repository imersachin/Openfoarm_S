"""Logs: the end of any command log, read by seek (bounded size)."""

from __future__ import annotations

import streamlit as st

from app.vawt import state


def render(ctx: state.Context) -> None:
    st.subheader("Logs")
    names = ctx.service.logs()
    if not names:
        st.info("No logs yet.")
        return
    name = st.selectbox("Log", names, key="vawt_log")
    if name:
        tail = ctx.service.log_tail(log=name)
        st.caption(f"`{tail.path}` · last {tail.bytes_read:,} of {tail.size:,} bytes"
                   + (" (earlier output not shown)" if tail.truncated else ""))
        st.code(tail.text or "(empty)", language=None)
