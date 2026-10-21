"""Where an AMI pair's sum(weights) falls short (owner, M2a: "the fraction of
interface area below 0.95, where it is, and both interface radii").

postProcess AMIWeights with writeFields true writes each side's faces with a
per-face weightsSum (postProcessing/AMIWeights1/constant/<patch>_src.vtp and
<patch>_tgt.vtp; evidence 3). For each side: the share of its area below the
range's lower end, where those faces are (radius and position along the
rotation axis), which edge of the side that is, and the side's own extent.
E0 (Francis RU / DT): 0.21 % of the runner side's area, one row at its
outer edge, r 0.175 m; 0.044 % of the draft-tube side, at its inner edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from machines.msh_import.surface import BoundarySurface, Point, SurfaceError, read_vtp

WEIGHTS_DIR = "postProcessing/AMIWeights1/constant"


@dataclass(frozen=True)
class SideDetail:
    patch: str
    faces: int
    faces_below: int
    percent_below: float  # of the side's area
    r: tuple[float, float]  # the side's extent: radius about the axis
    a: tuple[float, float]  # and position along it
    below_r: tuple[float, float] | None
    below_a: tuple[float, float] | None
    edge: str  # where the low faces are on the side

    def sentence(self, low: float, axis: str) -> str:
        def span(values: tuple[float, float]) -> str:
            a, b = values
            return f"{a:.4g} m" if f"{a:.4g}" == f"{b:.4g}" else f"{a:.4g} to {b:.4g} m"

        if self.below_r is None or self.below_a is None:
            return (f"{self.patch}: no face below {low:g} (r {span(self.r)}, {axis} "
                    f"{span(self.a)}).")
        return (f"{self.patch}: {self.percent_below:.2g} % of its area ({self.faces_below} of "
                f"{self.faces} faces) below {low:g}, at r {span(self.below_r)}, {axis} "
                f"{span(self.below_a)} ({self.edge}); the side spans r {span(self.r)}, "
                f"{axis} {span(self.a)}.")

    def as_dict(self) -> dict[str, Any]:
        return {"patch": self.patch, "faces": self.faces, "faces_below": self.faces_below,
                "percent_below": self.percent_below, "r_m": list(self.r), "axial_m": list(self.a),
                "below_r_m": list(self.below_r) if self.below_r else None,
                "below_axial_m": list(self.below_a) if self.below_a else None,
                "edge": self.edge}


def _span(values: np.ndarray) -> tuple[float, float]:
    return float(values.min()), float(values.max())


def _edge(r: tuple[float, float], a: tuple[float, float], below_r: tuple[float, float],
          below_a: tuple[float, float]) -> str:
    """Which edge of the side the low faces are on: by radius when the side
    spans radii (a disc or cone), else along the axis (a cylinder)."""
    span_r, span_a = r[1] - r[0], a[1] - a[0]
    if span_r >= 0.1 * max(span_a, 1e-12):
        middle = (below_r[0] + below_r[1]) / 2
        if middle >= r[0] + 0.75 * span_r:
            return "its outer (largest-radius) edge"
        if middle <= r[0] + 0.25 * span_r:
            return "its inner (smallest-radius) edge"
        return "between its edges"
    middle = (below_a[0] + below_a[1]) / 2
    if middle >= a[0] + 0.75 * span_a:
        return "its end at the larger axial position"
    if middle <= a[0] + 0.25 * span_a:
        return "its end at the smaller axial position"
    return "between its ends"


def side_detail(path: Path, low: float, origin: Point, axis: Point) -> SideDetail | None:
    """The detail of one side's weights file; None if it cannot be read."""
    try:
        arrays = read_vtp(path)
        points = np.asarray(arrays["Points"], dtype=np.float64).reshape(-1, 3)
        connectivity = np.asarray(arrays["connectivity"], dtype=np.int64)
        ends = np.asarray(arrays["offsets"], dtype=np.int64)
        sums = np.asarray(arrays["weightsSum"], dtype=np.float64)
    except (SurfaceError, KeyError):
        return None
    if not len(ends) or len(sums) != len(ends):
        return None
    side = BoundarySurface(points, np.concatenate([[0], ends]), connectivity,
                           np.zeros(len(ends), dtype=np.int64), ("side",))
    faces = np.arange(len(ends))
    areas, _ = side.area_vectors(faces)
    e = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    relative = points - np.asarray(origin, dtype=float)
    along = relative @ e
    radial = np.linalg.norm(relative - np.outer(along, e), axis=1)
    r, a = _span(radial), _span(along)
    below = sums < low
    patch = path.stem.rsplit("_", 1)[0]
    if not below.any():
        return SideDetail(patch, len(faces), 0, 0.0, r, a, None, None, "")
    labels = side.labels(faces[below])
    below_r, below_a = _span(radial[labels]), _span(along[labels])
    total = float(areas.sum())
    return SideDetail(patch, len(faces), int(below.sum()),
                      100.0 * float(areas[below].sum()) / total if total else 0.0,
                      r, a, below_r, below_a, _edge(r, a, below_r, below_a))


def pair_details(case: Path, pair: tuple[str, str], low: float, origin: Point,
                 axis: Point) -> list[SideDetail]:
    """Both sides of a pair (cases/merged/), each from its own weights file."""
    found = []
    for patch in pair:
        for path in sorted((case / WEIGHTS_DIR).glob(f"{patch}_*.vtp")):
            if path.stem.rsplit("_", 1)[0] == patch:
                detail = side_detail(path, low, origin, axis)
                if detail is not None:
                    found.append(detail)
    return found
