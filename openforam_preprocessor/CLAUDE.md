# CLAUDE.md

## 1. Project Identity

Project: OpenFOAM Preprocessor

Purpose: a lightweight, deterministic Python application for preparing, meshing,
validating, visualizing, and exporting OpenFOAM cases without requiring users
to manually edit OpenFOAM dictionaries.

The MVP is an engineering preprocessing application. It is not a CFD solver,
CAD system, HPC scheduler, cloud platform, or AI assistant.

The current implementation may be incomplete. Treat the documented target
architecture as the intended design, but treat existing code as the source of
truth for what is actually implemented until verified.

---

## 2. Instruction Hierarchy

When instructions conflict, use this order:

1. Direct user instruction for the current task.
2. This `CLAUDE.md`.
3. `docs/architecture.md`.
4. `docs/development_workflow.md`.
5. `docs/openfoam_basics.md`.
6. `docs/testing_strategy.md`.
7. Relevant files under `docs/agents/`.
8. Existing implementation and tests.

If a lower-level document conflicts with a higher-level instruction, do not
silently resolve the conflict. Report it.

---

## 3. Required Reading

### Before architectural work

Read:

- `CLAUDE.md`
- `docs/architecture.md`
- `docs/development_workflow.md`
- `docs/openfoam_basics.md`
- `docs/testing_strategy.md`

### Before geometry work

Also inspect:

- current geometry implementation
- relevant geometry tests
- geometry fixtures, if present

### Before OpenFOAM execution/case-generation work

Also inspect:

- current OpenFOAM implementation
- relevant dictionaries/fixtures
- OpenFOAM-related tests
- the relevant OpenFOAM version/profile assumptions

### Before caching/dependency work

Also inspect:

- current workflow/dependency code
- artifact implementation
- relevant tests
- current configuration models

### Before VAWT work

Also read `docs/vawt_mesh_generator.md`.

Do not claim to have followed documentation that you did not actually read.

---

## 4. Non-Negotiable Engineering Principles

1. UI is presentation only.
2. Core engineering logic must not be implemented in Streamlit pages.
3. Configuration is declarative.
4. Dependency graphs control execution.
5. Validate before expensive meshing whenever practical.
6. Resource safety is mandatory.
7. Errors must explain what failed, where, severity, likely cause, and next action.
8. Geometry transformations must produce the actual geometry artifact consumed downstream.
9. STL units must never be silently assumed.
10. Mesh validity, mesh quality, simulation suitability, and CFD accuracy are different concepts.
11. Engineering behavior must be deterministic for identical inputs and environment/profile.
12. AI/LLM functionality is outside the MVP workflow.
13. Do not silently change engineering semantics to make a test pass.
14. Do not introduce infrastructure that the current requirements do not justify.
15. Prefer the smallest correct change over broad refactoring.

---

## 5. Architectural Boundaries

The intended high-level flow is:

User Intent
→ Configuration
→ Validation
→ Dependency Planning
→ Deterministic Operations
→ OpenFOAM
→ Structured Validation
→ Visualization
→ Export

The intended geometry flow is:

Import
→ Explicit Units
→ Transform
→ Validate
→ Artifact
→ Mesh

The intended meshing flow is:

Domain
→ blockMesh
→ surfaceFeatureExtract when required
→ snappyHexMesh
→ checkMesh
→ Structured Mesh Validation

Do not bypass these boundaries merely because a shortcut is easier to implement.

---

## 6. OpenFOAM Case Rules

For the MVP, the case structure is:

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

`system/blockMeshDict` is the required location for `blockMeshDict`.

Do not invent alternate case paths without an explicit architectural reason.

The typical meshing sequence is:

```text
blockMesh
→ surfaceFeatureExtract (when required)
→ snappyHexMesh
→ checkMesh
```

If `snappyHexMesh` references an `.eMesh`, feature extraction is a dependency
and must be represented as such.

---

## 7. Geometry Rules

STL units are not assumed to be reliable.

The application must make source and target units explicit.

