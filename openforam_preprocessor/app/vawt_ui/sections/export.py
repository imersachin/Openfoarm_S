"""Export: the generated OpenFOAM dictionaries. Fluent .msh and the case
archive arrive in V7."""

from __future__ import annotations

import streamlit as st

from app.vawt_ui import state


def render(ctx: state.Context) -> None:
    st.subheader("Export")
    st.info("Fluent .msh export and the OpenFOAM case archive arrive in V7.")
    files = ctx.service.generated_files()
    if not files:
        st.caption("No dictionaries yet: they are written when a run starts.")
        return
    name = st.selectbox("Generated dictionary", list(files), key="vawt_dictionary")
    if name:
        st.code(files[name], language=None)
