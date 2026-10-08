# VAWT Mesh Generator — Build Specification

| | |
|---|---|
| Status | V0 to V4 complete; V5 next |
| Place this file at | `openforam_preprocessor/docs/vawt_mesh_generator.md` |
| Written against | `main` after M9 (commit "Replace placeholder PR template with project checklist") |
| Replaces | The standalone "Mesh" app (React frontend + WSL backend). That app is reference material only; none of its code is copied. |

---

## 1. Purpose

Build, from scratch, a VAWT (vertical-axis wind turbine) mesh generator inside
this repository. It produces a rotor mesh with a rotating zone, an optional
outer domain with wake refinement, a structured quality report, and a Fluent
`.msh` export.

It is a second workflow on the existing engine, not a second engine.

### In scope

- STL rotor import with explicit units, transformation and validation
- Rotor analysis: diameter, span, rotation axis (suggestions, never silent defaults)
- Rotating zone: sliding interface (AMI) or single-mesh cell zone
- Outer domain with named patches, wake box, interface and blade refinement
- Boundary layers with relative or absolute first-layer sizing
- Resource preflight, cached incremental execution, cancellation
- Mesh validation, 3D boundary view, cross-sections
- Export: Fluent `.msh`, OpenFOAM case
- A Streamlit prototype UI built for low latency

### Out of scope

- CFD setup or solving (fields, schemes, solver control, MRF/dynamicMesh dictionaries)
- Geometry repair
- Parallel (decomposed) meshing
- Any change to the behaviour of the existing generic workflow

---

## 2. Position in the Project

### 2.1 Instruction hierarchy

For VAWT work this document ranks directly below `docs/architecture.md`.
`CLAUDE.md` and `docs/architecture.md` win every conflict. Report conflicts;
do not resolve them silently.

All 13 architectural invariants apply unchanged. The development protocol,
change discipline, testing requirements and definition of done in `CLAUDE.md`
apply unchanged.

### 2.2 Additions the owner must approve in existing documents

This specification extends the documented architecture in four places. Each
needs a matching edit before the milestone that depends on it.

| Document | Addition | Needed by |
|---|---|---|
| `CLAUDE.md` §3 | "Before VAWT work, also read `docs/vawt_mesh_generator.md`." | V0 |
| `CLAUDE.md` §19 | Milestone list V0–V7 from section 18 below | V0 |
| `docs/architecture.md` §3 | `vawt/` package and `app/vawt/` in the target tree | V1 |
| `docs/architecture.md` §18 | A second UI entry point; section navigation instead of one tab strip | V5 |

### 2.3 Rules specific to this workflow

1. Reuse engine components; never fork or copy them.
2. Changes to shared modules are additive. Every existing test passes unmodified.
3. `streamlit` is imported only under `app/`. `plotly` is imported lazily.
4. Each OpenFOAM sub-case follows the documented case structure; `blockMeshDict`
   is under `system/` in every sub-case.
5. No queue, database or server infrastructure. Background work is an
   in-process worker.
6. No OpenFOAM dictionary syntax or command option enters the code without
   evidence from a real run on the target version (section 10).

---

## 3. Reuse, New Work, Integration Points

### 3.1 Reused as-is

| Component | Module |
|---|---|
| Structured issues | `core/issues` |
| Verified artifact cache, hashing | `core/artifacts` |
| Run lock, run records | `core/workflow/run_lock.py`, `run_records.py` |
| Command runner (timeout, cancel, separate stderr) | `openfoam/runner.py` |
| Environment check, tool identity | `openfoam/environment.py` |
| Dictionary formatting and write-if-changed | `openfoam/dictionary.py` |
| STL import, transform, artifact writer | `geometry/importer.py`, `geometry/transformer.py` |
| Geometry validator | `geometry/validator.py` |
| checkMesh parser, mesh validator, quality limits | `mesh/parser.py`, `mesh/validator.py`, `core/config/models.py` |
| polyMesh reader | `visualization/foam_reader.py` |
| UI field widgets, issue rendering | `app/components/widgets.py` |

### 3.2 New

Configuration model, presets, rotor metrics, cross-field validation, two-region
case generator, operation graph, pipeline, service, preview builder, exporter,
Streamlit app.

