"""The 3D preview pane of the machines UI (V6 views; G7). Presentation only.

Setup shows the draft: bodies, domain, rotating zones, joints, seed points,
patches by type, and the arrows: each zone's rotation axis, its rotation
direction from the physics (right-hand rule; "not set" until chosen), and the
flow direction. Mesh shows the last mesh: boundary by patch, the two cuts and
checkMesh problem locations. Rotor axis (VAWT, HAWT; U1) shows the rotor, the
axis through the proposed point and the swept circle before the preset is
applied; Setup shows it too while the draft cannot be drawn yet.

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
from vawt.config import Axis
from vawt.service import PreviewView

VIEW_KEY = "machines_preview_view"
AXIS_VIEW = "axis"  # the proposed rotor axis (not a stored view)
LABELS: dict[Any, str] = {PreviewView.SETUP: "Setup", PreviewView.MESH: "Mesh",
                          AXIS_VIEW: "Rotor axis"}
HELP = {
    PreviewView.SETUP: "The draft as it will be meshed. Click a legend entry or group to "
                       "show or hide it. Arrows: rotation axis, rotation direction "
                       "(right-hand rule, from the physics) and flow direction. Check the "
                       "rotation arc against the real rotor.",
    PreviewView.MESH: "The last mesh: boundary by patch, a cut along the flow through the "
                      "rotation axis, a cut across the axis, and checkMesh problem "
                      "locations. A mesh that looks right is not evidence of CFD accuracy.",
    AXIS_VIEW: "The rotor, the rotation axis through the proposed axis point and the swept "
               "circle about it. Nothing is applied until the preset button on the rotor "
               "page.",
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


@st.cache_data(max_entries=8, show_spinner=False)
def _axis_figure(root: str, signature: str, _service: MachineService,
                 _inputs: Mapping[str, Any]) -> tuple[Any, tuple[dict[str, Any], ...]]:
    """(figure or None, issues) of the proposed axis; cached on the inputs and
    the STL's stat (signature)."""
    scene, arrows, issues = _service.axis_preview(
        _inputs["source"], Axis(_inputs["axis"]), _inputs.get("axis_point"))
    if scene is None:
        return None, tuple(i.as_dict() for i in issues)
    from visualization.machine_scene import scene_figure

    return scene_figure(scene, arrows), tuple(i.as_dict() for i in issues)


def axis_inputs(service: MachineService) -> dict[str, Any] | None:
    """The rotor page's inputs when they give a proposal: a rotor preset, an STL
    with its units and a chosen axis."""
    from app.machines_ui.sections.bodies import ROTOR, axis_point
    from machines.profiles import PresetKind

    profile = state.profile()
    if profile is None or profile.preset not in (PresetKind.VAWT, PresetKind.HAWT):
        return None
    inputs = st.session_state.get(ROTOR) or {}
    source = inputs.get("source") or {}
    if not source.get("source_path") or not source.get("source_units") or not inputs.get("axis"):
        return None
    return {"source": dict(source), "axis": inputs["axis"], "axis_point": axis_point(inputs)}


def _axis_view(service: MachineService, inputs: dict[str, Any]) -> None:
    signature = service.axis_preview_key(inputs)
    with st.spinner("Preparing the 3D view…"):
        figure, issues = _axis_figure(str(service.root), signature, service, inputs)
    if figure is not None:
        st.caption(HELP[AXIS_VIEW])
        st.plotly_chart(figure, width="stretch", key="machines_preview_axis")
    _show(tuple(Issue.from_dict(i) for i in issues))


def _show(issues: tuple[Issue, ...]) -> None:
    for issue in issues:
        if issue.severity is IssueSeverity.INFO:
            st.caption(f"{issue.message} {issue.suggested_action}".strip())
    render_issues([i for i in issues if i.severity is not IssueSeverity.INFO])


@st.fragment
def preview_pane() -> None:
    with st.container(border=True):
        st.markdown("**3D preview**")
        service = state.service()
        proposal = axis_inputs(service)
        views = [PreviewView.SETUP, PreviewView.MESH, *([AXIS_VIEW] if proposal else [])]
        if st.session_state.get(VIEW_KEY) not in views:
            st.session_state.pop(VIEW_KEY, None)
        view = st.radio("View", views, format_func=LABELS.__getitem__, key=VIEW_KEY,
                        horizontal=True, label_visibility="collapsed")
        if not isinstance(view, PreviewView):  # AXIS_VIEW: offered only with a proposal
            if proposal is not None:
                _axis_view(service, proposal)
            return
        st.caption(HELP[view])
        raw = state.draft() if view is PreviewView.SETUP else None
        key = service.preview_key(view, raw)
        if key.signature is None:
            if view is PreviewView.SETUP and proposal is not None:
                _axis_view(service, proposal)  # the rotor page before the preset
                return
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
