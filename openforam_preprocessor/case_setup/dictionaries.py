"""system/ and constant/ dictionaries of the case (sections 9.6 and 9.7; G6).

Each setting comes from an OpenFOAM v2512 tutorial or template, as G0 used
them (notes section 4), and is proven again by the G6 solver runs:

    controlDict:   tutorials/incompressible/pimpleFoam/RAS/propeller/system/
                   controlDict (adjustTimeStep, maxCo, writeControl adjustable);
                   maxDeltaT as tutorials/incompressible/pimpleFoam/RAS/
                   rotatingFanInRoom/system/controlDict; AMIWeights as
                   .../propeller/system/AMIWeights
    fvSchemes:     .../propeller/system/fvSchemes; div(phi,omega) and wallDist
                   from etc/templates/inflowOutflowRotating/system/fvSchemes
    fvSolution:    .../propeller/system/fvSolution, omega added to U and k;
                   above 70 deg mesh non-orthogonality 2 non-orthogonal
                   correctors and nCorrectors 2 (owner, after M2a; notes 13.9)
    dynamicMeshDict, transportProperties: .../propeller/constant
    turbulenceProperties: etc/templates/inflowOutflowRotating/constant (k-omega
                   SST); .../laminar/mixerVesselAMI2D/mixerVesselAMI2D/constant
                   (laminar)
"""

from __future__ import annotations

from case_setup.physics import StartUp
from case_setup.setup import NON_ORTHO_CORRECTORS, PRESSURE_CORRECTORS, CaseSetup
from openfoam.dictionary import foam_header, foam_scalar, foam_vector

SOLVER = "pimpleFoam"
ALLRUN = "Allrun"  # an imported machine's run order (C1)


def control_dict(setup: CaseSetup) -> str:
    """The time step adjusts to maxCo and never exceeds the configured rotation
    angle (maxDeltaT). The first step also respects the Courant estimate. An
    imported machine steps by the angle alone (C1, imported_control_dict)."""
    time = setup.time
    if setup.imported:
        return imported_control_dict(setup)
    return foam_header("controlDict") + f"""
application     {SOLVER};
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {foam_scalar(time.end_time)};
deltaT          {foam_scalar(time.initial_step)};
writeControl    adjustable;
writeInterval   {foam_scalar(time.write_interval)};
purgeWrite      0;
writeFormat     binary;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
adjustTimeStep  yes;
maxCo           {foam_scalar(time.max_courant)};
maxDeltaT       {foam_scalar(time.step_for_angle)};

functions
{{
    AMIWeights1
    {{
        type            AMIWeights;
        libs            (fieldFunctionObjects);
        writeFields     false;
        writeToFile     true;
        log             true;
    }}
}}
"""


_AMI_WEIGHTS = """
functions
{
    AMIWeights1
    {
        type            AMIWeights;
        libs            (fieldFunctionObjects);
        writeFields     false;
        writeToFile     true;
        log             true;
    }
}
"""


def imported_control_dict(setup: CaseSetup) -> str:
    """A fixed step of the chosen angle (C1, docs/c1_startup_notes.md 3.3)."""
    time = setup.time
    degrees = setup.physics.time.degrees_per_step
    return foam_header("controlDict") + f"""
application     {SOLVER};
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {foam_scalar(time.end_time)};
// {degrees:g} deg of runner turn a step, fixed. Not limited by a Courant number:
// in an imported machine the largest one sits in the wall cells (C1: 25-148 at
// 1-2 deg, stable), and a limit of 2 cut the step about 100 times.
deltaT          {foam_scalar(time.step_for_angle)};
adjustTimeStep  no;
writeControl    adjustable;
writeInterval   {foam_scalar(time.write_interval)};
purgeWrite      0;
writeFormat     binary;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
""" + _AMI_WEIGHTS


def fv_schemes(setup: CaseSetup) -> str:
    return foam_header("fvSchemes") + """
ddtSchemes
{
    default         Euler;
}
gradSchemes
{
    default         Gauss linear;
    grad(p)         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
}
divSchemes
{
    default         none;
    div(phi,U)      Gauss linearUpwind grad(U);
    turbulence      Gauss upwind;
    div(phi,k)      $turbulence;
    div(phi,omega)  $turbulence;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes
{
    default         Gauss linear limited corrected 0.33;
}
interpolationSchemes
{
    default         linear;
}
snGradSchemes
{
    default         limited corrected 0.33;
}
wallDist
{
    method          meshWave;
}
"""