### 3.3 Integration points (verified in the current code)

These existing parts assume one case and `ProjectConfig`. Each needs a decision
in V1, recorded in the plan, before code is written.

| # | Fact in the current code | Consequence |
|---|---|---|
| 1 | `DependencyGraph` and `ExecutionPlanner` use module-level tables bound to `PipelineOperation` and `ProjectConfig`. | VAWT needs its own tables. Preferred: let the graph accept tables as arguments (additive). Alternative: a sibling graph in `vawt/`. |
| 2 | `IssueStage` has no stage for merge, patch creation, zone creation, surface check or export. | Add enum members (additive). |
| 3 | `OpenFOAMRunner` writes logs to `<case_root>/logs`; `openfoam/commands.py` defines four steps. | Decide where sub-case logs live; add command builders. |
| 4 | `OpenFOAMMeshCaseGenerator` writes one region, patches `xmin…zmax`, an empty `refinementRegions`, and `relativeSizes true`. | VAWT has its own generator built on the shared formatting helpers. The existing generator is not modified. |
| 5 | `ResourceEstimator.estimate_cells` takes a `ProjectConfig`. | Extract or add an entry point that takes cell-count inputs directly. |
| 6 | `geometry/validator.py` reports `INWARD_NORMALS` from the total signed volume. A rotor whose blades are inside-out but whose shaft is not passes with only an INFO. | Add a per-body orientation check (additive, new issue code). |
| 7 | The geometry artifact is written as ASCII STL. | Measure read/hash time on a large rotor in V0. Do not change the format without approval. |
| 8 | `ProjectService.read_log` reads the whole file, then slices. | VAWT log tail reads the last bytes by seek. |

### 3.4 Integration-point decisions (V1, approved by the owner)

| # | Decision | Implemented in |
|---|---|---|
| 1 | `DependencyGraph` and `ExecutionPlanner` accept their dependency tables as arguments. The current module-level tables stay the defaults, so the generic workflow is unchanged (additive). | V3 |
| 2 | New `IssueStage` members (merge, patch creation, zone creation, surface check, export) are added when an operation first emits them, not before. | V3 |
| 3 | Sub-case logs live in `<project>/logs/<subcase>/`. New command builders are added to `openfoam/commands.py`; existing builders are unchanged. | V3 |
| 4 | VAWT has its own generator, `vawt/case_generator.py`, built on the shared formatting helpers. `OpenFOAMMeshCaseGenerator` is not modified. | V2 |
| 5 | `ResourceEstimator` gains an entry point that takes cell-count inputs directly; `estimate_cells(ProjectConfig)` is unchanged. | V3 |
| 6 | Per-body orientation: `geometry/metrics.py` (`body_orientations`) reports each closed body; `vawt/validation.py` raises `BODY_INSIDE_OUT` (WARNING). `geometry/validator.py` is unchanged. | V1 (done) |
| 7 | The ASCII STL artifact format stays as is until V0 measures read/hash time on a large rotor. Any change needs owner approval. | V0 |
| 8 | `VawtService.log_tail` reads a bounded number of bytes from the end of the log by seek. `ProjectService.read_log` is unchanged. | V4 |

---

## 4. Repository Layout

```text
openforam_preprocessor/
├── vawt/
│   ├── config.py           declarative configuration models
│   ├── presets.py          domain / wake / sizing defaults as data
│   ├── rotor_metrics.py    diameter, span, axis suggestion
│   ├── validation.py       cross-field engineering checks → issues
│   ├── case_generator.py   dictionaries for each sub-case
│   ├── operations.py       operations, dependency tables
│   ├── pipeline.py         cached execution
│   ├── previews.py         decimated surfaces and slices for display
│   ├── exporter.py         Fluent export
│   └── service.py          the only interface the UI calls
├── app/
│   ├── vawt_app.py         Streamlit entry point
│   └── vawt/
│       ├── shell.py        header, navigation, status strip
│       └── sections/       one module per section
└── tests/
    ├── unit/vawt/
    ├── integration/        real-OpenFOAM tests, marker `openfoam`
    └── fixtures/vawt/      rotor STL generator, captured logs and dictionaries
```

