# Claude Agent Guidance

These files define specialized review roles.

## Files

- `testing_agent.md` — test/reliability review
- `architecture_review_agent.md` — architecture compliance review
- `openfoam_review_agent.md` — OpenFOAM-specific review
- `milestone_execution_template.md` — reusable Claude milestone prompt

## Usage

The root `CLAUDE.md` is the permanent project instruction.

Do not paste all agent files into every Claude conversation.

For a milestone, tell Claude which agent review is required, for example:

```text
Implement M2 according to CLAUDE.md and docs/development_workflow.md.

After implementation, run the testing-agent review using:
docs/agents/testing_agent.md

Do not start M3.
```

For architecture-sensitive work:

```text
Run an architecture review using:
docs/agents/architecture_review_agent.md
```

For OpenFOAM-specific work:

```text
Run an OpenFOAM review using:
docs/agents/openfoam_review_agent.md
```
