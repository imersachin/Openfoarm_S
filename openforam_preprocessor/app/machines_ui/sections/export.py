"""Export: the generated meshing dictionaries. Downloads of the case archive
arrive in V7."""

from __future__ import annotations

import streamlit as st

from app.machines_ui import state


def render(ctx: state.Context) -> None:
    st.subheader("Export")
    st.info("The OpenFOAM case archive and other exports arrive in V7. The set-up case is "
            f"in `{ctx.service.root / 'case'}`.")
    files = ctx.service.generated_files()
    if not files:
        st.caption("No dictionaries yet: they are written when a run starts.")
        return
    name = st.selectbox("Generated dictionary", list(files), key="machines_dictionary")
    if name:
        st.code(files[name], language=None)
