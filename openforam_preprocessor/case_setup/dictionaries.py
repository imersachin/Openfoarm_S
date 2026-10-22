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

from case_setup.setup import NON_ORTHO_CORRECTORS, PRESSURE_CORRECTORS, CaseSetup
from openfoam.dictionary import foam_header, foam_scalar, foam_vector

SOLVER = "pimpleFoam"


def control_dict(setup: CaseSetup) -> str:
    """The time step adjusts to maxCo and never exceeds the configured rotation
    angle (maxDeltaT). The first step also respects the Courant estimate."""
    time = setup.time
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
    """Relative path -> text of every system/ and constant/ dictionary."""
    return {
        "system/controlDict": control_dict(setup),
        "system/fvSchemes": fv_schemes(setup),
        "system/fvSolution": fv_solution(setup),
        "constant/dynamicMeshDict": dynamic_mesh_dict(setup),
        "constant/transportProperties": transport_properties(setup),
        "constant/turbulenceProperties": turbulence_properties(setup),
    }