def fv_solution(setup: CaseSetup) -> str:
    """Above NON_ORTHO_LIMIT_DEG of mesh non-orthogonality the PIMPLE loop gets
    pressure and non-orthogonal correctors, the reason written beside them;
    else the tutorial's settings, unchanged."""
    if setup.imported:
        return foam_header("fvSolution") + imported_fv_solution(setup)
    text = _FV_SOLUTION
    if setup.corrected:
        assert setup.non_orthogonality is not None
        text = text.replace(_PLAIN_CORRECTORS, (
            f"    // {setup.non_orthogonality.reason}\n"
            f"    nCorrectors         {PRESSURE_CORRECTORS};\n"
            f"    nNonOrthogonalCorrectors {NON_ORTHO_CORRECTORS};\n"))
    return foam_header("fvSolution") + text


_PLAIN_CORRECTORS = "    nCorrectors         1;\n    nNonOrthogonalCorrectors 0;\n"
_FV_SOLUTION = """
solvers
{
    "pcorr.*"
    {
        solver          GAMG;
        tolerance       1e-2;
        relTol          0;
        smoother        DICGaussSeidel;
        maxIter         50;
        cacheAgglomeration yes;
        updateInterval  -1;
    }
    p
    {
        $pcorr;
        tolerance       1e-5;
        relTol          0.01;
    }
    pFinal
    {
        $p;
        tolerance       1e-6;
        relTol          0;
    }
    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-6;
        relTol          0.1;
    }
    "(U|k|omega)Final"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-6;
        relTol          0;
    }
}
PIMPLE
{
    correctPhi          no;
    nOuterCorrectors    2;
    nCorrectors         1;
    nNonOrthogonalCorrectors 0;
}
relaxationFactors
{
    "(U|k|omega).*"   1;
}
cache
{
    grad(U);
}
"""


# The robust PIMPLE settings of an imported machine (owner, after C1; docs/
# c1_startup_notes.md 3.2-3.3): with nOuterCorrectors 2, nCorrectors 1 and no
# relaxation every start of the Francis runner + draft tube diverged (the
# Courant maximum x30 a step); with these every start ran.
OUTER_CORRECTORS = 5
ROBUST_PRESSURE_CORRECTORS = 2
ROBUST_NON_ORTHO_CORRECTORS = 1
OUTER_TOLERANCES = {"U": 1e-4, "p": 1e-3}  # residualControl, initial residuals
RELAXATION = {"U": 0.7, "p": 0.3, "turbulence": 0.7}  # outer iterations only


def _solvers(potential: bool) -> str:
    """The solvers block of the tutorial (as _FV_SOLUTION), with potentialFoam's
    Phi for a potential-flow start."""
    block = _FV_SOLUTION.split("PIMPLE\n", 1)[0]
    if not potential:
        return block
    return block.replace("solvers\n{\n", (
        "solvers\n{\n    Phi  // potentialFoam (the potential-flow start)\n    {\n"
        "        solver          GAMG;\n        smoother        DICGaussSeidel;\n"
        "        tolerance       1e-06;\n        relTol          0.01;\n    }\n"), 1)


