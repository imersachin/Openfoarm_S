# OpenFOAM Preprocessor

A deterministic Python application for preparing, meshing, validating and
visualizing OpenFOAM cases without hand-editing dictionaries:
STL import with explicit units → transformation → validation → blockMesh /
surfaceFeatureExtract / snappyHexMesh / checkMesh → structured mesh report.

It is a preprocessing tool. It does not run CFD solvers, and a valid,
high-quality mesh does not establish CFD accuracy.

## Requirements

- Python 3.11+
- For meshing: OpenFOAM. The target version is **OpenFOAM v2512**
  (openfoam.com / OpenCFD), the version the real-OpenFOAM tests run against
  (Ubuntu 24.04 under WSL2: package `openfoam2512`). Other openfoam.com releases
  are not verified; openfoam.org releases are supported without feature
  extraction. Source its environment in the shell that starts the application
  (`source /usr/lib/openfoam/openfoam2512/etc/bashrc`).
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

## Run the VAWT mesh generator

The VAWT app runs inside WSL, where OpenFOAM is installed, and is opened from
the Windows browser. It serves on `127.0.0.1` only (not on the network).

From Windows, double-click or run `scripts\run_vawt_app.cmd`. Set these
first if the defaults do not fit (Linux paths):

```bat
set VAWT_WSL_DISTRO=Ubuntu-24.04
set VAWT_PYTHON=/path/to/venv/bin/python
```

Or inside WSL: `bash scripts/run_vawt_app.sh` (same settings as environment
variables; also `VAWT_PORT`, `VAWT_PROJECTS_DIR`, `OPENFOAM_BASHRC`).

Then open <http://localhost:8501>. Projects are kept in `~/vawt_projects` in
the WSL file system (Windows: `\\wsl.localhost\<distro>\...`); a project on
a Windows drive works but is slower and is flagged. Stop the app with Ctrl+C;
an active run is cancelled first.

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