Transformation is an actual preprocessing operation:

```text
Original STL
→ unit conversion
→ scale
→ rotation
→ translation
→ orientation
→ transformed artifact
→ validation
```

The transformed artifact, not the original input, must be consumed by
downstream meshing.

Transformation order must be explicit and tested.

Geometry validation should consider, where applicable:

- finite coordinates
- empty geometry
- degenerate faces
- duplicate/problematic faces
- dimensions
- connected components
- watertightness
- winding/orientation
- topology/geometry consistency

Do not silently repair engineering geometry unless the specification explicitly
permits that behavior.

---

## 8. Refinement Rules

Treat these as separate concepts:

- background/base resolution
- surface refinement
- feature refinement
- local/region refinement
- boundary-layer refinement

Do not collapse them into one generic refinement setting unless the architecture
explicitly requires it.

Increasing refinement generally increases cells, RAM, CPU time, and disk use.

---

## 9. Resource Preflight

Before expensive meshing, evaluate resource risk using available information
such as:

- estimated cell/refinement cost
- background cells
- geometry complexity
- refinement levels
- boundary layers
- available RAM
- CPU capacity
- available disk

Use these statuses:

```text
SAFE
WARNING
HIGH RESOURCE RISK
BLOCKED
```

These are heuristic risk classifications, not exact runtime predictions.

Do not present a heuristic estimate as a guaranteed runtime or exact memory
requirement.

Resource thresholds must be configurable or derived from the runtime
environment. Do not hard-code assumptions about one development machine.

---

## 10. OpenFOAM Runner Rules

The runner/environment layer must account for:

- executable discovery
- OpenFOAM profile/version
- environment variables
- working directory
- timeout
- cancellation where supported
- stdout/stderr
- exit code
- duration
- run ID
- structured status

Expected status values:

```text
QUEUED
RUNNING
SUCCESS
FAILED
TIMEOUT
CANCELLED
```

Do not hide subprocess failures behind generic exceptions.

---

## 11. Validation and Error Rules

Errors/findings should be structured.

Relevant categories include:

- INPUT
- UNITS
- GEOMETRY
- TRANSFORMATION
- CONFIGURATION
- DEPENDENCY
- OPENFOAM_ENVIRONMENT
- EXECUTION
- TIMEOUT_CANCELLATION
- RESOURCE_RISK
- MESHING
- MESH_QUALITY
- INTERNAL

Relevant severities include:

```text
INFO
WARNING
ERROR
BLOCKING
```

A user-facing issue should answer:

1. What failed?
2. At which stage?
3. Why is it likely to have failed?
4. How severe is it?
5. What should the user do next?
6. Where is the raw evidence/log, when available?

Do not swallow raw logs when they are needed for diagnosis.

---

## 12. Mesh Validation Rules

Parse `checkMesh` output into structured data when possible, including:

- points
- faces
- cells
- patches
- non-orthogonality
- skewness, where available
- volume failures
- failed checks
- overall status

Keep these concepts separate:

```text
Mesh validity
≠
Mesh quality
≠
Simulation suitability
≠
CFD accuracy
```

Never tell users that a good mesh guarantees accurate CFD.

---

## 13. Performance Rules

Prefer:

- lazy loading
- content hashes
- dependency hashes
- write-if-changed behavior
- incremental execution
- streaming logs
- bounded memory use
- responsive UI
- asynchronous subprocess handling where appropriate

Do not introduce distributed systems, databases, queues, or other infrastructure
without a demonstrated requirement.

Avoid unnecessary file I/O and unnecessary recomputation.

---

## 14. Dependency and Cache Rules

Execution must be driven by dependency state.

Examples:

```text
Geometry translation change
→ transformed geometry stale
→ feature extraction stale
→ mesh stale
→ checkMesh stale
```

```text
Surface refinement change
→ snappy configuration stale
→ mesh stale
→ checkMesh stale
```

```text
Quality-threshold-only change
→ validation stale
→ geometry/mesh remain valid
```

An existing file is not automatically a valid cache entry.

