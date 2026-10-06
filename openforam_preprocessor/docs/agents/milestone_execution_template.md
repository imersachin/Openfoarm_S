# Milestone Execution Template

Use this template in Claude when starting a milestone.

Replace the bracketed values.

---

## Task

Execute milestone:

`[MILESTONE] — [NAME]`

---

## Scope

Implement only the work defined for this milestone.

Do not start later milestones.

---

## Required Reading

Before coding, read:

- `CLAUDE.md`
- `docs/architecture.md`
- `docs/development_workflow.md`
- `docs/openfoam_basics.md`
- `docs/testing_strategy.md`

Also read any milestone-specific agent guidance.

---

## Current State

Inspect:

- current source tree
- relevant implementation
- relevant tests
- fixtures
- current git diff/status

Do not assume the repository matches the target architecture.

---

## Plan

Before implementation, provide:

1. Current implementation status
2. Required changes
3. Files likely to change
4. Tests to add/update
5. Risks
6. Open questions

Wait for approval if this is a multi-file or architectural change.

---

## Implementation Rules

- Make the smallest correct change.
- Preserve existing working behavior.
- Do not redesign unrelated architecture.
- Do not add unnecessary dependencies.
- Do not invent OpenFOAM behavior.
- Do not weaken validation to make tests pass.
- Do not modify unrelated files.

---

## Verification

After implementation:

1. Run focused tests.
2. Run related integration tests.
3. Run full `pytest` when practical.
4. Run `ruff check .`.
5. Run `mypy .` if configured.
6. Run the appropriate review agent.
7. Fix approved CRITICAL/HIGH findings.
8. Re-run affected tests.
9. Inspect the final diff.

---

## Final Report

Return:

```text
Milestone:
Status:

Files changed:
- ...

Behavior implemented:
- ...

Tests executed:
- ...

Static checks:
- ...

Review result:
- ...

Known risks:
- ...

Documentation updated:
- ...

Next milestone:
Do not start automatically.
```
