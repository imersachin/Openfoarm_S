## Summary

<!-- What changes and why, in a few sentences. Link the issue or milestone. -->

**Milestone / scope:** <!-- e.g. M3 Case Generation, or "fix" -->

## Engineering impact

<!-- Tick what this change touches and explain each ticked item below it.
     Rules: openforam_preprocessor/CLAUDE.md and docs/architecture.md. -->

- [ ] **Geometry / units / transformation**: units stay explicit; transformation
      order unchanged or tested; the transformed artifact is what gets meshed
- [ ] **OpenFOAM case or dictionaries**: files written under `system/`; tested
      against which profile/version (openfoam.com / openfoam.org)?
- [ ] **Dependencies / cache**: which config fields invalidate which steps; the
      planner and cache still agree (`test_executed_work_matches_the_dependency_plan`)
- [ ] **Resource safety**: effect on cell / RAM / disk estimates or thresholds
- [ ] **Validation / issues**: new or changed issue codes, severities, or report fields
- [ ] **UI**: presentation only; no engineering logic in `app/`
- [ ] **Configuration schema**: `CONFIG_SCHEMA_VERSION` bumped and migration
      added if needed; units are never filled in automatically
- [ ] **Dependencies (packages)**: new packages are justified and added to `pyproject.toml`

## Tests

<!-- Commands actually run, and their results. Do not tick what you did not run. -->

- [ ] `pytest`: <!-- e.g. 294 passed, 2 skipped -->
- [ ] `ruff check .`
- [ ] `mypy`
- [ ] `pytest -m openfoam tests/integration -v` on a real OpenFOAM install
      (required when OpenFOAM commands, dictionaries or parsing change):
      <!-- version, e.g. v2312 -->

New or changed tests cover:

- [ ] Normal path
- [ ] Failure path(s)
- [ ] Concrete results (coordinates, dictionary contents, invalidation), not just "no exception"
- [ ] Deterministic output, where expected

## Definition of done

- [ ] Required behaviour works and failure paths are handled
- [ ] Architecture boundaries respected (see `docs/architecture.md`, Architectural Invariants)
- [ ] Mesh quality is not presented as CFD accuracy
- [ ] Documentation updated (`docs/architecture.md`, README) where behaviour changed
- [ ] No unrelated changes in the diff

## Risks and uncertainties

<!-- Unverified OpenFOAM behaviour, version-specific assumptions, performance,
     backward compatibility with existing project files. Write "None" if none. -->

## Screenshots

<!-- For UI or visualization changes. Delete this section otherwise. -->
