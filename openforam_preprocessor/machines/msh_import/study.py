"""A smaller study of a machine of several files (M2b-4; owner answer 1): the
rotating part and the part with the outlet (a Francis runner and its draft
tube), offered when solving the whole machine needs more memory than WSL has.

The study is a new project beside this one, made from the saved project:
its parts, the interfaces between them, and their patches as typed. The
rotating part's interface sides that led to a part left out are its inlet
(the flow enters where the guide vanes were); a flow-rate inlet keeps the
machine's total, split between those sides by their areas. Everything is
listed for the user to check before running; nothing else is changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from machines.config import MachineParts, MachineProjectConfig, PatchType
from machines.msh_import.interfaces import part_of


def study_parts(config: MachineProjectConfig) -> tuple[str, ...] | None:
    """The prefixes kept: the rotating part and the part(s) with an outlet;
    None when there is no smaller study (not several files, nothing to drop)."""
    mesh = config.imported_mesh
    if not isinstance(mesh, MachineParts) or len(mesh.zones) != 1:
        return None
    rotating = mesh.zones[0].cell_zone
    outlets = {part_of(p.source.ref) for p in config.patches if p.type is PatchType.OUTLET}
    kept = tuple(p.prefix for p in mesh.parts if p.prefix == rotating or p.prefix in outlets)
    return kept if 0 < len(kept) < len(mesh.parts) else None


def study_draft(config: MachineProjectConfig, areas: Mapping[str, float],
                name: str) -> tuple[dict[str, Any], list[str]] | None:
    """The study's configuration (raw) and what was decided, for the user;
    `areas`: the machine patches' areas (m^2) measured from the mesh."""
    kept = study_parts(config)
    mesh = config.imported_mesh
    if kept is None or not isinstance(mesh, MachineParts):
        return None
    rotating = mesh.zones[0].cell_zone
    raw = config.model_dump(mode="json")
    raw["project_name"] = name
    found = raw["imported_mesh"]
    found["parts"] = [p for p in found["parts"] if p["prefix"] in kept]
    inlets: list[str] = []
    interfaces = []
    for interface in found.get("interfaces") or []:
        sides = (interface["first"], interface["second"])
        owners = [part_of(s[0]) for s in sides]
        if all(o in kept for o in owners):
            interfaces.append(interface)
        elif rotating in owners:  # the rotating part's side towards a part left out
            inlets += sides[owners.index(rotating)]
    found["interfaces"] = interfaces
    found["ami_pairs"] = [p for p in found.get("ami_pairs") or []
                          if part_of(p["first"]) in kept and part_of(p["second"]) in kept]
    raw["patches"] = [p for p in raw["patches"] if part_of(p["source"]["ref"]) in kept
                      and p["type"] != PatchType.INLET.value]
    raw["patches"] += [{"name": n.replace("-", "_").replace(".", "_"), "type": "INLET",
                        "source": {"kind": "MESH_PATCH", "ref": n}} for n in inlets]
    notes = [f"Parts kept: {', '.join(kept)} (the rotating part and the outlet's).",
             "Inlet: " + (", ".join(inlets) or "none found: choose it in the patch table")
             + " (the rotating part's sides towards the parts left out)."]
    physics = raw.get("physics")
    if physics:
        total = sum(float(i.get("volumetric_flow_rate") or 0.0)
                    for i in (physics.get("inlets") or {}).values()
                    if i.get("kind") == "FLOW_RATE")
        known = {n: areas.get(n) for n in inlets}
        if total > 0 and inlets and all(a for a in known.values()):
            whole = sum(a for a in known.values() if a)
            physics["inlets"] = {
                p["name"]: {"kind": "FLOW_RATE",
                            "volumetric_flow_rate": total * float(known[p["source"]["ref"]] or 0)
                            / whole}
                for p in raw["patches"] if p["source"]["ref"] in known}
            notes.append(f"Flow rate: the machine's {total:g} m3/s split by area between the "
                         "inlet sides.")
        else:
            physics["inlets"] = {}
            notes.append("The inlet values were not set: set them under Boundary.")
    return raw, notes
