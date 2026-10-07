# OpenFOAM Preprocessor Architecture

## 1. Purpose

The OpenFOAM Preprocessor is a lightweight Python application for preparing,
meshing, validating, visualizing, and exporting OpenFOAM cases.

The application hides OpenFOAM dictionary syntax and command sequencing from
normal users while exposing the engineering decisions they actually need to
make.

The MVP is deterministic. Identical inputs, configuration, and OpenFOAM
profile should produce equivalent planned operations and deterministic
generated configuration.

---

## 2. Architectural Principles

### 2.1 Separation of concerns

The system is divided into:

```text
UI
↓
Application/Core Services
↓
Configuration + Validation
↓
Dependency Planner
↓
Deterministic Operations
↓
OpenFOAM Environment/Runner
↓
Artifacts + Structured Results
```

The UI must not own engineering logic.

### 2.2 Declarative configuration

Users define engineering intent through configuration models.

The application derives:

- validation
- dependency state
- execution plan
- generated dictionaries
- artifact state

from that configuration.

### 2.3 Dependency-driven execution

The application must not blindly rerun the complete workflow after every
change.

A change invalidates only the operations and artifacts that depend on it.

### 2.4 Deterministic engineering

The application must not silently guess:

- STL units
- OpenFOAM executable locations
- engineering thresholds
- transformation semantics
- required dependencies

When information is required and unavailable, report it explicitly.

---

## 3. Target Repository Structure

```text
openforam_preprocessor/
├── CLAUDE.md
├── pyproject.toml
├── docs/
│   ├── architecture.md
│   ├── development_workflow.md
│   ├── openfoam_basics.md
│   ├── testing_strategy.md
│   └── agents/
│       ├── architecture_review_agent.md
│       ├── openfoam_review_agent.md
│       └── testing_agent.md
├── app/
│   ├── dashboard.py
│   ├── pages/
│   └── components/
├── core/
│   ├── config/
│   ├── workflow/
│   ├── artifacts/
│   └── issues/
├── geometry/
│   ├── importer.py
│   ├── transformer.py
│   ├── validator.py
│   └── metrics.py
├── mesh/
│   ├── generator.py
│   ├── parser.py
│   ├── validator.py
│   ├── estimator.py
│   └── models.py
├── openfoam/
│   ├── environment.py
│   ├── profile.py
│   ├── dictionary.py
│   ├── runner.py
│   └── commands.py
├── visualization/
├── storage/
└── tests/
    ├── unit/
    ├── integration/
    ├── regression/
    └── fixtures/
```

The repository may temporarily differ from this target during implementation.
Do not create empty modules solely to make the tree match the target.

---

## 4. End-to-End Workflow

```text
Project
→ STL Import
→ Explicit Units
→ Geometry Validation
→ Geometry Transformation
→ Re-validation
→ Domain Configuration
→ Mesh Resolution
→ Refinement
→ Boundary Layers
→ Resource Preflight
→ Dictionary Generation
→ blockMesh
→ surfaceFeatureExtract (when required)
→ snappyHexMesh
→ checkMesh
→ Structured Validation
→ Visualization
→ Export
```

The dependency planner determines which stages are actually required.

---

## 5. Geometry Architecture

Geometry responsibilities are separate:

```text
Importer
Transformer
Validator
Metrics
Artifact
```

### 5.1 Import

The importer:

- reads the supported geometry format
- verifies that the file can be parsed
- produces an internal geometry representation or normalized artifact
- reports import errors structurally

For MVP, STL is the supported surface format.

### 5.2 Units

STL does not reliably encode units.

The application therefore requires explicit source/target units.

Unit conversion must occur before downstream engineering decisions that depend
on physical dimensions.

### 5.3 Transformation

The transformation pipeline is:

```text
Original STL
→ Unit Conversion
→ Scale
→ Rotation
→ Translation
→ Orientation
→ Transformed Artifact
```

The exact order is part of the contract and must be covered by tests.

The transformed artifact is the geometry consumed by downstream meshing.