Add `vawt` to the wheel and mypy package lists in `pyproject.toml`. Do not
create a module until it has content.

---

## 5. Project Folder Layout

```text
<project>/
├── .preprocessor/          configuration history, artifacts.json, runs/, run.lock, status.json
├── inputs/                 uploaded STL
├── cases/
│   ├── outer/              stationary domain          (0/, constant/, system/)
│   ├── rotor/              rotating zone with blades  (0/, constant/, system/)
│   └── merged/             final mesh
├── previews/               display artifacts, keyed by mesh hash
├── export/                 .msh
├── reports/
└── logs/
```

`cases/outer` and `cases/merged` exist only when the outer domain is enabled.
One artifact manifest at project level covers all sub-cases.

VAWT files under `.preprocessor/` (V4, `vawt/project_store.py`):
`vawt/project.json` (current revision), `vawt/history/NNNNNN.json` (every
saved revision), `vawt/last_meshed.json` (run ID, mesh status and
configuration of the last run that produced meshes). They are kept apart from
the generic workflow's `project.json`, which holds a different model.

Projects live in the WSL file system: default `~/vawt_projects`, or
`$VAWT_PROJECTS_DIR` (`vawt/workspace.py`). A project on a Windows drive
(`/mnt/<drive>`, file system 9p/drvfs per `/proc/mounts`) gets a WARNING
`PROJECT_ON_WINDOWS_DRIVE` on load, save, start and in the environment report.

Sub-cases per mode (method A, owner decision after V0; `vawt/case_generator.py`):

| Mode | Sub-cases, in run order | Final mesh |
|---|---|---|
| `AMI` (domain required) | `outer`, `rotor`, `merged` (mergeMeshes + createPatch) | `cases/merged` |
| `CELL_ZONE` with a domain | `merged` only: one snappyHexMesh pass, cylinder as a zoned surface | `cases/merged` |
| `CELL_ZONE` without a domain | `rotor` only (zone boundary is a plain patch) | `cases/rotor` |

Switching mode removes the generated dictionaries the new mode does not use
(only files this application generated).

---

## 6. Configuration Model

One frozen, validated model. Unknown keys rejected. No non-finite numbers.
Lengths in metres after unit conversion.

```text
VawtProjectConfig
├── schema_version
├── project_name
├── openfoam_profile
├── geometry                 reuse GeometryConfig; source_units required; patch_name default "rotor"
├── rotor
│   ├── axis                 x | y | z      (required; a suggestion is offered, never applied silently)
│   └── flow_axis            one of the two remaining axes (required)
├── rotating_zone
│   ├── centre_u, centre_v   in the plane normal to the axis
│   ├── axis_min, axis_max
│   ├── diameter
│   ├── interface            AMI | CELL_ZONE
│   ├── cell_size
│   └── location_in_mesh     inside the cylinder, outside the rotor solid
├── domain                   optional; absent = rotating zone only
│   ├── bounds
│   ├── cell_size
│   ├── patches              inlet, outlet, and four named sides
│   └── location_in_mesh     inside the domain, outside the cylinder
├── refinement
│   ├── blade_min_level, blade_max_level
│   ├── interface_level
│   ├── wake                 optional: box + level
│   ├── extract_features, feature_level, feature_angle_deg
├── layers
│   ├── enabled, count, expansion_ratio
│   ├── sizing               RELATIVE | ABSOLUTE
│   ├── final_layer_thickness, min_thickness       (RELATIVE)
│   └── first_layer_thickness, min_thickness_m     (ABSOLUTE)
├── snappy_quality           reuse SnappyQualityControls
├── quality                  reuse MeshQualityLimits (acceptance only)
├── max_global_cells
└── export
    └── fluent_msh
```

Rules:

- `interface = AMI` requires `domain`.
- Settings that are inactive (wake absent, layers disabled, features off) do not
  invalidate anything when changed.
- A field is added only when an operation consumes it. The old app's
  `num_blades` is not carried over until a consumer is identified.

---

## 7. Presets and Rotor Metrics

### 7.1 Rotor metrics (`vawt/rotor_metrics.py`)

Computed from the transformed artifact, in metres: bounding box, diameter `D`
(largest extent normal to the axis), span `H` (extent along the axis), and a
suggested axis. Output is a report. Nothing in it is applied without the user
confirming it.

