"""How much memory and time solving the set-up case needs (M2b-4; owner
answer 1): a heuristic, never a guarantee.

Memory: bytes a cell measured on one well-posed case of the app's own setup
(REFERENCE), against what WSL has free now. Over it: a WARNING before any
solve, and the runner + draft tube study is offered (study.py). The app does
not run the solver; the owner decides on more WSL memory (.wslconfig).

Time a step: scaled from REFERENCE by cells per rank (perfect scaling assumed,
labelled "scaled") until the case's own solver log (case/log.*) has steps:
then measured, from the ExecutionTime between steps (the first step left
out: it includes reading and the first AMI build). A revolution's time uses
the time step the solver took (deltaT in the log, as maxCo set it) once
there is one, else the configured degrees a step: in REFERENCE the first
1 deg step reached Courant 82 and maxCo 2 cut the step to 0.6-4 us
(0.004-0.024 deg at 1000 rpm), a revolution about 100 times longer than at
1 deg a step.

REFERENCE, measured (v2512, 16 ranks, scotch): decomposePar 20 s / 7.9 GB;
pimpleFoam 22.0 GB peak (1.88 kB a cell). M2b-4's 70 s a step was with the
earlier settings (maxCo 2); since C1 an imported machine's case runs the
robust settings at a fixed step: STEP_SECONDS, measured over one blade pitch
(docs/c1_startup_notes.md 3.4).

The outer loop (C1, owner): each step that reached its outer-iteration limit
without residualControl's tolerances ("PIMPLE: not converged within N
iterations") is counted from the log, so a non-converged transient shows.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

# REFERENCE: the app's runner + draft tube case of the owner's Francis machine
# (docs/m2b_assembly_notes.md, M2b-4; docs/c1_startup_notes.md, C1), pimpleFoam
# on 16 ranks, v2512.
REFERENCE = ("runner + draft tube, 11.7 M cells, 16 ranks, v2512, the robust settings "
             "(C1 evidence)")
RAM_PER_CELL = 1884.0  # bytes: 22.0 GB / 11,671,680 cells
STEP_SECONDS = 266.6  # C1: median s a step at 1 deg, robust settings, 16 ranks
SECONDS_PER_CELL_STEP = STEP_SECONDS * 16 / 11_671_680  # rank-seconds a cell a step
MARGIN = 1.25
_TIME = re.compile(r"^ExecutionTime = ([0-9.eE+-]+) s", re.MULTILINE)
_DELTA = re.compile(r"^deltaT = ([0-9.eE+-]+)", re.MULTILINE)
_NOT_CONVERGED = re.compile(r"^PIMPLE: not converged within (\d+) iterations", re.MULTILINE)


@dataclass(frozen=True)
class SolveEstimate:
    cells: int
    ram_bytes: float  # with the margin
    free_bytes: int | None
    ranks: int
    seconds_per_step: float
    measured: bool  # from the case's own log
    degrees_per_step: float
    basis: str
    step_from_log: bool = False  # degrees_per_step from the solver's deltaT
    outer: OuterLoop | None = None  # the log's outer-loop record (C1)

    @property
    def fits(self) -> bool | None:
        return None if self.free_bytes is None else self.ram_bytes <= self.free_bytes

    @property
    def hours_per_revolution(self) -> float:
        return self.hours_for(self.degrees_per_step)

    def hours_for(self, degrees: float) -> float:
        """Hours a revolution at `degrees` a step, at this time a step (C1: the
        time a step hardly depends on the step: 260 s at 2 deg, 267 s at 1)."""
        return self.seconds_per_step * 360.0 / degrees / 3600.0


@dataclass(frozen=True)
class OuterLoop:
    """Steps of a log and how many reached the outer-iteration limit."""

    steps: int
    not_converged: int
    limit: int | None


def outer_loop(log_text: str) -> OuterLoop | None:
    """The log's steps (ExecutionTime lines) and those that reached the
    outer-iteration limit without converging; None without a step."""
    steps = len(_TIME.findall(log_text))
    if not steps:
        return None
    limits = _NOT_CONVERGED.findall(log_text)
    return OuterLoop(steps, len(limits), int(limits[-1]) if limits else None)


def step_seconds(log_text: str) -> list[float]:
    """Seconds of each solver step after the first, from ExecutionTime lines."""
    times = [float(t) for t in _TIME.findall(log_text)]
    return [b - a for a, b in zip(times, times[1:], strict=False)]


def log_degrees(log_text: str, rpm: float) -> float | None:
    """Degrees a step the solver took: the median deltaT after the first step
    (maxCo adjusts it) times the rotation rate; None without one."""
    steps = [float(t) for t in _DELTA.findall(log_text)][1:]
    if not steps or not rpm:
        return None
    return statistics.median(steps) * abs(rpm) * 6.0


def case_log(case: Path) -> str:
    """The newest solver log in case/ (log.pimpleFoam or log.*) written after the
    case's mesh and controlDict, or "": an older log is of another case."""
    made = max((p.stat().st_mtime for p in (case / "system" / "controlDict",
                                            case / "constant" / "polyMesh" / "boundary")
                if p.is_file()), default=0.0)
    logs = sorted((p for p in case.glob("log.*") if p.stat().st_mtime >= made),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for log in logs:
        text = log.read_text(encoding="utf-8", errors="replace")
        if _TIME.search(text):
            return text
    return ""


def estimate(cells: int, free_bytes: int | None, ranks: int, degrees_per_step: float,
             log_text: str = "", rpm: float = 0.0) -> SolveEstimate:
    ranks = max(1, ranks)
    taken = log_degrees(log_text, rpm)
    measured = step_seconds(log_text)
    if len(measured) >= 2:
        seconds, basis = statistics.median(measured), (
            f"measured: the median of {len(measured)} steps of the case's own log")
    else:
        seconds = SECONDS_PER_CELL_STEP * cells / ranks
        basis = f"scaled from {REFERENCE} by cells per rank"
    return SolveEstimate(cells, MARGIN * RAM_PER_CELL * cells, free_bytes, ranks, seconds,
                         len(measured) >= 2, taken or degrees_per_step, basis, taken is not None,
                         outer_loop(log_text))