def imported_fv_solution(setup: CaseSetup) -> str:
    """The robust settings, each with its reason."""
    if setup.corrected:
        assert setup.non_orthogonality is not None
        correctors = (f"    // {setup.non_orthogonality.reason}\n"
                      f"    nCorrectors         {PRESSURE_CORRECTORS};\n"
                      f"    nNonOrthogonalCorrectors {NON_ORTHO_CORRECTORS};\n")
    else:
        correctors = ("    // 2 pressure correctors and 1 non-orthogonal corrector a "
                      "solve (C1: ran\n    // stably on the runner + draft tube, 64 deg "
                      "max non-orthogonality)\n"
                      f"    nCorrectors         {ROBUST_PRESSURE_CORRECTORS};\n"
                      f"    nNonOrthogonalCorrectors {ROBUST_NON_ORTHO_CORRECTORS};\n")
    potential = setup.physics.start_up.method is StartUp.POTENTIAL_FLOW
    u, p = OUTER_TOLERANCES["U"], OUTER_TOLERANCES["p"]
    text = _solvers(potential) + f"""PIMPLE
{{
    // Robust settings for an imported machine (C1): the app's earlier
    // settings (2 outer iterations, 1 pressure corrector, no relaxation)
    // diverged from every start; these ran from every start.
    correctPhi          no;
    // Up to {OUTER_CORRECTORS} outer iterations a step, ended early by residualControl.
    nOuterCorrectors    {OUTER_CORRECTORS};
{correctors}    // Turbulence solved on every outer iteration, not the last only.
    turbOnFinalIterOnly false;
    // A step's outer loop ends when every initial residual is below its
    // tolerance; a step that reaches {OUTER_CORRECTORS} iterations without it is reported
    // (owner, C1: a non-converged transient is visible).
    residualControl
    {{
        U {{ tolerance {foam_scalar(u)}; relTol 0; }}
        p {{ tolerance {foam_scalar(p)}; relTol 0; }}
    }}
}}
// Under-relaxation on the outer iterations; the final iteration of each step
// is not relaxed (Final factors 1: owner, C1), so a converged step is the
// transient's own solution.
relaxationFactors
{{
    equations
    {{
        U               {foam_scalar(RELAXATION["U"])};
        UFinal          1;
        "(k|omega)"     {foam_scalar(RELAXATION["turbulence"])};
        "(k|omega)Final" 1;
    }}
    fields
    {{
        p               {foam_scalar(RELAXATION["p"])};
        pFinal          1;
    }}
}}
cache
{{
    grad(U);
}}
"""
    if potential:
        text += ("// potentialFoam (the potential-flow start; C1 evidence used 3)\n"
                 "potentialFlow\n{\n    nNonOrthogonalCorrectors 3;\n}\n")
    return text


START_UP_LABELS = {
    StartUp.POTENTIAL_FLOW: "Potential flow (potentialFoam first; seconds)",
    StartUp.STEADY_MRF: "Steady MRF first (the developed flow from the first step)",
    StartUp.FROM_REST: "From rest",
}


def solution_settings(setup: CaseSetup) -> list[dict[str, str]]:
    """An imported machine's settings as written, each with its reason (the
    case setup report and screen; owner, C1: every setting labelled)."""
    degrees = setup.physics.time.degrees_per_step
    corrected = setup.non_orthogonality.reason if setup.corrected and \
        setup.non_orthogonality else None
    evidence = "C1 (docs/c1_startup_notes.md 3.3)"
    return [
        {"setting": f"Time step: {degrees:g} deg of runner turn, fixed",
         "reason": "Not limited by a Courant number: the largest sits in the wall cells "
                   f"(25-148 at 1-2 deg, stable); a limit of 2 cut the step about 100 times. "
                   f"{evidence}"},
        {"setting": f"Outer iterations: up to {OUTER_CORRECTORS} a step, with residualControl "
                    f"(U {OUTER_TOLERANCES['U']:g}, p {OUTER_TOLERANCES['p']:g})",
         "reason": "With 2 and no relaxation every start diverged. A step that reaches "
                   f"{OUTER_CORRECTORS} without converging is counted from the solver log "
                   "(owner, C1)."},
        {"setting": (f"Pressure correctors {PRESSURE_CORRECTORS}, non-orthogonal correctors "
                     f"{NON_ORTHO_CORRECTORS}") if corrected else
                    (f"Pressure correctors {ROBUST_PRESSURE_CORRECTORS}, non-orthogonal "
                     f"correctors {ROBUST_NON_ORTHO_CORRECTORS}"),
         "reason": corrected or f"Ran stably at 64 deg max non-orthogonality. {evidence}"},
        {"setting": f"Under-relaxation U {RELAXATION['U']:g}, p {RELAXATION['p']:g}, k and "
                    f"omega {RELAXATION['turbulence']:g} on the outer iterations; final "
                    "iteration 1",
         "reason": "Relaxation steadies the outer iterations; the final iteration is not "
                   "relaxed, so a converged step is the transient's own solution (owner, C1)."},
        {"setting": "Turbulence on every outer iteration",
         "reason": f"As the runs that were stable. {evidence}"},
        {"setting": f"Start-up: {START_UP_LABELS[setup.physics.start_up.method]}",
         "reason": "Every start ran with these settings; the steady MRF start gave the "
                   f"calmest first steps. {evidence}"},
        {"setting": f"{setup.physics.ranks} MPI ranks (system/decomposeParDict)",
         "reason": "16 was the fastest on the owner's 24-core PC, 8 nearly as fast (memory "
                   "bandwidth bound). C1 (docs/c1_startup_notes.md 3.1)"},
    ]


