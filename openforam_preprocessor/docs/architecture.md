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

Case-generation conventions (approved in M3):

- Feature extraction is required only when `mesh.surface.extract_features` is
  enabled. Only then are `system/surfaceFeatureExtractDict`, the `.eMesh`
  reference and `explicitFeatureSnap true` generated; a stale generated
  feature dictionary is removed when extraction is disabled.
- Supported profile: openfoam.com (ESI), using `surfaceFeatureExtract -case
  <case>` (see `openfoam/commands.py`). For openfoam.org (Foundation), feature
  extraction is reported as a BLOCKING `FEATURE_EXTRACTION_UNSUPPORTED_PROFILE`
  issue rather than generating unverified syntax.
- `includedAngle = 180 - feature_angle_deg` (the tutorial pairing 150/30).
- Numbers are written with shortest round-trip precision; non-finite values
  are rejected in configuration and by the dictionary writer.
- If `max_cells_per_axis` limits the background cells, generation continues
  with a `BACKGROUND_CELLS_CAPPED` warning that reports the effective cell size.

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

Implementation (M6): `mesh/estimator.py`, gated in `core/workflow/pipeline.py`.

- Cells: background (effective blockMesh cells) + surface band at the maximum
  surface level (from the transformed geometry's surface area) + feature band
  when feature refinement is finer than surface refinement + boundary layers.
- RAM/disk estimates = cells × configurable bytes-per-cell coefficients,
  compared with *available* RAM (psutil) and free disk in the case directory.
  Fractions above 0.5 / 0.8 / 1.0 of available give WARNING / HIGH RESOURCE
  RISK / BLOCKED (configurable `ResourceThresholds`). Unknown resources are a
  WARNING, never a guess. Exceeding `max_global_cells` or a large serial run
  is a WARNING.
- The preflight runs only when meshing will actually run (not for verified
  cached meshes) and writes `reports/resource_preflight.json` with a
  disclaimer. BLOCKED always stops; HIGH RESOURCE RISK stops unless the caller
  passes `allow_high_resource_risk=True`.
- Before any OpenFOAM command, the environment check reports every missing
  executable at once (BLOCKING) and warns when `WM_PROJECT_VERSION` does not
  match the profile's release naming.

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

Implementation (M4): `core/workflow/dependency_graph.py` and
`core/workflow/planner.py`.

- Operations, in execution order: import geometry (units + transform →
  artifact), validate geometry, generate blockMesh / feature-extraction /
  snappy / meshQuality dictionaries, blockMesh, surfaceFeatureExtract,
  snappyHexMesh, checkMesh, validate mesh.
- Every configuration field maps to the operations that read it directly;
  staleness then follows the data-flow edges. A test enforces that every
  field has a rule. An unmapped path is reported and treated conservatively
  as invalidating everything.
- `snappyHexMesh -overwrite` replaces the background mesh in place, so any
  snappy re-run also re-runs blockMesh (but not blockMeshDict generation).
- `mesh.quality` holds the post-checkMesh acceptance limits (change → validate
  mesh only). `mesh.snappy_quality` holds the `meshQualityDict` values snappy
  uses while meshing (change → mesh stale).
- Feature extraction is planned only when enabled; otherwise it is listed as
  skipped. The feature-dictionary step still runs to remove a stale file.
- Settings that only reach a generated file while enabled
  (`feature_refinement_level` without feature extraction, `number_of_layers`
  without layers) are ignored while inactive and reported as `inactive_paths`.
- The plan explains what is stale and why. Execution decisions are made by
  verified artifacts (section 12); a test asserts both agree for every
  configuration field.

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

Implementation (M5): `core/artifacts/` and `core/workflow/pipeline.py`.

- Manifest: `<case>/.preprocessor/artifacts.json`, one record per cached
  operation: input hashes, output content hashes, creation time.
- Cached operations and their recorded inputs:
  - geometry: source STL content, geometry configuration, artifact format
    version, validator limits → transformed STL + geometry report;
  - feature extraction: `surfaceFeatureExtractDict`, `controlDict`,
    transformed STL, tool identity → `.eMesh`;
  - mesh (blockMesh + snappyHexMesh as one unit, because snappy overwrites the
    background mesh in place): all meshing dictionaries, transformed STL,
    `.eMesh` when used, tool identity → `constant/polyMesh` files;
  - checkMesh: `constant/polyMesh` files, tool identity → checkMesh log.
- `constant/polyMesh/sets/` (checkMesh diagnostics) is excluded from mesh hashes.
- Tool identity: profile, `WM_PROJECT`, `WM_PROJECT_VERSION`, resolved
  executable paths. Unknown values are recorded as `None`.
- Reuse requires a record, identical input hashes, and every output present
  with an identical hash. A record is removed before its operation re-runs, so
  partial outputs from a failed run are never reused. An unreadable manifest
  reuses nothing and is reported (`ARTIFACT_MANIFEST_UNREADABLE`).
- Dictionaries are cheap and always regenerated (write-if-changed); mesh
  validation always re-runs. `force=True` ignores the cache.

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

Implementation (M6): `mesh/parser.py` and `mesh/validator.py`.

- Parsed: points, faces, cells, boundary patches (name/faces/points), max and
  average non-orthogonality, severely non-orthogonal face count, max skewness,
  max aspect ratio, min/max cell volume, zero/negative volume cells, failed
  check count and `***` messages, `*` warnings, overall status, and whether the
  output was recognized at all. Missing values stay `None`.
- `mesh_quality_report.json` contains separate `mesh_validity`
  (VALID / INVALID / UNKNOWN, from checkMesh), `mesh_quality` (WITHIN_LIMITS /
  REVIEW / NOT_EVALUATED, against `mesh.quality`), and `simulation_suitability`
  and `cfd_accuracy`, which are always `NOT_ASSESSED`, with an explanatory note.

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

Implementation (M8): `visualization/foam_reader.py`, `visualization/views.py`
(plotly), exposed through `ProjectService` and the Results tab.

- Geometry view: transformed artifact (what is meshed) overlaid on the source
  STL converted to metres only, plus the domain box and `locationInMesh`.
- Mesh view: boundary patches from `constant/polyMesh` (uncompressed ASCII,
  as configured in `controlDict`; binary/compressed meshes are reported, not
  guessed), coloured per patch, with checkMesh face/cell/point sets from
  `constant/polyMesh/sets` drawn at their locations (cell sets at approximate
  centres).
- Quality chart: measured checkMesh values against the acceptance limits.
- Views load only on request and sample large surfaces deterministically
  (default 150,000 faces) with a visible note. Plotly is imported lazily;
  a test asserts the pipeline and services do not import it.

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

Implementation (M7): `app/dashboard.py` (run with `streamlit run
app/dashboard.py` from the repository root), `app/components/widgets.py`, and
the application service `core/services/project_service.py`.

- The UI calls only `ProjectService`: load/validate/save configuration (all
  problems as structured issues, including unreadable or outdated project
  files), preview which steps a change makes stale (via the planner), store
  uploaded STLs under `inputs/`, prepare the case, run the resource check, run
  meshing, and read reports, logs and generated dictionaries.
- Edits go into a draft held in the session; the Validate tab shows live
  validation and the stale-step preview, and saves a new revision. Generate
  actions always use the saved configuration.
- Source units have no default; a new project cannot be saved until they are
  chosen. HIGH RESOURCE RISK meshing requires an explicit checkbox.
- Results show mesh validity, mesh quality, simulation suitability and CFD
  accuracy as separate values (the last two always NOT_ASSESSED).
- Known limitation: meshing runs synchronously behind a spinner; logs are
  viewable after the run, not streamed live.

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