### 7.2 Presets (`vawt/presets.py`)

Presets are data that produce a draft configuration from `D`, `H` and the axes.
They are starting values carried over from the old app. They are not
engineering recommendations and must not be presented as such.

| Quantity | Simple | Extended |
|---|---|---|
| Upstream / downstream of rotor centre | 3D / 7D | 6D / 8D |
| Lateral half-width | 1.5D | 3H |
| Along the axis | rotor ± 1.5D | centre ± 3H |

| Quantity | Default |
|---|---|
| Interface cylinder diameter | 1.5D |
| Cylinder extent along axis | rotor ± 0.05H |
| Wake box | 1.5D upstream, 8D downstream, ±2.5D lateral, 3H tall, clamped 5 % inside the domain |
| Domain cell size / rotating-zone cell size | D/9, D/22 |
| Levels: wake, interface, blade min–max | 1, 1, 1–2 |
| Layers | 3, ratio 1.2, relative, final 0.3, minimum 0.1 |
| Absolute first layer (when selected) | D/5000 |

Presets live in the backend. The UI requests a draft; it contains no preset
arithmetic.

---

## 8. Validation Before Meshing

Pure Python, no OpenFOAM, no file output beyond the report. Returns structured
issues. BLOCKING and ERROR stop the run.

| Check | Severity |
|---|---|
| Units not chosen | BLOCKING |
| Rotor axis or flow axis not confirmed | BLOCKING |
| Rotor bounding box not inside the cylinder | BLOCKING |
| Clearance between rotor sweep and cylinder below a configurable fraction of the zone cell size | WARNING |
| Cylinder not inside the domain | BLOCKING |
| Wake box not inside the domain | ERROR |
| Outer mesh point not inside the domain or inside the cylinder | BLOCKING |
| Inner mesh point not inside the cylinder | BLOCKING |
| Inner mesh point inside the rotor solid (checked when the surface is closed) | BLOCKING |
| Inner mesh point cannot be checked (open surface) | WARNING |
| Individual body inside-out | WARNING |
| Absolute first layer thicker than the finest blade cell | ERROR |
| `min_thickness` above the layer thickness it limits | ERROR |
| `AMI` without a domain | BLOCKING |
| Fewer than a configurable number of zone cells across `D` | WARNING |

Thresholds are configuration, not constants in the checks.

---

## 9. Meshing Workflow

### 9.1 Logical sequence

```text
Geometry:   import → units → transform → artifact → validate → rotor metrics
Outer:      dictionaries → blockMesh → snappyHexMesh                 (domain enabled)
Rotor:      dictionaries → blockMesh → feature extraction (if on) → snappyHexMesh
Assembly:   merge → interface patches → rotating cell zone           (method per section 10)
Check:      checkMesh → structured validation
Outputs:    previews, Fluent export
```

Method A (decided after V0, proven on v2512): `AMI` uses the two-mesh
sequence above, with the cell zone created by topoSet in the rotor sub-case
before merging; `CELL_ZONE` with a domain uses a single snappyHexMesh pass
with the cylinder as a zoned surface. In a single mesh the zone's cells can
only be the domain cell size / 2^n: the generator uses the smallest n whose
cells are no larger than `rotating_zone.cell_size` and reports the effective
size (INFO `ZONE_CELL_SIZE_ADJUSTED`); blade and feature levels count from it.
Each dictionary depends only on the settings of its own sub-case, which is
what makes the table below possible (tested for every configuration field).

Pipeline (V3, `vawt/pipeline.py`, operations and tables in
`vawt/operations.py`):

- Operations, in order: validate (gate: ERROR or BLOCKING stops before any
  OpenFOAM command), import geometry, generate dictionaries, outer mesh,
  rotor features, rotor mesh, single mesh, assembly, checkMesh, mesh
  validation. Only those of the configuration's layout run.
- The geometry artifact (ASCII, unchanged format) is written once and copied
  to every sub-case that reads it; a layout change copies it, never re-encodes.