def decompose_par_dict(setup: CaseSetup) -> str:
    return foam_header("decomposeParDict") + f"""
// {setup.physics.ranks} ranks (C1: 16 was the fastest on the owner's 24-core PC,
// 8 nearly as fast: the solve is bound by memory bandwidth)
numberOfSubdomains {setup.physics.ranks};
method          scotch;
"""


def _mrf_files(setup: CaseSetup, files: dict[str, str]) -> dict[str, str]:
    """The steady MRF case run before the transient (STEADY_MRF start): the
    same fields and physics, the rotating zone as MRF, simpleFoam; as the C1
    evidence (100 iterations: continuity 0.035 -> 0.0012)."""
    rotation, iterations = setup.rotation, setup.physics.start_up.mrf_iterations
    correctors = NON_ORTHO_CORRECTORS if setup.corrected else ROBUST_NON_ORTHO_CORRECTORS
    found = {f"mrf/{k}": v for k, v in files.items() if k.startswith("0/")}
    for name in ("constant/transportProperties", "constant/turbulenceProperties",
                 "system/fvSchemes", "system/decomposeParDict"):
        found[f"mrf/{name}"] = files[name]
    found["mrf/system/fvSchemes"] = (
        files["system/fvSchemes"].replace("default         Euler;",
                                          "default         steadyState;")
        .replace("Gauss linearUpwind", "bounded Gauss linearUpwind")
        .replace("turbulence      Gauss upwind", "turbulence      bounded Gauss upwind"))
    found["mrf/system/controlDict"] = foam_header("controlDict") + f"""
// The steady MRF start (C1): {iterations} iterations, its last written for the transient.
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {iterations};
deltaT          1;
writeControl    timeStep;
writeInterval   {iterations};
purgeWrite      0;
writeFormat     binary;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
"""
    found["mrf/system/fvSolution"] = foam_header("fvSolution") + _solvers(False) + f"""SIMPLE
{{
    nNonOrthogonalCorrectors {correctors};
    consistent      yes;
    residualControl
    {{
        p               1e-4;
        U               1e-5;
        "(k|omega)"     1e-5;
    }}
}}
relaxationFactors
{{
    equations
    {{
        U               0.7;
        "(k|omega)"     0.7;
    }}
    fields
    {{
        p               0.3;
    }}
}}
"""
    found["mrf/constant/MRFProperties"] = foam_header("MRFProperties") + f"""
MRF1
{{
    cellZone        {rotation.zone};
    active          yes;
    nonRotatingPatches ();
    origin          {foam_vector(*rotation.origin)};
    axis            {foam_vector(*rotation.axis)};
    omega           {foam_scalar(rotation.omega)};
}}
"""
    return found


_START_UPS = {
    StartUp.POTENTIAL_FLOW: (
        "# Start-up: potential flow (potentialFoam first, seconds; C1).\n"
        "runApplication decomposePar -force || exit 1\n"
        "runParallel potentialFoam -writephi || exit 1\n"),
    StartUp.STEADY_MRF: (
        "# Start-up: a steady MRF solve first (mrf/), its last fields in 0/: the\n"
        "# developed flow from the first step (C1). Set up the case again to have\n"
        "# the app's own 0/ back.\n"
        "(\n"
        "    cd mrf || exit 1\n"
        "    [ -e constant/polyMesh ] || ln -s ../../constant/polyMesh constant/polyMesh\n"
        "    runApplication decomposePar -force || exit 1\n"
        "    runParallel simpleFoam || exit 1\n"
        "    runApplication reconstructPar -latestTime || exit 1\n"
        ") || exit 1\n"
        "latest=$(foamListTimes -case mrf -latestTime)\n"
        "if [ -z \"$latest\" ] || [ ! -f \"mrf/$latest/U\" ]\n"
        "then\n"
        "    echo \"The steady MRF solve wrote no fields (mrf/log.simpleFoam).\" >&2\n"
        "    exit 1\n"
        "fi\n"
        "for field in U p k omega nut\n"
        "do\n"
        "    if [ -f \"mrf/$latest/$field\" ]\n"
        "    then\n"
        "        cp \"mrf/$latest/$field\" \"0/$field\" || exit 1\n"
        "    fi\n"
        "done\n"
        "runApplication decomposePar -force || exit 1\n"),
    StartUp.FROM_REST: (
        "# Start-up: from rest (C1).\n"
        "runApplication decomposePar -force || exit 1\n"),
}


