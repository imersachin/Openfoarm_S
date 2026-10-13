"""Plotly figures of the V6 display data (machines.previews). Display only.

The camera's up direction is the first rotation axis, so a rotor axis is
vertical on screen whatever its global axis; mesh coordinates are untouched.

One colour per patch type, the same before and after meshing, so the patch
table can be checked by eye. Every trace name states its type, so colour is
never the only cue; legend groups are the types, and clicking a group or an
entry shows or hides it in the browser without a rerun.

Palette (dataviz reference instance; categorical slots for the five types
checked with its validator, all pairs: hard gates pass, colour-blind
separation in the warning band, hence the type in every name). Walls are
neutral; the checkMesh problem colour is the reserved status "critical".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import plotly.graph_objects as go

from machines.config import PatchType
from machines.previews import (
    CUT,
    DOMAIN,
    INTERFACE,
    PROBLEM,
    ROTATING_BODY,
    SEED,
    SOURCE,
    STATIONARY_BODY,
    UNASSIGNED,
    WAKE,
    ZONE,
    Arrow,
    Scene,
)

# kind ->(colour, legend group title, surface opacity)
KIND_STYLE: dict[str, tuple[str, str, float]] = {
    PatchType.INLET.value: ("#2a78d6", "Inlet", 0.45),
    PatchType.OUTLET.value: ("#e87ba4", "Outlet", 0.45),
    PatchType.WALL.value: ("#8a8984", "Wall", 1.0),
    PatchType.ROTATING_WALL.value: ("#eda100", "Rotating wall", 1.0),
    PatchType.SLIP.value: ("#1baf7a", "Symmetry or slip", 0.15),
    INTERFACE: ("#4a3aa7", "Interface (AMI)", 0.35),
    UNASSIGNED: ("#c8c7c0", "No patch type", 0.5),
    ROTATING_BODY: ("#eda100", "Body without patch", 1.0),
    STATIONARY_BODY: ("#8a8984", "Body without patch", 1.0),
    SOURCE: ("#c8c7c0", "Before transform (units converted)", 0.3),
    ZONE: ("#4a3aa7", "Rotating zone", 1.0),
    DOMAIN: ("#8a8984", "Domain", 1.0),
    WAKE: ("#8a8984", "Refinement box", 1.0),
    SEED: ("#52514e", "Mesh seed points", 1.0),
    CUT: ("#8a8984", "Cuts", 1.0),
    PROBLEM: ("#d03b3b", "checkMesh problems", 1.0),
}
ARROW_STYLE = {"axis": "#8a8984", "rotation": "#eda100", "flow": "#2a78d6"}
_FALLBACK = ("#c8c7c0", "Other", 0.5)


def style(kind: str) -> tuple[str, str, float]:
    return KIND_STYLE.get(kind, _FALLBACK)


def _name(base: str, shown: int, total: int, unit: str) -> str:
    if shown < total:
        return f"{base} — {shown:,} of {total:,} {unit}"
    return f"{base} — {total:,} {unit}" if total else base


def _xyz(points: np.ndarray) -> dict[str, Any]:
    return {"x": points[:, 0], "y": points[:, 1], "z": points[:, 2]}


def scene_figure(scene: Scene, arrows: Sequence[Arrow] = (), title: str = "") -> go.Figure:
    """Every surface, line set, marker set and arrow of a scene."""
    figure = go.Figure()
    for surface in scene.surfaces:
        colour, group, opacity = style(surface.kind)
        figure.add_trace(go.Mesh3d(
            **_xyz(surface.vertices), i=surface.triangles[:, 0], j=surface.triangles[:, 1],
            k=surface.triangles[:, 2], color=colour, opacity=opacity, flatshading=True,
            name=_name(surface.name, surface.shown, surface.total, "faces"),
            legendgroup=surface.kind, legendgrouptitle_text=group, showlegend=True,
            hovertemplate=f"{surface.name}<br>{group}<extra></extra>"))
    for lines in scene.lines:
        colour, group, _ = style(lines.kind)
        dashed = lines.kind in (ZONE, WAKE)
        figure.add_trace(go.Scatter3d(
            **_xyz(lines.points), mode="lines", connectgaps=False,
            line={"color": colour, "width": 2 if lines.kind == CUT else 4,
                  "dash": "dash" if dashed else "solid"},
            name=_name(lines.name, lines.shown, lines.total, "cell edges")
            if lines.kind == CUT else lines.name,
            legendgroup=lines.kind, legendgrouptitle_text=group, hoverinfo="skip"))
    for markers in scene.markers:
        colour, group, _ = style(markers.kind)
        problem = markers.kind == PROBLEM
        figure.add_trace(go.Scatter3d(
            **_xyz(markers.points), mode="markers" if problem else "markers+text",
            text=list(markers.labels) or None, textposition="top center",
            marker={"size": 3 if problem else 5, "color": colour,
                    "symbol": "x" if problem else "diamond"},
            name=_name(markers.name, markers.shown, markers.total, "locations")
            if problem else markers.name,
            legendgroup=markers.kind, legendgrouptitle_text=group,
            hovertemplate=(f"{markers.name}<extra></extra>" if problem
                           else "%{text}<extra>seed point</extra>")))
    _add_arrows(figure, scene, arrows)
    # The first rotation axis points up on screen: a camera setting only, the
    # coordinates are never transformed (VAWT spec section 15).
    up = next((a.head_direction for a in arrows if a.kind == "axis" and a.head_direction),
              (0.0, 0.0, 1.0))
    figure.update_layout(
        title=title or None,
        scene={"aspectmode": "data", "xaxis_title": "x (m)", "yaxis_title": "y (m)",
               "zaxis_title": "z (m)",
               "camera": {"up": {"x": up[0], "y": up[1], "z": up[2]}}},
        legend={"itemsizing": "constant", "groupclick": "toggleitem"},
        margin={"l": 0, "r": 0, "t": 30 if title else 0, "b": 0},
        uirevision=scene.view,  # keep the camera across reruns of the same view
    )
    return figure


def _add_arrows(figure: go.Figure, scene: Scene, arrows: Sequence[Arrow]) -> None:
    size = 1.0
    if scene.bounds is not None:
        size = float(np.linalg.norm(np.subtract(scene.bounds[1], scene.bounds[0]))) or 1.0
    for arrow in arrows:
        colour = ARROW_STYLE.get(arrow.kind, "#8a8984")
        group = f"arrow-{arrow.kind}"
        title = {"axis": "Rotation axes", "rotation": "Rotation directions",
                 "flow": "Flow direction"}.get(arrow.kind, "Arrows")
        figure.add_trace(go.Scatter3d(
            **_xyz(np.asarray(arrow.path)), mode="lines", name=arrow.label,
            line={"color": colour, "width": 6, "dash": "solid" if arrow.defined else "dash"},
            legendgroup=group, legendgrouptitle_text=title,
            hovertemplate=f"{arrow.label}<extra></extra>"))
        if arrow.head is not None and arrow.head_direction is not None:
            (x, y, z), (u, v, w) = arrow.head, arrow.head_direction
            figure.add_trace(go.Cone(
                x=[x], y=[y], z=[z], u=[u], v=[v], w=[w], anchor="tip", sizemode="absolute",
                sizeref=0.04 * size, colorscale=[[0, colour], [1, colour]], showscale=False,
                showlegend=False, legendgroup=group, name=arrow.label,
                hovertemplate=f"{arrow.label}<extra></extra>"))