- Commands run from their sub-case directory; logs go to `logs/<sub-case>/`.
- Result checks after each meshing operation: expected patches exist with
  faces (`MESHED_WRONG_REGION`), the rotating zone exists
  (`ROTATING_ZONE_MISSING`), the AMI pair has faces (`AMI_PATCH_EMPTY`); all
  ERROR with a log reference. A failed check leaves no manifest record (the
  previous one is removed before the operation runs).
- Region rule: an AMI mesh must have exactly 2 regions (V0 E1: the outer and
  rotor meshes, 487,630 + 121,296 cells), every other layout exactly 1;
  anything else is INVALID (`REGION_COUNT_UNEXPECTED`). Every other failed
  check still makes the mesh INVALID.
- Resource preflight (`vawt/preflight.py`): arithmetic only; RAM follows the
  larger of the outer and rotor meshes (they run one after the other).
- `.preprocessor/status.json` is written atomically at every stage boundary
  (run ID, stage, step n of m, state, times).
- A test checks, for every configuration field, that the operations the
  tables plan are exactly the ones that re-run.

### 9.2 Operations are cached independently

Outer and rotor meshes do not depend on each other. This is the main source of
saved time on repeat runs.

| Changed setting | Re-runs | Reused |
|---|---|---|
| Wake box or wake level | outer mesh, assembly, check, outputs | rotor mesh |
| Blade levels or layers | rotor mesh, assembly, check, outputs | outer mesh |
| Domain bounds or patch names | outer mesh, assembly, check, outputs | rotor mesh |
| Cylinder geometry | both meshes and everything after | geometry |
| Geometry transform or units | everything after import | — |
| Acceptance limits (`quality`) | validation only | all meshes |
| Export flag | export only | everything else |
| Project name | nothing | everything |

Every configuration field has an entry in the dependency table. A test fails
when a field is added without one.

Cache reuse follows the existing rule: recorded inputs match, every recorded
output exists with the same hash, same OpenFOAM tool identity.

---

## 10. OpenFOAM Method — Verification Required

Nothing in this section is established fact. The old app's stage names suggest
this sequence, and it is the first candidate:

```text
blockMesh (outer) → snappyHexMesh (outer)
blockMesh (rotor) → surfaceFeatureExtract → snappyHexMesh (rotor)
mergeMeshes → createPatch (cyclicAMI pair) → topoSet (cell zone)
checkMesh → foamMeshToFluent
```

An alternative to evaluate is a single snappyHexMesh pass with the cylinder as
a zoned surface, followed by interface creation.

V0 must settle, on the target installation, with captured evidence:

1. Target OpenFOAM version. The old app's environment was openfoam.com v2412.
   Settled in V0: OpenFOAM v2512 (section 19).
2. Which method produces a valid interface, and its exact dictionaries.
3. How the outer mesh excludes the cylinder volume.
4. `CELL_ZONE` mode: how the zone is created in a single mesh.
5. Absolute layer sizing: the entries required with `relativeSizes false`.
6. Exit codes and output of each command on success and on a typical failure.
7. Where `foamMeshToFluent` writes, and whether the interface patches and cell
   zone survive in the `.msh`.
8. Whether `surfaceCheck` adds information the Python validator lacks.

Evidence is stored as fixtures: the dictionaries that worked, and the logs of
every command. Generator tests compare against them.

If the old backend (`~/vawt-mesh-app/backend`) is available, its dictionaries
are admissible evidence of what ran before. They still need one confirming run.

---

## 11. Resource Preflight

Same statuses and rules as the existing preflight: SAFE, WARNING,
HIGH RESOURCE RISK, BLOCKED; heuristic, never presented as exact.

The estimate adds: both background meshes, the wake region volume at its level,
the interface band, the blade surface band, and layers. Peak memory is the
larger of the two meshing steps when they run in sequence, and their sum when
they run together.

---

## 12. Service and Background Execution

### 12.1 `VawtService`

The only interface the UI calls. Returns plain data and structured issues. No
Streamlit types cross this boundary, so the UI can be replaced later.

```text
load / validate / save / draft_from_preset
store_source_file
rotor_metrics
section_status            → per section: EMPTY | INCOMPLETE | READY | STALE | ERROR
plan                      → which operations will run and why
preflight
start_run / cancel_run / run_status
reports / runs / log_tail
preview                   → paths to display artifacts
export_files
```

