"""How much RAM and time each heavy step of an imported mesh needs (owner, M2a):
models from the E0 measurements on this machine (v2512, 32 cores), scaled by
cell count, with a 1.25 margin; recalibrated by the last measured peak of the
same step on the same mesh.

E0 (peak RSS and wall time) and the model per step:

- conversion: GV 0.15 GB / 0.4 s (48 MB file) to SC 2.47 GB / 7.8 s (637 MB):
  5.5 x the file size, 13 ms per MB
- surface (topoSet + surfaceMeshExtract): 23 M cells 9.2 GB / 29 s (22 zones),
  SC 0.9 GB / 15 s: 0.4 kB and 1.3 us a cell
- checkMesh (-allTopology -meshQuality, sets, fields): GV ring 11.1 GB / 42 s
  (7.9 M hexahedra), RU ring 14.9 GB / 57 s, SC 7.1 GB / 40 s (3.4 M mixed),
  23 M 31.9 GB / 170 s: hex-dominant 1.45 kB and 5.5 us, mixed 2.1 kB and
  11.6 us a cell
- views (surface and cuts): 23 M 10.8 GB / 45 s: 0.47 kB and 2 us a cell
- AMI weights at rest: 23 M 9.2 GB / 8.6 s: 0.4 kB and 0.4 us a cell
- one step of the revolution check (moveDynamicMesh -checkAMI): 23 M 15.7 GB,
  85 s a step: 0.7 kB and 3.7 us a cell a step

A machine of several files (M2b, the same 23 M machine):

- the assembly (copies, stitches, merges; machines.msh_import.parts): rings GV
  7.9 M 61 s / 7.5 GB, RU 10.8 M 136 s / 10.2 GB, the merges 133 s / 18.2 GB:
  0.95 kB and 14.4 us a cell of the machine
- checkMesh without -allTopology (-meshQuality, sets, fields): 23.5 GB / 90 s:
  1.05 kB and 3.9 us a cell; mergeOrSplitBaffles -detectOnly in its place for
  the baffles: 9.2 GB / 7.8 s: 0.4 kB and 0.34 us a cell

Weak evidence, said where it is shown: one machine, four parts and one
assembly; the cell types alone change the figures about 2 times.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MARGIN = 1.25
WARN_SHARE = 0.70  # of the free RAM
HEX_DOMINANT = 0.8  # share of hexahedra for the hex-dominant figures
HISTORY = "reports/resource_history.json"
NOTE = ("Estimates scaled from one machine's E0 measurements (four parts, one assembly; "
        "v2512, 32 cores) with a 1.25 margin, or this mesh's last measured peak; the cell "
        "types alone change them about 2 times.")


@dataclass(frozen=True)
class Model:
    ram_per_cell: tuple[float, float]  # bytes: hex-dominant, mixed
    time_per_cell: tuple[float, float]  # seconds: hex-dominant, mixed


MODELS = {
    "surface": Model((400.0, 400.0), (1.3e-6, 1.3e-6)),
    "checkMesh": Model((1450.0, 2100.0), (5.5e-6, 11.6e-6)),
    "views": Model((470.0, 470.0), (2.0e-6, 2.0e-6)),
    "amiWeights": Model((400.0, 400.0), (0.4e-6, 0.4e-6)),
    "revolution_step": Model((700.0, 700.0), (3.7e-6, 3.7e-6)),
    "assembly": Model((950.0, 950.0), (14.4e-6, 14.4e-6)),
    "checkMeshTopology": Model((1050.0, 1050.0), (3.9e-6, 3.9e-6)),
    "baffles": Model((400.0, 400.0), (0.34e-6, 0.34e-6)),
}
CONVERT_RAM_PER_BYTE = 5.5
CONVERT_S_PER_MB = 0.013
LABELS = {"convert": "conversion", "surface": "boundary surface", "checkMesh": "checkMesh",
          "views": "views for the 3D pane", "amiWeights": "AMI weights",
          "revolution_step": "one step of the revolution check",
          "assembly": "assembly of the parts", "checkMeshTopology": "checkMesh",
          "baffles": "baffle count"}


@dataclass(frozen=True)
class Estimate:
    step: str
    ram_bytes: float
    seconds: float
    basis: str  # what it was scaled from
    measured: bool = False  # the last real peak was larger than the model

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate(step: str, cells: int, hex_share: float | None = None,
             file_bytes: int | None = None, history: dict[str, Any] | None = None,
             key: str = "") -> Estimate:
    """The model's figure with the margin, or the last measured peak of this
    step on this mesh (`key`) with the margin when that is larger."""
    if step == "convert":
        size = float(file_bytes or 0)
        found = Estimate(step, MARGIN * CONVERT_RAM_PER_BYTE * size,
                         MARGIN * CONVERT_S_PER_MB * size / 1e6,
                         f"{size / 1e6:.0f} MB file x {CONVERT_RAM_PER_BYTE} (E0)")
    else:
        model = MODELS[step]
        hexes = hex_share is not None and hex_share >= HEX_DOMINANT
        which = 0 if hexes else 1
        kind = ("hex-dominant" if hexes else "mixed or unknown cell types")
        found = Estimate(step, MARGIN * model.ram_per_cell[which] * cells,
                         MARGIN * model.time_per_cell[which] * cells,
                         f"{cells:,} cells, {kind} (E0)")
    last = (history or {}).get(key, {}).get(step) if key else None
    if isinstance(last, dict) and float(last.get("peak_bytes", 0)) * MARGIN > found.ram_bytes:
        return Estimate(step, MARGIN * float(last["peak_bytes"]),
                        max(found.seconds, MARGIN * float(last.get("seconds", 0.0))),
                        f"measured on this mesh: {float(last['peak_bytes']) / 1e9:.1f} GB peak",
                        measured=True)
    return found


def read_history(root: Path) -> dict[str, Any]:
    try:
        found = json.loads((root / HISTORY).read_text("utf-8"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def record(root: Path, key: str, step: str, peak_bytes: int, seconds: float) -> None:
    """The measured peak RSS and time of a step on a mesh (`key`)."""
    history = read_history(root)
    history.setdefault(key, {})[step] = {"peak_bytes": peak_bytes, "seconds": seconds}
    path = root / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")


def peak_rss(pid: int) -> int | None:
    """The process's peak resident set size so far (VmHWM), in bytes."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text("utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None
