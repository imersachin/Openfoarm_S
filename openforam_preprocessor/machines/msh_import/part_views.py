"""What the UI shows of each part of a machine of several files (M2b), and the
"Read the parts" conversions it is shown from.

Plain data for MachineService, read from a draft (it may be incomplete): each
file's header detection, its box, whether it has been converted (at scale 1,
cases/part_<P>_convert/, with its surface) and, once it has and the machine
axis is set, the periodic pairs and passage count found about the axis. A
passage count is shown for the user to confirm; nothing is applied by itself.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from machines.msh_import import parts, surface
from machines.msh_import.cases import converter
from machines.msh_import.detect import MeshFileInfo
from machines.msh_import.inspection import MeshInspection, suggest_types
from machines.msh_import.pipeline import SCRATCH_RECORD, scratch_record
from machines.msh_import.validation import machine_info_of, read_mesh_file

# Part roles offered (owner's plan: SC, GV, RU, DT or custom): prefix -> name.
ROLES = {"SC": "Spiral casing", "GV": "Guide vanes", "RU": "Runner", "DT": "Draft tube"}
_WORDS = {"SC": ("sc", "spiral", "casing", "volute", "sv", "stay"),
          "GV": ("gv", "guide", "vane", "vanes", "wicket", "distributor"),
          "RU": ("ru", "runner", "rotor", "impeller", "wheel"),
          "DT": ("dt", "draft", "draught", "diffuser")}
_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def suggest_role(path: Path) -> str | None:
    """The role a file's name suggests ("GV-03-91-AG5.msh": GV), or None."""
    words = [w for w in re.split(r"[^a-z]+", path.stem.lower()) if w]
    for role, names in _WORDS.items():
        if any(w in names for w in words[:3]):
            return role
    return None


@dataclass(frozen=True)
class PartView:
    index: int
    name: str
    prefix: str
    path: Path
    info: MeshFileInfo
    sha256: str | None
    converted: bool  # cases/part_<P>_convert/ is this file, with its surface
    periodicity: parts.Periodicity | None  # None: not converted, or no axis yet
    note: str = ""  # why there is no periodicity


def _axis(mesh: Mapping[str, Any]) -> tuple[parts.Point, parts.Point] | None:
    axis, origin = mesh.get("axis"), mesh.get("origin")
    if axis not in _AXES or not isinstance(origin, Mapping):
        return None
    try:
        point = (float(origin["x"]), float(origin["y"]), float(origin["z"]))
    except (KeyError, TypeError, ValueError):
        return None
    return point, _AXES[str(axis)]


def converted(root: Path, prefix: str, info: MeshFileInfo, digest: str | None,
              tool: str) -> Path | None:
    """The part's conversion folder when it is of this file and tool, with a surface."""
    folder = root / "cases" / parts.convert_case(prefix)
    if digest is None:
        return None
    try:
        found = json.loads((folder / SCRATCH_RECORD).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict):
        return None
    found.pop("box", None)
    if found != scratch_record(info, digest, tool) or surface.load(folder) is None:
        return None
    return folder


def views(root: Path, mesh: Mapping[str, Any], tool: Any) -> list[PartView]:
    """Each part of the draft's imported_mesh.parts with a file. `tool(names)`:
    the tool identity a conversion is recorded with."""
    axis = _axis(mesh)
    found: list[PartView] = []
    for index, part in enumerate(mesh.get("parts") or []):
        if not isinstance(part, Mapping) or not part.get("source_path"):
            continue
        path = Path(str(part["source_path"]))
        info, digest = read_mesh_file(path)
        prefix = str(part.get("prefix") or "")
        prefix = prefix if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,7}", prefix) else ""
        folder = (converted(root, prefix, info, digest, tool((converter(info),)))
                  if prefix and info.readable else None)
        periodicity, note = None, ""
        if folder is None:
            note = "not read yet"
        elif axis is None:
            note = "the machine axis is not set"
        else:
            shape = surface.load(folder)
            periodicity = parts.detect(shape, *axis) if shape is not None else None
        found.append(PartView(index, str(part.get("name") or ""), prefix, path, info, digest,
                              folder is not None, periodicity, note))
    return found


def summary(view: PartView) -> str:
    """"Guide vanes: 1 of 28 passages (12.857143 deg; 3 periodic pairs)"."""
    found = view.periodicity
    if found is None:
        return view.note
    if found.whole_ring:
        return "a whole ring: 360 deg, no periodic pair"
    if found.passages is None:
        return f"no passage count: {found.problem}"
    return (f"1 of {found.passages} passages ({found.pitch_deg:.6f} deg; {len(found.pairs)} "
            f"periodic pair{'s' if len(found.pairs) != 1 else ''}: "
            + ", ".join(f"{a} / {b}" for a, b, _ in found.pairs) + ")")


def confirmed(view: PartView, part: Mapping[str, Any]) -> bool:
    """Whether the draft's passages and pairs are what was found."""
    found = view.periodicity
    if found is None or found.passages is None or part.get("passages") != found.passages:
        return False
    pairs = {frozenset((p.get("first"), p.get("second")))
             for p in part.get("periodic") or [] if isinstance(p, Mapping)}
    return pairs == {frozenset((a, b)) for a, b, _ in found.pairs}


def confirmation(view: PartView) -> dict[str, Any] | None:
    """The draft values a confirmation sets: passages and periodic pairs."""
    found = view.periodicity
    if found is None or found.passages is None:
        return None
    return {"passages": found.passages,
            "periodic": [{"first": a, "second": b} for a, b, _ in found.pairs]}


def to_read(root: Path, mesh: Mapping[str, Any], tool: Any,
            ) -> Sequence[tuple[str, MeshFileInfo, str]]:
    """(prefix, info, sha256) of each part with a file and a prefix not converted yet."""
    pending = []
    for view in views(root, mesh, tool):
        if view.prefix and view.info.readable and view.sha256 and not view.converted:
            pending.append((view.prefix, view.info, view.sha256))
    return pending


def machine_inspection(mesh: Mapping[str, Any]) -> MeshInspection | None:
    """The draft's parts as one mesh (validation.machine_info): its prefixed
    patches and zones, with suggested types; None before a part has a file and
    a prefix."""
    entries = []
    for part in mesh.get("parts") or []:
        if not isinstance(part, Mapping) or not part.get("source_path"):
            continue
        prefix = str(part.get("prefix") or "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,7}", prefix):
            continue
        periodic = {str(n) for p in part.get("periodic") or [] if isinstance(p, Mapping)
                    for n in (p.get("first"), p.get("second")) if n}
        passages = part.get("passages")
        entries.append((prefix, Path(str(part["source_path"])), periodic,
                        passages if isinstance(passages, int) and passages > 0 else 1))
    if not entries:
        return None
    info = machine_info_of(entries)
    return MeshInspection(info, None, None, "", suggest_types(info))