Section status is computed here, not in the UI.

### 12.2 Runs do not block the UI

This removes the known limitation of the existing dashboard (synchronous run
behind a spinner, logs only afterwards).

- `start_run` hands the pipeline to one in-process worker and returns at once.
- The pipeline writes `.preprocessor/status.json` at each stage boundary:
  run ID, stage, step index of total, state, start time. Written atomically.
- `run_status` reads that file. `log_tail` reads the last bytes of the active log.
- `cancel_run` sets the runner's existing cancel event.
- The run lock still allows one run per project. A page refresh or a second
  browser tab re-attaches by reading the status file; it never starts a second run.
- A worker that dies leaves a stale lock; the existing stale-lock rule clears it.
  The status file is then reported as an interrupted run, not as running.

Implemented in V4 (`vawt/service.py`, `vawt/runtime.py`):

- The app runs inside WSL, where OpenFOAM is sourced; the browser on Windows
  opens it on `localhost`. The server binds `127.0.0.1` (not the network); WSL
  forwards Windows `localhost` to it (verified, `vawt_method_notes.md` §12).
- `run_status` combines the status file, the run lock and the process's run
  registry: `RUNNING` (this process; can cancel), `RUNNING_ELSEWHERE` (a live
  lock held by another process; cannot cancel from here), `INTERRUPTED` (the
  status says running but no live run holds the lock), or the last terminal
  state. `start_run` during an active run returns that run's ID
  (`RUN_ALREADY_ACTIVE`); it never starts a second run.
- The lock records the holder's process start time, so a PID reused after a
  WSL restart does not keep a dead run's lock alive.
- The status file names the running command, its log and, once started, its
  PID and start time. After an interrupted run, a command still running with
  that PID and start time is reported as `ORPHANED_OPENFOAM_PROCESS`
  (BLOCKING); `terminate_orphan` stops it after confirmation.
- After an interruption the interrupted operation re-runs (its record was
  removed before it started); earlier operations are reused only if their
  files verify. The next run's record names the interrupted run.
- On a normal exit of the app every active run is cancelled, so its OpenFOAM
  command is terminated and the status ends as `CANCELLED`.
- `section_status`: EMPTY / INCOMPLETE / READY / STALE / ERROR per setup
  section; STALE names the settings that changed since the last mesh and make
  a mesh operation stale (acceptance limits do not).
- `plan` comes from the dependency tables (from scratch before the first
  mesh); the run re-verifies cached files itself.
- `log_tail` reads at most 64 KiB from the end of the active log by seek.
- `preview` (V6) and `export_files` (V7) are not implemented yet.

---

## 13. UI Specification (Streamlit Prototype)

### 13.1 Layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Header: project name · saved / unsaved changes · OpenFOAM environment · run   │
├───────────────┬──────────────────────────────────────┬───────────────────────┤
│ Navigation    │ Action bar: Apply · Revert · Save    │                       │
│ sections with │──────────────────────────────────────│   3D preview          │
│ status marks  │ Section content in titled groups     │   (stays in place)    │
│               │ Advanced settings collapsed          │                       │
├───────────────┴──────────────────────────────────────┴───────────────────────┤
│ Status strip: stage · step n of m · elapsed · issue count · Cancel            │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 13.2 Sections

| Group | Section | Purpose |
|---|---|---|
| Setup | Project | Name, folder, OpenFOAM profile, environment check |
| Setup | Geometry | Upload, units, transform, validation issues, rotor metrics |
| Setup | Rotating zone | Axes, cylinder, interface type, cell size, mesh point |
| Setup | Domain | On/off, preset, bounds, patch names, cell size, mesh point |
| Setup | Refinement | Blade, interface, wake, features |
| Setup | Boundary layers | Count, ratio, sizing method |
| Run | Review | All issues, what will run and why, estimated cells, resource status |
| Run | Run | Start, cancel, stage list, live log tail |
| Results | Mesh | Validity and quality (separate), metrics against limits, 3D by patch, slices |
| Results | Export | `.msh`, case archive, generated dictionaries |
| Tools | History | Run records |
| Tools | Logs | Log files |

### 13.3 Behaviour