Cache/artifact reuse requires verification of:

- content hash
- input dependency hashes
- relevant configuration
- OpenFOAM profile/version
- tool/version metadata
- artifact status

---

## 15. UI Rules

Streamlit is a presentation layer.

Recommended workflow tabs:

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

UI code must call backend services/models rather than contain engineering
orchestration, dependency planning, OpenFOAM command sequencing, or validation
algorithms.

Expose engineering concepts to users.

Hide implementation details such as:

- dictionary syntax
- command sequencing
- cache invalidation
- dependency resolution
- subprocess internals

Advanced users may inspect raw dictionaries and logs.

---

## 16. Development Protocol

For every milestone:

```text
1. Read the applicable specification.
2. Inspect the current implementation.
3. Inspect relevant tests.
4. Identify affected files.
5. Produce a concise implementation plan.
6. Wait for approval when the task is architectural or multi-file.
7. Implement the smallest correct change.
8. Add/update tests.
9. Run focused tests.
10. Run related integration tests.
11. Run the full test suite when practical.
12. Run static checks configured by the project.
13. Review the git diff.
14. Run the relevant testing-agent review.
15. Fix approved critical/high issues.
16. Re-run affected tests.
17. Report results and remaining risks.
18. Stop.
```

Do not automatically start the next milestone.

---

## 17. Change Discipline

Before changing code, determine:

- current behavior
- intended behavior
- affected interfaces
- affected artifacts
- affected dependencies
- required tests
- possible backward-compatibility impact

Do not:

- perform unrelated cleanup
- rename broad portions of the codebase without need
- replace working architecture with a new architecture
- add dependencies without justification
- modify unrelated tests to hide failures
- weaken validation to make tests pass
- remove failure-path tests because they are inconvenient

If an architectural change appears necessary, stop and explain the conflict
before implementing it.

---

## 18. Testing Requirements

Every meaningful feature needs tests appropriate to its risk.

At minimum, consider:

- normal path
- invalid input
- failure path
- boundary conditions
- deterministic output
- dependency invalidation
- cache behavior
- resource-risk behavior
- OpenFOAM path correctness
- geometry correctness
- user-visible error behavior

Use `docs/testing_strategy.md` as the testing authority.

---

## 19. Milestones

The intended milestone order is:

```text
M0 Audit
M1 Architecture
M2 Geometry
M3 Case Generation
M4 Dependency Planner
M5 Artifacts/Cache
M6 Validation/Resource Safety
M7 UI
M8 Visualization
M9 Reliability
```

The VAWT mesh generator (`docs/vawt_mesh_generator.md`) follows its own
milestone order:

```text
V0 Method proof and baseline
V1 Configuration and analysis
V2 Case generation
V3 Pipeline
V4 Service and background runs
V5 UI
V6 Visualization
V7 Export and verification
```

Do not skip ahead because a later feature is convenient to implement.

A milestone is not complete merely because the code runs. It requires tests,
review, documentation updates where appropriate, and verification against its
acceptance criteria.

---

## 20. Definition of Done

A change is done only when:

- required behavior works
- failure paths are considered
- tests exist and pass
- resource implications are considered
- architecture is respected
- dependencies are justified
- output is deterministic where expected
- I/O is not unnecessarily duplicated
- relevant documentation is updated
- testing-agent review is satisfactory
- no unrelated changes are included

---

## 21. Uncertainty Rule

When uncertain:

```text
Inspect code
→ inspect architecture
→ inspect OpenFOAM documentation in this project
→ inspect tests/fixtures
→ identify the uncertainty
→ make the smallest defensible change
→ add a test
→ report the uncertainty
```

Never invent OpenFOAM behavior.

Never silently reinterpret engineering requirements.

If two requirements conflict, report the conflict.

---

## 22. AI Boundary

AI/LLM functionality is not required for the MVP.

Future AI may:

- explain errors
- suggest settings
- summarize logs
- compare project versions
- assist with setup

AI must not silently override deterministic engineering validation or directly
bypass validated application APIs.