def allrun(setup: CaseSetup) -> str:
    """The run order of an imported machine's case (C1): its start-up, then the
    transient on the case's ranks. The app does not run it. It stops at the
    first failed step, and does not start over an earlier run's output (a new
    setup keeps logs and results: they are the solver's)."""
    return ("#!/bin/sh\n"
            "# The run order of this case, written by the app (it does not run the\n"
            "# solver). With OpenFOAM v2512 sourced, from this folder: ./Allrun\n"
            "# It stops at the first step that fails.\n"
            "cd \"${0%/*}\" || exit 1\n"
            ". \"${WM_PROJECT_DIR:?}\"/bin/tools/RunFunctions\n"
            "# An earlier run's logs and results are kept by a new setup: run on a\n"
            "# clean case, so no step is skipped and no old result is reused.\n"
            "for old in log.* mrf/log.* processor* mrf/processor*\n"
            "do\n"
            "    if [ -e \"$old\" ]\n"
            "    then\n"
            "        echo \"An earlier run's output is here ($old): remove the log.* files\" \\\n"
            "             \"and the processor* folders (here and in mrf/) to run again.\" >&2\n"
            "        exit 1\n"
            "    fi\n"
            "done\n"
            "# Open MPI refuses to run as root unless allowed (WSL's default user is root).\n"
            "if [ \"$(id -u)\" = 0 ]\n"
            "then\n"
            "    export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1\n"
            "fi\n\n"
            + _START_UPS[setup.physics.start_up.method]
            + f"\n# The transient: {setup.physics.ranks} ranks (system/decomposeParDict).\n"
            f"runParallel {SOLVER} || exit 1\n")


def dynamic_mesh_dict(setup: CaseSetup) -> str:
    """Solid-body rotation of the zone's cells; a positive omega turns them
    right-handed about `axis` (verified in G0, check_rotation.sh)."""
    rotation = setup.rotation
    return foam_header("dynamicMeshDict") + f"""
dynamicFvMesh   dynamicMotionSolverFvMesh;
motionSolverLibs (fvMotionSolvers);
motionSolver    solidBody;
cellZone        {rotation.zone};
solidBodyMotionFunction  rotatingMotion;
origin          {foam_vector(*rotation.origin)};
axis            {foam_vector(*rotation.axis)};
omega           {foam_scalar(rotation.omega)};
"""


def transport_properties(setup: CaseSetup) -> str:
    return foam_header("transportProperties") + f"""
transportModel  Newtonian;
nu              {foam_scalar(setup.physics.fluid.kinematic_viscosity)};
"""


def turbulence_properties(setup: CaseSetup) -> str:
    if not setup.turbulent:
        return foam_header("turbulenceProperties") + "\nsimulationType      laminar;\n"
    return foam_header("turbulenceProperties") + """
simulationType      RAS;
RAS
{
    RASModel        kOmegaSST;
    turbulence      on;
    printCoeffs     on;
}
"""


def dictionaries(setup: CaseSetup) -> dict[str, str]:
    """Relative path -> text of every system/ and constant/ dictionary; for an
    imported machine (C1) also decomposeParDict and Allrun (and mrf/ for the
    steady MRF start: imported_files, which needs the 0/ fields)."""
    found = {
        "system/controlDict": control_dict(setup),
        "system/fvSchemes": fv_schemes(setup),
        "system/fvSolution": fv_solution(setup),
        "constant/dynamicMeshDict": dynamic_mesh_dict(setup),
        "constant/transportProperties": transport_properties(setup),
        "constant/turbulenceProperties": turbulence_properties(setup),
    }
    if setup.imported:
        found["system/decomposeParDict"] = decompose_par_dict(setup)
        found[ALLRUN] = allrun(setup)
    return found


def imported_files(setup: CaseSetup, files: dict[str, str]) -> dict[str, str]:
    """The steady MRF case of a STEADY_MRF start, from the case's own files."""
    if not setup.imported or setup.physics.start_up.method is not StartUp.STEADY_MRF:
        return {}
    return _mrf_files(setup, files)
