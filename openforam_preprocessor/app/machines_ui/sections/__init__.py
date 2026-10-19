"""The section renderers of the machines UI, by section key (machines.profiles
SECTION_CATALOGUE). A profile lists which of them a machine shows."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

from app.machines_ui.sections import (
    bodies,
    boundary,
    case,
    domain,
    export,
    fluid,
    history,
    imported_mesh,
    layers,
    logs,
    mesh,
    operating_point,
    parts,
    patches,
    project,
    refinement,
    review,
    rotation,
    run,
    time_controls,
    zones,
)

RENDERERS: dict[str, ModuleType] = {
    "project": project, "bodies": bodies, "parts": parts, "domain": domain, "zones": zones,
    "imported_mesh": imported_mesh, "patches": patches, "refinement": refinement,
    "layers": layers, "fluid": fluid,
    "boundary": boundary, "rotation": rotation, "operating_point": operating_point,
    "time": time_controls, "review": review, "run": run, "mesh": mesh, "case": case,
    "export": export, "history": history, "logs": logs,
}


def renderer(key: str) -> Callable[..., None]:
    render: Callable[..., None] = RENDERERS[key].render
    return render