- Navigation shows each section's status from `section_status`.
- Edits go to a draft. **Apply** validates the section. **Save** writes a revision.
- Run actions use the saved configuration only.
- A stale result is labelled stale, with the setting that caused it.
- Issues appear in the section they belong to and all together in Review.
- HIGH RESOURCE RISK needs an explicit confirmation. BLOCKED cannot be overridden.
- Mesh validity, mesh quality, simulation suitability and CFD accuracy are
  shown separately; the last two are always NOT_ASSESSED.
- Advanced users can view generated dictionaries and raw logs.

---

## 14. Performance Requirements

### 14.1 Mechanisms (all required)

| # | Mechanism | Removes |
|---|---|---|
| 1 | Section navigation where only the active section's code runs. Not one `st.tabs` strip: the current dashboard builds all 11 tab bodies on every interaction. | Work for hidden sections |
| 2 | One form per section; inputs commit on Apply. | A rerun per keystroke |
| 3 | 3D preview, status strip and log tail as fragments. Timed refresh only while a run is active. | Full-page reruns for local updates |
| 4 | Parsed geometry and metrics cached by content hash plus transform. Services cached as resources. | Re-parsing the STL on every rerun |
| 5 | Display artifacts built once per mesh hash, decimated, stored in a compact binary form under `previews/`. | Parsing ASCII polyMesh in the UI |
| 6 | Background worker with status file (section 12.2). | A blocked UI during meshing |
| 7 | Log tail by seek, bounded size. | Reading whole logs |
| 8 | Independent caching of outer and rotor meshes (section 9.2). | Re-meshing unchanged regions |
| 9 | Cell estimates as arithmetic on the draft; no file access. | Latency on live feedback |
| 10 | `plotly` and `trimesh` imported only where used. | Start-up time |

Optional, after V7 and only if preflight allows the combined memory: run the
outer and rotor meshing steps at the same time.

### 14.2 Budgets

These are targets. V0 measures the current dashboard as a baseline; V7 measures
the result. Revise a target only with a recorded measurement.

| Interaction | Target |
|---|---|
| Apply on a setup section, 100,000-triangle rotor loaded | under 200 ms of server time |
| Switching section | no geometry or mesh file read |
| Upload to metrics shown | one parse of the STL |
| Start run to first status shown | under 1 s |
| Status and log refresh during a run | every 1–2 s, bounded bytes read |
| Run with nothing changed | no OpenFOAM command starts |
| 3D view of a finished mesh, second time | loads the stored display artifact only |

Each row has a test (section 17). Timing tests run with a generous margin and
assert on work done (parses, file reads, commands) wherever possible, not on
wall-clock time alone.

---

## 15. Visualization

- Geometry: source converted to metres, transformed artifact, cylinder, domain
  box, wake box, both mesh points.
- Mesh: boundary surfaces coloured per patch with show/hide, sampled
  deterministically above a face cap, with a visible note when sampled.
- Slices: one plane through the axis along the flow, one normal to the axis at
  mid-span.
- Quality: measured values against acceptance limits; checkMesh problem sets at
  their locations.

The display transform that makes the rotor axis vertical never touches mesh
coordinates. Views are built on request and never required by the pipeline.

---

## 16. Export

- Fluent `.msh`, produced by a cached operation after a valid check.
- OpenFOAM case archive of the final case.
- Each export records the mesh hash it came from. A stale export is not offered
  as current.
- A mesh with validity failures can be exported only with an explicit
  confirmation, and the export record notes it.

---

## 17. Testing

Follows `docs/testing_strategy.md`. Additional requirements:

| Area | Must cover |
|---|---|
| Fixtures | A rotor generator taking an output path: outward-wound bodies, a ×100 copy for units, a binary file with a `solid` header, a copy with one body inside-out |
| Configuration | Every rule in section 6; unknown keys; schema migration |
| Rotor metrics | Exact `D`, `H`, axis for the fixture in each orientation |
| Validation | Every row of section 8, pass and fail |
| Presets | Values in section 7.2; wake box clamping |
| Case generation | Paths, required keys, references, patch names, determinism, comparison with V0 fixtures |
| Dependencies | Every row of section 9.2, including what is **not** re-run; every field mapped |
| Cache | Hit, miss, missing output, corrupted output, tool identity change |
| Pipeline (fake OpenFOAM) | Order, stop on failure, each failing stage, timeout, cancel, status file contents |
| Service | Section status transitions; re-attach to a running run; interrupted run |
| UI | Each section renders; no uncaught exception on any failure path; only the active section executes |
| Performance | Each row of section 14.2 |
| Real OpenFOAM | End-to-end fixture rotor in both interface modes; export produced; marker `openfoam` |