Transformation conventions (approved in M2):

- **Units:** `geometry.source_units` is required (`m`, `cm`, `mm`, `um`, `in`,
  `ft`). The artifact is always in metres.
- **Pivot:** scale and rotation act about the origin `(0, 0, 0)`.
- **Rotation:** `rotation_deg` is applied about the fixed global axes, X then Y
  then Z (`R = Rz · Ry · Rx`), matching OpenFOAM's roll-pitch-yaw convention.
- **Translation:** applied last, in metres (after unit conversion).
- **Orientation:** normals are checked and reported (`INWARD_NORMALS`); the
  geometry is never flipped or repaired.
- **Artifact:** `constant/triSurface/<patch>.stl`, written as deterministic
  ASCII STL with round-trip float precision, only when its content changes,
  and re-validated from disk after writing.

### 5.4 Validation

Validation should cover, where applicable:

- finite coordinates
- empty geometry
- degenerate faces
- duplicate/problematic faces
- dimensions
- connected components
- watertightness
- winding/orientation
- topology/geometry consistency

The validator returns structured findings rather than UI-specific messages.

---

## 6. Domain and Background Mesh

The domain defines the computational background volume.

`blockMeshDict` must be generated at:

```text
case/system/blockMeshDict
```

The domain configuration must be separated from the code that writes the
OpenFOAM dictionary.

The `blockMesh` operation consumes the generated dictionary and produces the
background mesh.

---

## 7. Refinement Architecture

Refinement is modeled independently:

1. Background/base resolution
2. Surface refinement
3. Feature refinement
4. Local/region refinement
5. Boundary-layer refinement

These settings affect dependency state and resource estimates.

Changing a refinement setting must invalidate the relevant downstream
dictionary/mesh/checkMesh artifacts but should not invalidate unrelated
geometry when geometry itself has not changed.

---

## 8. surfaceFeatureExtract

If `snappyHexMesh` uses an `.eMesh`, the dependency is:

```text
STL / transformed geometry
→ surfaceFeatureExtract
→ .eMesh
→ snappyHexMesh
```

The planner must represent this dependency.

If no feature extraction is required, it should not execute merely because the
operation exists in the codebase.

---

## 9. snappyHexMesh

Conceptually:

```text
Background Mesh
→ Castellation
→ Snap
→ Optional Boundary Layers
→ Final Mesh
```

The generated dictionary must reference the actual geometry/artifacts produced
by the current project state.

Do not reference stale or unrelated geometry artifacts.

---

## 10. Resource Preflight

Before expensive meshing, estimate resource risk using:

- background cell count
- refinement configuration
- geometry complexity
- feature refinement
- local/region refinement
- boundary layers
- available RAM
- CPU
- available disk

Return:

```text
SAFE
WARNING
HIGH RESOURCE RISK
BLOCKED
```

The estimator is heuristic.

It must not claim exact runtime or exact memory consumption unless an
authoritative calculation is actually available.

---

## 11. Dependency Model

The logical model is:

```text
Configuration Change
        ↓
ChangeSet
        ↓
DependencyGraph
        ↓
ExecutionPlan
        ↓
Operations
        ↓
Artifacts
```

Examples:

### Geometry translation changes

```text
Translation
→ transformed geometry
→ feature extraction
→ mesh
→ checkMesh
```

### Surface refinement changes

```text
Surface refinement
→ snappy dictionary
→ mesh
→ checkMesh
```

### Quality threshold changes

```text
Quality threshold
→ validation
```

The mesh itself remains reusable if its inputs are unchanged.

---

## 12. Artifact and Cache Model

Potential artifacts:

- normalized/transformed geometry
- `.eMesh`
- `blockMeshDict`
- `surfaceFeatureExtractDict`
- `snappyHexMeshDict`
- `meshQualityDict`
- background mesh
- final mesh
- checkMesh report
- structured quality report

Artifact metadata should include, where applicable:

- content hash
- input/dependency hashes
- configuration identity
- OpenFOAM profile/version
- tool/version metadata
- creation time
- status

