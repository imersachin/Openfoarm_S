# Development Workflow

## 1. Purpose

This document defines how Claude should modify the OpenFOAM Preprocessor.

The goal is controlled, incremental engineering rather than a large AI-generated
rewrite.

---

## 2. Standard Change Cycle

For a normal feature or fix:

```text
Understand
→ Inspect
→ Plan
→ Implement
→ Test
→ Review
→ Fix approved findings
→ Verify
→ Report
→ Commit
```

Do not automatically continue into unrelated work.

---

## 3. Before Coding

1. Read `CLAUDE.md`.
2. Read the relevant architecture section.
3. Read relevant OpenFOAM guidance.
4. Inspect current implementation.
5. Inspect relevant tests.
6. Identify affected interfaces.
7. Identify affected artifacts/dependencies.
8. Identify required tests.
9. Identify risks.

For architectural changes, read all required project documents before coding.

---

## 4. Planning Rule

For changes involving multiple modules, new interfaces, dependency behavior,
artifact behavior, OpenFOAM execution, or geometry semantics:

- produce a concise plan first;
- identify files to change;
- identify tests to add/change;
- identify risks;
- wait for user approval unless the user explicitly requested immediate
  implementation.

For small, isolated fixes, implementation may proceed directly if the behavior
is unambiguous.

---

## 5. Smallest Correct Change

Prefer:

- existing abstractions
- existing models
- existing utilities
- minimal new interfaces
- focused tests

Avoid:

- broad rewrites
- unrelated refactoring
- speculative abstractions
- premature frameworks
- unnecessary dependencies

---

## 6. Milestones

### M0 — Repository Audit

No broad code changes.

Deliver:

- repository/file map
- architecture compliance review
- actual behavior
- defects
- missing dependencies
- missing tests
- risks
- recommended M1 work

### M1 — Architecture Foundation

Implement only approved architecture foundation work:

- clean module boundaries
- structured issue model
- configuration validation
- execution/service interfaces
- correct OpenFOAM case paths

Do not implement caching as part of M1 unless explicitly approved.

### M2 — Geometry

Implement:

```text
STL import
→ explicit units
→ scale
→ rotation
→ translation/orientation
→ validation
→ transformed artifact
```

Tests must verify actual geometry coordinates, not merely that a function returns
without error.

### M3 — Case Generation

Implement and test:

- `system/blockMeshDict`
- `system/surfaceFeatureExtractDict`
- `system/snappyHexMeshDict`
- `system/meshQualityDict`
- deterministic dictionary generation
- geometry references
- patch references
- refinement values

### M4 — Dependency Planner

Implement:

```text
ChangeSet
→ DependencyGraph
→ ExecutionPlan
→ Operations
```

The planner must identify exactly what becomes stale after a configuration
change.

### M5 — Artifacts/Cache

Implement:

- artifact identity
- content hashes
- dependency hashes
- reuse
- invalidation
- stale/corrupt artifact rejection

### M6 — Validation/Resource Safety

Implement:

- resource preflight
- structured issues
- environment validation
- mesh validation
- checkMesh parsing
- mesh-quality reporting

### M7 — UI

Implement the Streamlit workflow.

Keep engineering logic outside the UI.

### M8 — Visualization

Implement:

- geometry preview
- transformed geometry
- domain/mesh views
- quality/problem views
- lazy loading where useful

### M9 — Reliability

Review:

- end-to-end tests
- failure paths
- cancellation
- persistent run metadata
- version handling
- reproducibility
- packaging
- documentation
- performance

---

## 7. Testing After Changes

After meaningful changes:

1. Run focused tests.
2. Run related integration tests.
3. Run the full test suite when practical.
4. Run `ruff check .`.
5. Run `mypy .` if configured.
6. Run the testing-agent review.
7. Review the git diff.
8. Re-run tests after approved fixes.

Do not report tests as passing unless they were actually run.

---

## 8. Commit Rule

Before a milestone commit:

- tests pass
- static checks pass where configured
- no unintended diff remains
- documentation is updated where required
- known critical/high issues are resolved or explicitly accepted

Commit messages should identify the milestone or purpose.

Do not commit unrelated work.

---

## 9. OpenFOAM Change Rule

Before changing OpenFOAM behavior:

- identify the target OpenFOAM version/profile;
- inspect existing command/path assumptions;
- inspect relevant fixtures;
- verify dictionary location and references;
- test failure behavior.

Never invent command syntax or dictionary semantics.

If behavior differs by OpenFOAM version, model the difference explicitly.

---

## 10. Geometry Change Rule

Geometry changes must verify actual geometry results.

Do not accept:

```text
function returned successfully
```

as proof that a transformation worked.

Verify coordinates, dimensions, orientation, or another concrete geometry
property.

---

## 11. Dependency/Cache Change Rule

For every configuration input, identify:

- direct consumers
- generated artifacts
- downstream consumers
- invalidation behavior

Explicitly test both:

- changed input → required invalidation
- unrelated input → no unnecessary invalidation

---

## 12. Uncertainty Rule

When uncertain:

```text
inspect code
→ inspect specification
→ inspect tests
→ inspect fixtures
→ identify uncertainty
→ ask or make the smallest defensible change
→ test
→ report
```

Do not silently make an engineering assumption.

---

## 13. Definition of Done

A task is complete when:

- requested behavior works
- relevant failure paths are handled
- tests exist and pass
- architecture remains valid
- output is deterministic where expected
- resources are considered
- no unrelated changes are included
- documentation is updated where necessary
- review findings are addressed