Existing tests are never edited to make new work pass.

---

## 18. Milestones

Stop after each. Do not start the next without an explicit instruction.

| | Milestone | Delivers |
|---|---|---|
| V0 | Method proof and baseline | Section 10 answers with fixtures; baseline timings; decisions from section 19; `docs/vawt_method_notes.md`. No application code. |
| V1 | Configuration and analysis | `config`, `presets`, `rotor_metrics`, `validation`; integration-point decisions; fixtures. No OpenFOAM. |
| V2 | Case generation | Deterministic dictionaries for every sub-case, tested against V0 fixtures. |
| V3 | Pipeline | Operations, dependency tables, cache, command builders, preflight, status file; fake-OpenFOAM tests; one real end-to-end test. |
| V4 | Service and background runs | `VawtService`, worker, cancel, re-attach. |
| V5 | UI | Shell, sections, forms, fragments. |
| V6 | Visualization | Display artifacts, patch view, slices, quality view. |
| V7 | Export and verification | Export, performance measurements against section 14.2, documentation updates. |

---

## 19. Decisions for the Owner

1. Target OpenFOAM version and where it runs (native Linux, WSL).
2. Is `CELL_ZONE` mode needed in the first release, or AMI only?
3. Is Fluent `.msh` the primary deliverable, or the OpenFOAM case?
4. Flow direction convention: inlet on the minimum face of the flow axis?
5. Should the VAWT app be a separate entry point (`app/vawt_app.py`, assumed
   here) or a mode inside the existing dashboard?
6. Largest rotor STL and cell count expected. This sets the display cap and
   whether integration point 7 matters.

Confirmed by the owner during V1:

- Decision 1 (profile): OpenCFD (openfoam.com) is the default profile. The
  exact version and where it runs are still settled in V0.
- Decision 2 (`CELL_ZONE`): stays in the configuration, but is not exposed in
  the UI until a real OpenFOAM run has proven it.
- Decision 4 (flow direction): inlet on the minimum face of the flow axis.

Confirmed by the owner during V0:

- Decision 1 (version and platform): **OpenFOAM v2512** (openfoam.com /
  OpenCFD, package `openfoam2512`), running in WSL2 Ubuntu 24.04. All V0
  evidence was captured on this version (`docs/vawt_method_notes.md`). Other
  versions, including the old app's v2412, are not verified.

Decision 5 is reflected in `docs/architecture.md` §3 and §18 (separate entry
point `app/vawt_app.py`). Decisions 3 and 6 remain open (see
`docs/vawt_method_notes.md` section 8).

---

## 20. UI Framework After the Prototype

Streamlit is the right choice for the prototype: fastest to build, and
section 14.1 removes most of its latency. Its limits are structural: the script
reruns on interaction, panels cannot be docked or resized freely, and 3D is
limited to what a chart component can draw.

Because the UI calls only `VawtService`, it can be replaced without touching
the engine.

| Option | Strength | Cost |
|---|---|---|
| FastAPI + React + three.js | Full control of layout and interaction; the most product-like result; the old app's 3D viewer can be reused | Two languages; an API layer to build and test |
| NiceGUI | Python only; event-driven, no full rerun; built-in 3D scene | Smaller ecosystem; less layout polish than React |
| trame + PyVista | Strongest mesh inspection: large meshes, slicing, colouring by quality | Heavier dependency; more a viewer than an application framework |
| PySide6 + PyVista (desktop) | Fastest for one local user; native feel | No browser access; packaging per platform |

Suggested path: Streamlit prototype, then FastAPI + React if this becomes a
product for other users; trame + PyVista instead if inspecting large meshes
matters more than interface polish. Re-check each option's current
capabilities before committing.
