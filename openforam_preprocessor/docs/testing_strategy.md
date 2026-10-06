# Testing Strategy

## 1. Testing Goal

Test the engineering workflow, not only individual Python functions.

Testing must cover:

- configuration
- geometry correctness
- OpenFOAM case structure
- dependency invalidation
- artifact/cache behavior
- resource safety
- failure handling
- deterministic output
- OpenFOAM runner behavior
- checkMesh parsing
- user-visible structured issues

---

## 2. Test Pyramid

```text
End-to-End / Real OpenFOAM
            ↑
        Integration
            ↑
           Unit
            ↑
      Static Checks
```

Use the highest level that provides useful evidence without making the test
suite unnecessarily slow or environment-dependent.

---

## 3. Unit Tests

At minimum, consider unit coverage for:

- configuration models
- configuration validation
- geometry transformation
- geometry validation
- geometry metrics
- resource estimator
- dependency planner
- artifact identity/hashing
- dictionary generation
- issue model
- checkMesh parser
- mesh-quality validator

---

## 4. Geometry Tests

Test actual geometry results.

Required cases should include, where applicable:

- valid STL
- invalid STL
- empty geometry
- degenerate geometry
- multiple components
- explicit unit conversion
- scale
- rotation
- translation
- combined transformations
- extreme dimensions

For transformations, verify concrete coordinate/dimension changes.

Do not treat "function returned successfully" as sufficient evidence.

---

## 5. Dictionary Tests

Verify:

- `system/blockMeshDict`
- `system/surfaceFeatureExtractDict`
- `system/snappyHexMeshDict`
- `system/meshQualityDict`
- required keys
- correct geometry references
- patch names
- refinement settings
- `.eMesh` references when required
- deterministic output

Tests should fail if dictionaries are written to the wrong case path.

---

## 6. Dependency Tests

At minimum:

```text
Transformation change
→ transformed geometry stale
→ feature extraction stale when applicable
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
Quality threshold change
→ validation stale
→ mesh remains reusable
```

Test that unrelated settings do not cause unnecessary invalidation.

---

## 7. Cache/Artifact Tests

Test:

1. identical inputs → cache hit
2. changed inputs → cache miss
3. unrelated settings → no unnecessary invalidation
4. OpenFOAM profile change → invalidation where relevant
5. missing artifact → cache rejection
6. corrupted artifact → cache rejection
7. dependency hash mismatch → cache rejection

Never treat file existence alone as cache validity.

---

## 8. Resource Tests

Test relative behavior:

- more background cells → higher estimated cost
- more refinement → higher estimated cost
- more layers → higher estimated cost
- greater geometry complexity → higher estimated cost

Test all statuses:

```text
SAFE
WARNING
HIGH RESOURCE RISK
BLOCKED
```

Test:

- low RAM
- low disk
- high refinement
- large geometry
- many layers

Resource limits should be configurable.

Tests must not depend on one developer's machine-specific resources.

---

## 9. Runner Tests

Mock/test:

- success
- non-zero exit
- timeout
- cancellation
- missing executable
- stderr
- large logs
- unexpected output

Verify structured results contain the required fields.

---

## 10. checkMesh Tests

Maintain fixtures for:

- successful checkMesh
- failed checks
- high non-orthogonality
- high skewness
- zero/negative volume
- unexpected formatting
- version-specific formatting where supported

The parser should preserve useful raw evidence.

---

## 11. Mesh Quality Tests

Keep separate assertions for:

```text
mesh validity
mesh quality
simulation suitability
CFD accuracy
```

Never make a test imply that mesh quality proves CFD accuracy.

---

## 12. Failure-Path Tests

Include:

- missing STL
- invalid STL
- missing OpenFOAM executable
- invalid configuration
- invalid dictionary
- missing `.eMesh`
- failed blockMesh
- failed feature extraction
- failed snappyHexMesh
- checkMesh failure
- timeout
- cancellation
- insufficient resources
- unexpected output
- missing/corrupted artifact

---

## 13. Determinism Tests

Where deterministic output is expected:

- same configuration → same generated dictionary
- same geometry/configuration → equivalent transformation result
- same dependency state → same execution plan

Avoid assertions that depend on:

- absolute machine-specific paths
- timestamps
- process IDs
- nondeterministic ordering

unless those values are intentionally part of the contract.

---

## 14. Static Checks

Run project-configured checks such as:

```text
ruff check .
mypy .
pytest
```

Only report a check as passing if it was actually executed.

If `mypy` is not configured or applicable, report that fact rather than inventing
a result.

---

## 15. Testing-Agent Review

The testing agent is a reviewer.

It must:

- inspect the implementation
- inspect tests
- run relevant tests
- identify confirmed failures
- identify missing coverage
- distinguish defects from risks
- check the git diff for unintended changes

It must not redesign the architecture.

See:

`docs/agents/testing_agent.md`

---

## 16. Severity

Use:

```text
CRITICAL
HIGH
MEDIUM
LOW
```

### CRITICAL

Blocks core workflow, causes serious engineering corruption, or creates a
severe safety/reproducibility problem.

### HIGH

Breaks important MVP behavior or produces materially incorrect results.

### MEDIUM

Important defect or coverage gap with a practical workaround.

### LOW

Minor issue, cleanup, documentation, or low-impact coverage gap.

---

## 17. Test Review Report

Use this structure:

```text
# Testing Review

## Scope
<what was reviewed>

## Tests Executed
<commands and outcomes>

## Confirmed Findings

### [SEVERITY] Finding title
File/Class:
Evidence:
Problem:
Impact:
Reproduction:
Recommended fix:

## Missing Coverage

- ...

## Risks / Observations

- ...

## Overall Assessment

PASS / PASS WITH FINDINGS / FAIL
```

Do not invent failures.

Do not claim a behavior was tested if it was not tested.

---

## 18. Milestone Verification

At the end of each milestone:

1. Run focused tests.
2. Run related integration tests.
3. Run the full suite when practical.
4. Run static checks.
5. Run testing-agent review.
6. Fix approved critical/high findings.
7. Re-run affected tests.
8. Review the diff.
9. Record remaining known risks.

Do not automatically proceed to the next milestone.