An existing file is not a valid cache entry merely because its path exists.

Cache reuse requires dependency identity verification.

---

## 13. OpenFOAM Environment/Profile

The environment/profile layer abstracts differences in:

- executable paths
- environment variables
- OpenFOAM versions
- supported capabilities
- command behavior
- parser behavior

The rest of the application should depend on the profile/environment interface,
not on hard-coded machine-specific executable paths.

---

## 14. Runner Contract

The runner should expose structured execution information:

```text
command
working_directory
environment/profile
timeout
stdout
stderr
exit_code
duration
run_id
status
```

Status:

```text
QUEUED
RUNNING
SUCCESS
FAILED
TIMEOUT
CANCELLED
```

Raw process output remains available for diagnosis.

---

## 15. Issue Model

Issues are structured and independent of the UI.

Recommended fields:

```text
category
severity
stage
code
message
explanation
suggested_action
artifact_reference
log_reference
details
```

Categories include:

```text
INPUT
UNITS
GEOMETRY
TRANSFORMATION
CONFIGURATION
DEPENDENCY
OPENFOAM_ENVIRONMENT
EXECUTION
TIMEOUT_CANCELLATION
RESOURCE_RISK
MESHING
MESH_QUALITY
INTERNAL
```

Severities:

```text
INFO
WARNING
ERROR
BLOCKING
```

---

## 16. Mesh Validation

`checkMesh` output should be converted into structured information where
possible:

- points
- faces
- cells
- patches
- non-orthogonality
- skewness
- volume failures
- failed checks
- overall status

The system must distinguish:

```text
Mesh Validity
Mesh Quality
Simulation Suitability
CFD Accuracy
```

A valid or high-quality mesh does not prove CFD accuracy.

---

## 17. Visualization Architecture

Visualization is a consumer of artifacts/results.

It may display:

- original geometry
- transformed geometry
- domain
- background mesh
- final mesh
- patches
- refinement
- quality/problem locations

Visualization must not become a required dependency for every computation.

Visualization answers:

> Is the geometry/mesh what I intended?

It does not answer:

> Is the CFD solution physically accurate?

---

## 18. UI Architecture

Recommended tabs:

```text
Project
Geometry
Transform
Domain
Mesh
Refinement
Boundary Layers
Validate
Generate
Results
Logs
```

The UI should:

- collect configuration
- call backend services
- display structured state
- display issues
- display progress/logs
- display results

The UI must not:

- build dependency graphs
- decide OpenFOAM command order
- implement geometry algorithms
- implement cache invalidation
- directly manipulate engineering artifacts without backend services

---

## 19. Performance Architecture

Priority order:

1. Avoid unnecessary computation.
2. Avoid unnecessary file I/O.
3. Reuse valid artifacts.
4. Stream long-running process output.
5. Keep memory bounded.
6. Keep the UI responsive.
7. Add asynchronous execution where justified.

Do not introduce distributed infrastructure without evidence that the application
needs it.

---

## 20. Extension Points

The architecture should allow future adapters for:

- OBJ/PLY
- STEP/STP
- IGES/IGS
- other mesh formats
- additional OpenFOAM versions/profiles

Future CFD functionality must remain separate from preprocessing.

Potential future AI functionality may explain, summarize, or suggest, but must
operate through validated application APIs and must not override deterministic
engineering validation.

---

## 21. Architectural Invariants

The following are architectural invariants:

1. UI does not own engineering orchestration.
2. Configuration is declarative.
3. Dependencies control execution.
4. Transformed geometry is the geometry consumed downstream.
5. STL units are explicit.
6. `blockMeshDict` is generated under `system/`.
7. Feature extraction is conditional and dependency-driven.
8. Cache reuse requires verified dependency identity.
9. Resource risk is assessed before expensive meshing.
10. Validation is structured.
11. Mesh quality is not CFD accuracy.
12. OpenFOAM environment differences are abstracted.
13. AI does not override engineering validation.

Changing an invariant requires an explicit architectural review.
