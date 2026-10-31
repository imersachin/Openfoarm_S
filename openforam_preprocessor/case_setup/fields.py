"""The 0/ fields (docs/rotating_machinery.md sections 7 and 9.7; G6).

Conditions per patch kind as they ran on OpenFOAM v2512 in G0 (notes section
4), each with its source:

    inlet, outlet, noSlip, movingWallVelocity, kqRWallFunction,
    nutkWallFunction:  tutorials/incompressible/pimpleFoam/RAS/propeller/0.orig
    omegaWallFunction, k/omega inlet and outlet forms:
                       etc/templates/inflowOutflowRotating/0
    slip:              tutorials/incompressible/lumpedPointMotion/building/steady/
                       0.orig/include/environ
    flowRateInletVelocity:
                       tutorials/incompressible/porousSimpleFoam/angledDuct/common/
                       0.orig/U (proven in G6, P4)
    cyclicAMI:         #includeEtc "caseDicts/setConstraintTypes" (propeller)

Every patch is listed by name: a field without an entry for a patch is a
fatal error at solver start (G0 R4 b').
"""

from __future__ import annotations

from collections.abc import Callable

from case_setup.setup import CaseSetup, PatchKind, Swirl
from openfoam.dictionary import foam_scalar, foam_vector

ZERO_VECTOR = "uniform (0 0 0)"


def _header(object_name: str, field_class: str) -> str:
    return f"""\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {field_class};
    object      {object_name};
}}
"""


def _entry(name: str, lines: str) -> str:
    body = "\n".join(f"        {line}" for line in lines.splitlines())
    return f"    {name}\n    {{\n{body}\n    }}"


def _field(setup: CaseSetup, name: str, field_class: str, dimensions: str, internal: str,
           condition: Callable[[str, PatchKind], str]) -> str:
    entries = [_entry(patch, condition(patch, kind)) for patch, kind in setup.patches
               if kind is not PatchKind.CONSTRAINT]
    return _header(name, field_class) + f"""
dimensions      {dimensions};
internalField   {internal};
boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"
{chr(10).join(entries)}
}}
"""


def _fixed(value: str) -> str:
    return f"type            fixedValue;\nvalue           uniform {value};"


def _swirl(swirl: Swirl) -> str:
    """cylindricalInletVelocity (C1 evidence, v2512: rpm -100 gave -20.54 m/s
    towards -theta on a test ring, exactly omega r; radial inflow negative)."""
    return (f"// Swirl {swirl.label}: flow angle {swirl.flow_angle_deg:g} deg from "
            f"tangential;\n// Vr {swirl.radial_velocity:.4g} m/s, Vt "
            f"{swirl.tangential_velocity:.4g} m/s at r {swirl.radius:.4g} m (measured)\n"
            "type            cylindricalInletVelocity;\n"
            f"origin          {foam_vector(*swirl.origin)};\n"
            f"axis            {foam_vector(*swirl.axis)};\n"
            "axialVelocity   constant 0;\n"
            f"radialVelocity  constant {foam_scalar(swirl.radial_velocity)};\n"
            f"rpm             constant {foam_scalar(swirl.rpm)};\n"
            f"value           {ZERO_VECTOR};")


def velocity(setup: CaseSetup) -> str:
    def condition(patch: str, kind: PatchKind) -> str:
        if kind is PatchKind.INLET:
            inlet = setup.inlets[patch]
            if inlet.velocity is not None:
                return _fixed(foam_vector(*inlet.velocity))
            if inlet.swirl is not None:
                return _swirl(inlet.swirl)
            assert inlet.flow_rate is not None
            return ("type            flowRateInletVelocity;\n"
                    f"volumetricFlowRate  constant {foam_scalar(inlet.flow_rate)};")
        if kind is PatchKind.OUTLET:
            return (f"type            inletOutlet;\ninletValue      {ZERO_VECTOR};\n"
                    f"value           {ZERO_VECTOR};")
        if kind is PatchKind.WALL:
            return "type            noSlip;"
        if kind is PatchKind.MOVING_WALL:
            return f"type            movingWallVelocity;\nvalue           {ZERO_VECTOR};"
        return "type            slip;"
    return _field(setup, "U", "volVectorField", "[0 1 -1 0 0 0 0]", ZERO_VECTOR, condition)


def pressure(setup: CaseSetup) -> str:
    """Kinematic pressure (m^2/s^2): the outlet's Pa divided by the density."""
    def condition(patch: str, kind: PatchKind) -> str:
        if kind is PatchKind.OUTLET:
            return _fixed(foam_scalar(setup.outlets[patch]))
        if kind is PatchKind.SLIP:
            return "type            slip;"
        return "type            zeroGradient;"
    return _field(setup, "p", "volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0", condition)


def _turbulence(setup: CaseSetup, name: str, dimensions: str, internal: float,
                inlet_value: Callable[[str], float], wall: str) -> str:
    def condition(patch: str, kind: PatchKind) -> str:
        if kind is PatchKind.INLET:
            return _fixed(foam_scalar(inlet_value(patch)))
        if kind is PatchKind.OUTLET:
            return ("type            inletOutlet;\ninletValue      $internalField;\n"
                    "value           $internalField;")
        if kind in (PatchKind.WALL, PatchKind.MOVING_WALL):
            return f"type            {wall};\nvalue           $internalField;"
        return "type            slip;"
    return _field(setup, name, "volScalarField", dimensions,
                  f"uniform {foam_scalar(internal)}", condition)


def turbulent_kinetic_energy(setup: CaseSetup) -> str:
    return _turbulence(setup, "k", "[0 2 -2 0 0 0 0]", setup.internal_k,
                       lambda p: setup.inlets[p].k, "kqRWallFunction")


def specific_dissipation(setup: CaseSetup) -> str:
    return _turbulence(setup, "omega", "[0 0 -1 0 0 0 0]", setup.internal_omega,
                       lambda p: setup.inlets[p].omega, "omegaWallFunction")


def turbulent_viscosity(setup: CaseSetup) -> str:
    def condition(patch: str, kind: PatchKind) -> str:
        if kind in (PatchKind.WALL, PatchKind.MOVING_WALL):
            return "type            nutkWallFunction;\nvalue           uniform 0;"
        if kind is PatchKind.SLIP:
            return "type            slip;"
        return "type            calculated;\nvalue           uniform 0;"
    return _field(setup, "nut", "volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0", condition)


def fields(setup: CaseSetup) -> dict[str, str]:
    """Relative path -> text of every 0/ file for the chosen model."""
    files = {"0/U": velocity(setup), "0/p": pressure(setup)}
    if setup.turbulent:
        files.update({"0/k": turbulent_kinetic_energy(setup),
                      "0/omega": specific_dissipation(setup),
                      "0/nut": turbulent_viscosity(setup)})
    return files
