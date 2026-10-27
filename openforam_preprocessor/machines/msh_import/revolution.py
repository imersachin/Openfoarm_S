"""The AMI weights over one revolution of an imported mesh (M1c, owner).

The run checks the weights at rest only (postProcess AMIWeights on the mesh
as converted). This optional check turns the rotating cell zone through one
revolution without solving the flow: moveDynamicMesh -checkAMI (v2512)
moves the zone by solid-body rotation and prints each pair's sum(weights)
at every step. On the M1 rotating fixture it took 0.67 s for 36 steps of 10
degrees and gave 0.9946-1.104, against 1.000 at rest (docs/msh_import_notes.md).

It runs on a copy of the assembled mesh in cases/ami_revolution/, removed
afterwards (-checkAMI writes VTK files of the AMI patches there), and reports
in reports/ami_revolution.json with the hash of what it checked: the mesh and
the motion (the zone's cell zone, axis and origin; check_key).

M2a: it runs in the background (MachineService.start_revolution), with its
progress read from the log ("Time =" lines), a Cancel, and the heavy-step
lock; on a large mesh coarser sweeps are offered (sweeps(); E0: one step
85 s at 23 M cells, 36 x 10 deg about 51 min). The label always states the
steps.

M2b-4: when the rotating zone is a part made of N copies of one passage, the
check turns it through one blade pitch (360 / N deg) in 12 steps: its mesh
repeats every pitch, so the weights over a pitch are those over a revolution
(evidence 1.4: on the 23 M machine RU-DT at 24 deg read 0.648203 / 0.756369,
the values at rest to every printed digit; 12 x 2 deg 17 min). 6 x 4 deg is
offered when 12 steps are estimated over 10 min (owner).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.artifacts.hashing import files_under, hash_files, sha256_json
from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage
from machines.config import MachineParts, MachineProjectConfig
from machines.mesh_folder import AmiWeights, check_ami_weights, parse_ami_weights
from machines.msh_import import interfaces
from machines.msh_import.cases import MERGED_CASE, coupled_pairs
from machines.msh_import.pipeline import _converter_files
from machines.pipeline import _EXCLUDED, POLY_MESH, _issue
from openfoam.commands import MeshingStep
from openfoam.dictionary import OpenFOAMFileWriter, foam_header, foam_scalar, foam_vector
from openfoam.runner import OpenFOAMRunner, RunStatus

REVOLUTION_CASE = "ami_revolution"
REVOLUTION_REPORT = "reports/ami_revolution.json"
REVOLUTION_LOG = "01_moveDynamicMesh.log"
EXECUTABLES = ("moveDynamicMesh",)
STEPS = 36  # 10 degrees per step
LONG_S = 600.0  # above this estimate, coarser sweeps are offered (owner, M2a)
# Coarser sweeps, the preferred first: on a 15-blade runner 45 deg steps test 8
# positions, 30 deg steps only 4 (owner).
COARSER = (8, 12)
# One revolution per second: the time is the fraction of a turn.
OMEGA = 2 * math.pi
PITCH_STEPS = 12  # over one blade pitch (owner, M2b)
PITCH_COARSER = (6,)
_TIME = re.compile(r"^Time = ", re.MULTILINE)
_FAILED = re.compile(r"^\s*Failed (\d+) mesh checks", re.MULTILINE)


def over_revolution(steps: int = STEPS) -> str:
    return f"over one revolution ({steps} x {360 / steps:g} deg)"


def progress(root: Path) -> int:
    """Steps done so far by a running check (its log's "Time =" lines)."""
    log = root / "logs" / REVOLUTION_CASE / REVOLUTION_LOG
    try:
        return len(_TIME.findall(log.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0


def sweeps(step_seconds: float | None) -> tuple[int, ...]:
    """The step counts to offer: 36 x 10 deg first (the default); when that
    is estimated over LONG_S, also 8 x 45 deg (preferred) and 12 x 30 deg."""
    if step_seconds is None or step_seconds * STEPS <= LONG_S:
        return (STEPS,)
    return (STEPS, *COARSER)


def pitch_of(config: MachineProjectConfig) -> tuple[float, str] | None:
    """(pitch in deg, part name) when the rotating zone is a part of N > 1
    copies of a passage (a machine of several files); None otherwise."""
    mesh = config.imported_mesh
    if not isinstance(mesh, MachineParts) or len(mesh.zones) != 1:
        return None
    part = next((p for p in mesh.parts if p.prefix == mesh.zones[0].cell_zone), None)
    if part is None or not part.passages or part.passages < 2:
        return None
    return 360.0 / part.passages, part.name


def sweep_label(config: MachineProjectConfig, steps: int) -> str:
    """What a check of `steps` steps turns, stated with its steps."""
    pitch = pitch_of(config)
    if pitch is None:
        return over_revolution(steps)
    degrees, name = pitch
    return (f"over one blade pitch ({degrees:g} deg, {steps} x {degrees / steps:g} deg), "
            f"equivalent to a full revolution because the {name} mesh repeats every "
            f"{degrees:g} deg")


def sweep_options(config: MachineProjectConfig, step_seconds: float | None) -> tuple[int, ...]:
    """The step counts to offer: a pitch's 12 (and 6 when 12 are estimated over
    LONG_S), or a revolution's (sweeps)."""
    if pitch_of(config) is None:
        return sweeps(step_seconds)
    if step_seconds is None or step_seconds * PITCH_STEPS <= LONG_S:
        return (PITCH_STEPS,)
    return (PITCH_STEPS, *PITCH_COARSER)


def default_steps(config: MachineProjectConfig) -> int:
    return STEPS if pitch_of(config) is None else PITCH_STEPS


def mesh_hash(root: Path) -> str:
    """The assembled mesh the check is for (its polyMesh files)."""
    folder = root / "cases" / MERGED_CASE / POLY_MESH
    files = files_under(folder, exclude_dirs=_EXCLUDED) if folder.is_dir() else []
    return sha256_json(hash_files(root, files))


def check_key(root: Path, config: MachineProjectConfig, steps: int = STEPS) -> str:
    """What a check over one revolution is of: the assembled mesh and the files
    that turn it (a changed axis, origin or cell zone is another check)."""
    return sha256_json([mesh_hash(root), revolution_files(config, steps)])


def revolution_files(config: MachineProjectConfig, steps: int = STEPS) -> dict[str, str]:
    """controlDict (moveDynamicMesh, one second, nothing written), schemes and
    solution for the mesh checks, and the zone's solid-body rotation."""
    mesh = config.imported_mesh
    assert mesh is not None and len(mesh.zones) == 1
    zone = mesh.zones[0]
    axis = [0.0, 0.0, 0.0]
    axis["xyz".index(zone.axis.value)] = 1.0
    pitch = pitch_of(config)
    turn = 1.0 if pitch is None else pitch[0] / 360.0  # the fraction of a revolution
    files = _converter_files()
    files["system/controlDict"] = foam_header("controlDict") + f"""
application     moveDynamicMesh;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {"1" if pitch is None else foam_scalar(turn)};
deltaT          {foam_scalar(turn / steps)};
writeControl    timeStep;
writeInterval   {steps + 1};
"""
    files["constant/dynamicMeshDict"] = foam_header("dynamicMeshDict") + f"""
dynamicFvMesh   dynamicMotionSolverFvMesh;
motionSolverLibs (fvMotionSolvers);
motionSolver    solidBody;
cellZone        {zone.cell_zone};
solidBodyMotionFunction  rotatingMotion;
origin          {foam_vector(zone.origin.x, zone.origin.y, zone.origin.z)};
axis            {foam_vector(*axis)};
omega           {foam_scalar(OMEGA)};
"""
    return files


@dataclass(frozen=True)
class RevolutionResult:
    """Min and max sum(weights) of each pair over all angles."""

    weights: tuple[AmiWeights, ...]
    steps: int  # steps the log shows
    failed_steps: int  # steps where the moved mesh failed a check
    mesh_sha256: str
    issues: tuple[Issue, ...] = ()
    log: str | None = None
    check_sha256: str = ""  # check_key: the mesh and the motion

    @property
    def done(self) -> bool:
        return self.steps > 0 and not any(i.is_stopping for i in self.issues)


def unavailable(config: MachineProjectConfig, root: Path) -> Issue | None:
    """Why the check cannot run now, or None."""
    mesh = config.imported_mesh
    if mesh is None or not coupled_pairs(mesh):
        why = "The project has no imported mesh with an AMI pair."
    elif len(mesh.zones) != 1:
        why = f"The check turns one rotating zone; this mesh has {len(mesh.zones)}."
    elif not (root / "cases" / MERGED_CASE / POLY_MESH / "boundary").is_file():
        why = "The mesh has not been imported yet; run the import first."
    else:
        return None
    return _issue(IssueSeverity.BLOCKING, IssueCategory.CONFIGURATION,
                  IssueStage.MESH_VALIDATION, "AMI_REVOLUTION_UNAVAILABLE", why,
                  "Import the mesh with its AMI pair, then check again.")


async def check_revolution(root: Path, config: MachineProjectConfig, *,
                           runner: OpenFOAMRunner, env: Mapping[str, str] | None,
                           ami_range: tuple[float, float],
                           steps: int = STEPS,
                           cancel_event: asyncio.Event | None = None) -> RevolutionResult:
    """Turn the zone through one revolution on a copy of the assembled mesh and
    report the weights. The caller holds the run lock."""
    digest = mesh_hash(root)
    blocked = unavailable(config, root)
    if blocked is not None:
        return RevolutionResult((), 0, 0, digest, (blocked,))
    mesh = config.imported_mesh
    assert mesh is not None
    logs = root / "logs" / REVOLUTION_CASE
    (logs / REVOLUTION_LOG).unlink(missing_ok=True)  # progress() reads this check's only
    if cancel_event is not None and cancel_event.is_set():
        return RevolutionResult((), 0, 0, digest, (_issue(
            IssueSeverity.INFO, IssueCategory.TIMEOUT_CANCELLATION,
            IssueStage.MESH_VALIDATION, "AMI_REVOLUTION_CANCELLED",
            "The check was cancelled before it started.", "Start it again when ready."),))
    case = root / "cases" / REVOLUTION_CASE
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(root / "cases" / MERGED_CASE / POLY_MESH, case / POLY_MESH,
                    ignore=shutil.ignore_patterns(*_EXCLUDED))
    for relative, body in revolution_files(config, steps).items():
        OpenFOAMFileWriter.write_if_changed(case / relative, body)
    try:
        result = await runner.run_step(
            MeshingStep(("moveDynamicMesh", "-case", ".", "-checkAMI"), REVOLUTION_LOG,
                        IssueStage.MESH_VALIDATION),
            case_root=case, env=env, logs_dir=logs, cancel_event=cancel_event)
    finally:
        shutil.rmtree(case, ignore_errors=True)  # the VTK files of -checkAMI
    log = Path(result.log_path)
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    done = len(_TIME.findall(text))
    failed = len(_FAILED.findall(text))
    pairs = list(coupled_pairs(mesh))
    issues: list[Issue] = list(result.issues)
    if result.status is RunStatus.CANCELLED:
        issues.append(_issue(
            IssueSeverity.INFO, IssueCategory.TIMEOUT_CANCELLATION,
            IssueStage.MESH_VALIDATION, "AMI_REVOLUTION_CANCELLED",
            f"The check was cancelled after {done} of {steps} steps.",
            "Start it again when ready."))
    elif result.status is not RunStatus.SUCCESS or done < steps:
        issues.append(_issue(
            IssueSeverity.ERROR, IssueCategory.EXECUTION, IssueStage.MESH_VALIDATION,
            "AMI_REVOLUTION_FAILED", f"moveDynamicMesh did not finish the revolution "
            f"({done} of {steps} steps).", f"Read logs/{REVOLUTION_CASE}/{REVOLUTION_LOG}.",
            log=str(log)))
    else:
        partial = interfaces.partial(mesh) if isinstance(mesh, MachineParts) else ()
        when = sweep_label(config, steps)
        issues += check_ami_weights(text, [p for p in pairs if p not in partial], ami_range,
                                    when=when, every=True)
        issues += interfaces.weights_notes(partial, text, when, every=True)
    if failed:
        issues.append(_issue(
            IssueSeverity.WARNING, IssueCategory.MESHING, IssueStage.MESH_VALIDATION,
            "AMI_REVOLUTION_MESH_FAILED", f"The turned mesh failed a mesh check at {failed} "
            f"of {steps} steps.", f"Read logs/{REVOLUTION_CASE}/{REVOLUTION_LOG}.",
            failed_steps=failed))
    found = RevolutionResult(parse_ami_weights(text, every=True), done, failed, digest,
                             tuple(issues), str(log), check_key(root, config, steps))
    _write_report(root, found, steps, ami_range, sweep_label(config, steps),
                  interfaces.partial(mesh) if isinstance(mesh, MachineParts) else ())
    return found


def _write_report(root: Path, result: RevolutionResult, steps: int,
                  ami_range: tuple[float, float], when: str | None = None,
                  acmi: tuple[tuple[str, str], ...] = ()) -> None:
    path = root / REVOLUTION_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "finished_at": datetime.now(UTC).isoformat(),
        "when": when or over_revolution(steps), "steps": result.steps,
        "planned_steps": steps,
        "failed_steps": result.failed_steps, "mesh_sha256": result.mesh_sha256,
        "check_sha256": result.check_sha256,
        "range": list(ami_range), "weights": [asdict(w) for w in result.weights],
        **({"acmi_pairs": [list(p) for p in acmi]} if acmi else {}),
        "issues": [i.as_dict() for i in result.issues], "log": result.log,
        "note": "The zone is turned by solid-body rotation without solving the flow "
                "(moveDynamicMesh -checkAMI).",
    }, indent=2) + "\n", encoding="utf-8")


def read_report(root: Path) -> dict[str, object] | None:
    """The last check's report, or None."""
    try:
        found = json.loads((root / REVOLUTION_REPORT).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None
