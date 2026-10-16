"""The 3D preview pane of the machines UI (V6 views; G7). Presentation only.

Setup shows the draft: bodies, domain, rotating zones, joints, seed points,
patches by type, and the arrows: each zone's rotation axis, its rotation
direction from the physics (right-hand rule; "not set" until chosen), and the
flow direction. Mesh shows the last mesh: boundary by patch, the two cuts and
checkMesh problem locations.

The figure is cached per preview key (the configuration and the stat of the
STL or mesh files); a rerun with the same key reads no geometry or mesh
file. Plotly is imported only when a figure is first drawn.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from app.components.widgets import render_issues
from app.machines_ui import state
from core.issues import Issue, IssueSeverity
from machines.service import MachineService
from vawt.service import PreviewView

VIEW_KEY = "machines_preview_view"
LABELS = {PreviewView.SETUP: "Setup", PreviewView.MESH: "Mesh"}
HELP = {
    PreviewView.SETUP: "The draft as it will be meshed. Click a legend entry or group to "
                       "show or hide it. Arrows: rotation axis, rotation direction "
                       "(right-hand rule, from the physics) and flow direction. Check the "
                       "rotation arc against the real rotor.",
    PreviewView.MESH: "The last mesh: boundary by patch, a cut along the flow through the "
                      "rotation axis, a cut across the axis, and checkMesh problem "
                      "locations. A mesh that looks right is not evidence of CFD accuracy.",
}


@st.cache_data(max_entries=8, show_spinner=False)
def _figure(root: str, view: str, signature: str, _service: MachineService,
            _raw: Mapping[str, Any] | None) -> tuple[Any, tuple[str, ...],
                                                   tuple[dict[str, Any], ...]]:
    """(figure or None, notes, issues) for one preview key. Cached on
    (root, view, signature); the service and draft are not part of the key."""
    data = _service.preview(PreviewView(view), _raw)
    issues = tuple(i.as_dict() for i in data.issues)
    scene = data.scene
    if scene is None or not (scene.surfaces or scene.lines or scene.markers):
        return None, data.notes, issues
    from visualization.machine_scene import scene_figure

    return scene_figure(scene, data.arrows), data.notes, issues


def _show(issues: tuple[Issue, ...]) -> None:
    for issue in issues:
        if issue.severity is IssueSeverity.INFO:
            st.caption(f"{issue.message} {issue.suggested_action}".strip())
    render_issues([i for i in issues if i.severity is not IssueSeverity.INFO])


@st.fragment
def preview_pane() -> None:
    with st.container(border=True):
        st.markdown("**3D preview**")
        view = st.radio("View", list(LABELS), format_func=LABELS.__getitem__, key=VIEW_KEY,
                        horizontal=True, label_visibility="collapsed")
        st.caption(HELP[view])
        service = state.service()
        raw = state.draft() if view is PreviewView.SETUP else None
        key = service.preview_key(view, raw)
        if key.signature is None:
            _show(key.issues)
            return
        with st.spinner("Preparing the 3D view…"):
            figure, notes, issues = _figure(str(service.root), view.value, key.signature,
                                            service, raw)
        if figure is not None:
            st.plotly_chart(figure, width="stretch",
                            key=f"machines_preview_{view.value}")
        for note in notes:
            st.caption(note)
        _show(tuple(Issue.from_dict(i) for i in issues))
