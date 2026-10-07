# OpenFOAM Preprocessor

A deterministic Python application for preparing, meshing, validating and
visualizing OpenFOAM cases without hand-editing dictionaries:
STL import with explicit units → transformation → validation → blockMesh /
surfaceFeatureExtract / snappyHexMesh / checkMesh → structured mesh report.

It is a preprocessing tool. It does not run CFD solvers, and a valid,
high-quality mesh does not establish CFD accuracy.

## Requirements

- Python 3.11+
- For meshing: OpenFOAM (openfoam.com / ESI releases such as v2312 are fully
  supported; openfoam.org releases are supported without feature extraction),
  with its environment sourced in the shell that starts the application.
  Geometry preparation, dictionary generation and visualization work without
  OpenFOAM.

## Install

```bash
pip install -e ".[dev]"
```

## Run the UI

From the repository root, in a shell where OpenFOAM is sourced:

```bash
streamlit run app/dashboard.py
```

Work through the tabs (Project → Geometry → … → Validate), save the
configuration on **Validate**, then use **Generate**. Results, 3D views, run
history and logs are on the **Results** and **Logs** tabs.

A project directory contains the OpenFOAM case (`system/`, `constant/`),
`reports/`, `logs/`, uploaded `inputs/`, and `.preprocessor/` (configuration
history, artifact manifest, run records, run lock).

## Test

```bash
pytest                                   # unit tests (fake OpenFOAM)
pytest -m openfoam tests/integration -v  # real OpenFOAM, when sourced
ruff check .
mypy
```

## Documentation

- `CLAUDE.md` — engineering rules for contributors
- `docs/architecture.md` — architecture and implemented conventions
- `docs/openfoam_basics.md`, `docs/testing_strategy.md`,
  `docs/development_workflow.md`
