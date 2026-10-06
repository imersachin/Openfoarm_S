# OpenFOAM Fundamentals for Developers

## 1. Scope

This document gives the project-level OpenFOAM concepts needed by developers.
It is not a replacement for version-specific OpenFOAM documentation.

When exact behavior depends on the installed OpenFOAM version, verify it
against the configured profile, local installation, fixtures, or authoritative
version-specific documentation.

---

## 2. Case Structure

A typical case contains:

```text
case/
├── 0/
├── constant/
└── system/
```

For this preprocessing MVP:

```text
case/
├── 0/
├── constant/
│   ├── geometry/
│   ├── triSurface/
│   └── polyMesh/
└── system/
    ├── blockMeshDict
    ├── snappyHexMeshDict
    ├── surfaceFeatureExtractDict
    ├── meshQualityDict
    └── controlDict
```

The application must generate meshing dictionaries under `system/`.

---

## 3. Dictionaries

OpenFOAM dictionaries commonly contain:

```text
keyword value;

section
{
    key value;
}
```

Example:

```text
vector (1 0 0);
```

The application should generate dictionaries programmatically.

Do not make users manually edit generated dictionaries for normal MVP use.

---

## 4. STL and Units

STL is a triangular surface representation.

STL generally does not reliably encode physical units.

Therefore:

```text
source units
→ explicit conversion
→ target/project units
```

must be represented in application configuration.

Never silently assume millimeters, meters, or another unit.

---

## 5. Geometry Transformation

The project transformation pipeline is:

```text
STL
→ unit conversion
→ scale
→ rotation
→ translation
→ orientation
→ transformed STL
→ validation
```

The transformed artifact is consumed by downstream meshing.

Transformation order is an engineering contract and must be tested.

---

## 6. Geometry Quality

Depending on the operation and available geometry library, consider:

- finite coordinates
- empty geometry
- degenerate faces
- duplicate/problematic faces
- dimensions
- connected components
- watertightness
- winding/orientation
- topology consistency

Do not silently repair geometry unless explicitly specified.

---

## 7. Mesh Concepts

A mesh contains conceptually:

- points
- faces
- cells
- boundary patches

More cells generally increase:

- RAM
- CPU time
- disk usage
- meshing difficulty

Therefore mesh resolution is both an accuracy-related engineering input and
a resource input.

---

## 8. blockMesh

`blockMesh` creates the background mesh from:

```text
system/blockMeshDict
```

Logical workflow:

```text
domain configuration
→ blockMeshDict
→ blockMesh
→ background mesh
```

The generated dictionary must be placed at the correct case path.

---

## 9. surfaceFeatureExtract

Feature extraction can produce `.eMesh` data used by snappy workflows.

Logical dependency:

```text
surface geometry
→ surfaceFeatureExtract
→ .eMesh
→ snappyHexMesh
```

Run feature extraction only when the current configuration requires it.

---

## 10. snappyHexMesh

Conceptually:

```text
background mesh
→ castellation
→ snap
→ optional layers
→ final mesh
```

Exact options and behavior depend on the OpenFOAM version/profile.

Do not assume an option is valid across all versions.

---

## 11. Refinement

Separate:

- base/background resolution
- surface refinement
- feature refinement
- local/region refinement
- boundary-layer refinement

More refinement generally means more cells and higher resource consumption.

Avoid global over-refinement.

---

## 12. Boundary Layers

Boundary layers represent near-wall mesh resolution.

Relevant controls can include:

- number of layers
- layer thickness
- growth/expansion behavior
- related geometric constraints

Boundary layers can substantially increase cell count and meshing difficulty.

---

## 13. Mesh Quality

Common quality concepts include:

- non-orthogonality
- skewness
- cell volume
- aspect ratio
- connectivity

Thresholds are not universal. They can depend on:

- solver
- physics
- OpenFOAM version
- numerical schemes
- engineering requirements

Do not hard-code universal engineering claims.

---

## 14. checkMesh

`checkMesh` reports structural and quality-related mesh information.

The application should parse relevant output into structured results.

Possible information includes:

- points
- faces
- cells
- patches
- quality metrics
- failed checks
- volume failures
- overall status

The parser must tolerate expected version-specific formatting differences where
possible.

---

## 15. Mesh Quality Is Not CFD Accuracy

The conceptual chain is:

```text
Geometry validity
→ Mesh validity
→ Mesh quality
→ Numerical stability
→ Convergence
→ Verification/Validation
→ CFD accuracy
```

A good mesh does not guarantee accurate CFD.

Accuracy also depends on:

- physics
- boundary conditions
- turbulence model
- numerical schemes
- time step
- domain
- convergence
- mesh independence
- reference/experimental validation

The preprocessing application must not claim otherwise.

---

## 16. Patches

Common patch concepts include:

- inlet
- outlet
- wall
- symmetry
- farField

Patch names should be deterministic and predictable within the application's
configuration model.

---

## 17. Fields and CFD Concepts

Examples include:

- `U` — velocity
- `p` — pressure
- `T` — temperature
- `k`
- `omega`
- `nut`

These belong primarily to CFD setup and solver workflows, not to the core
mesh-preprocessing logic.

---

## 18. controlDict / fvSchemes / fvSolution

High-level roles:

- `controlDict` — runtime/application/time/write controls
- `fvSchemes` — numerical discretization schemes
- `fvSolution` — linear solver settings, tolerances, relaxation, and algorithm
  configuration

The MVP should not expand into full CFD setup unless explicitly approved.

---

## 19. Resource Problems

A common resource chain is:

```text
more refinement
→ more cells
→ more RAM
→ possible swapping
→ longer runtime or failure
```

Also consider:

- disk usage
- CPU time
- large logs
- intermediate artifacts
- boundary-layer complexity

The application should warn before expensive operations when risk is high.

---

## 20. Resource-Aware UX

Example:

```text
Estimated cells: ~15M
Available RAM: 16 GB
Risk: HIGH RESOURCE RISK
Suggestion: reduce refinement or base resolution
```

This is a heuristic warning, not an exact runtime forecast.

---

## 21. Common Failure Classes

### Geometry

- invalid STL
- wrong scale
- bad topology
- degenerate geometry

### Dictionary

- syntax errors
- missing entries
- wrong paths
- invalid references

### Meshing

- snapping problems
- bad cells
- excessive refinement
- boundary-layer failure

### Environment

- executable missing
- incompatible OpenFOAM profile
- missing environment configuration

### Resources

- insufficient RAM
- insufficient disk
- excessive CPU/runtime demand
- timeout

All failures should become structured application issues where possible.

---

## 22. User Knowledge Boundary

Users should understand:

- geometry
- dimensions
- units
- orientation
- domain size
- mesh resolution
- refinement
- boundary layers
- quality findings
- resource implications

Users should not need to understand:

- raw dictionary syntax
- command sequencing
- cache invalidation
- dependency graph internals
- subprocess implementation

Advanced users may inspect raw dictionaries and logs.

---

## 23. Learning Order for Developers

Recommended sequence:

```text
Case structure
→ Dictionary syntax
→ STL
→ blockMesh
→ surfaceFeatureExtract
→ snappyHexMesh
→ checkMesh
→ patches
→ fields
→ solvers
→ numerical schemes
→ convergence
→ mesh independence
→ CFD validation
```
