"""Operations of a machine meshing run, in order (spec section 11; G4).

    validate -> generate cases -> mesh:<case> (each domain case, each zone case)
    -> assemble -> check mesh -> validate mesh

Each mesh operation is cached on its own case's files, so a change re-runs
only the meshes whose dictionaries or surfaces changed, then the assembly and
the checks. The assembly is cached on the meshes it merges and the patch
types it sets (H3): a change of patch type alone re-runs only the assembly.
"""

from __future__ import annotations

from machines.assembly import MachineCases

VALIDATE = "machine_validate"
GENERATE_CASES = "machine_generate_cases"
MESH_PREFIX = "machine_mesh:"
ASSEMBLE = "machine_assemble"
CHECK_MESH = "machine_check_mesh"
VALIDATE_MESH = "machine_validate_mesh"

MESH_EXECUTABLES = ("blockMesh", "surfaceFeatureExtract", "snappyHexMesh", "topoSet")
ASSEMBLE_EXECUTABLES = ("mergeMeshes", "createPatch", "foamDictionary")
CHECK_EXECUTABLES = ("checkMesh", "postProcess")
MACHINE_EXECUTABLES = tuple(sorted({*MESH_EXECUTABLES, *ASSEMBLE_EXECUTABLES,
                                    *CHECK_EXECUTABLES}))


def mesh_operation(case: str) -> str:
    return f"{MESH_PREFIX}{case}"


def case_of(operation: str) -> str:
    return operation.removeprefix(MESH_PREFIX)


def operations_for(cases: MachineCases) -> tuple[str, ...]:
    meshes = tuple(mesh_operation(c.name) for c in (*cases.domain, *cases.zones))
    return (VALIDATE, GENERATE_CASES, *meshes, ASSEMBLE, CHECK_MESH, VALIDATE_MESH)


def executables(operation: str) -> tuple[str, ...]:
    if operation.startswith(MESH_PREFIX):
        return MESH_EXECUTABLES
    return {ASSEMBLE: ASSEMBLE_EXECUTABLES, CHECK_MESH: CHECK_EXECUTABLES}.get(operation, ())
