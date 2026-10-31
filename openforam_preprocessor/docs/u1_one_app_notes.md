# U1 "one app": notes

Milestone U1 makes the machines app the one app: a three-step new project
(case type, mesh source, domain), a blade/airfoil test profile, parity with
`app/vawt_app.py`, old projects, and the old app retired (port 8502). Branch
`u1-one-app`, from main after g9-msh-import was merged (233e27d).

## 0. Order after C1 (owner, 2026-10-29)

Wrapping up soon does not cut scope: U1 is done in full. The order:

1. **U1 in full:** one app (the machines app, port 8502, every case type,
   VAWT included: D1, the general pipeline with the VAWT zone settings and
   the wake box, P2); the three-step new project (case type, mesh source,
   domain); .msh and "several .msh files" as mesh sources for every case
   type, not only Francis and Custom; the "Foil / object test (stationary)"
   (airfoil, hydrofoil, any object; air or water; D5); old projects; the old
   app retired (`app/vawt_app.py`, port 8501; its code and tests stay until
   the owner approves their removal).
   **C1b (owner, 2026-11-01), after U1 and before item 2 ships:** the outer
   convergence study (docs/c1_startup_notes.md 3.4): more outer iterations
   and/or a realistic residualControl target, until the 1 deg and 2 deg
   torques agree within a stated tolerance over several pitches.
2. **Run the case from the app:** case/Allrun in the background with its
   progress, cancel and log; no terminal for the user (section 6, 1).
   Scope change approved (owner, 2026-11-01; CLAUDE.md section 1). Until C1b
   passes, its results are labelled "not converged per step".
3. **Cluster-ready case** for the full machine: decomposePar, a run script,
   a README (section 6, 2).
4. **Mixed formats in one machine:** some parts STL, some .msh (for example
   the runner from STL with the casing and draft tube from .msh), meshed and
   assembled together, with the same ask-before-pairing interface flow as
   M2b.
5. **Mesh upgrades** milestone (section 7, as recorded).
6. Then: G8 VAWT with pole; the HAWT interface evidence (section 5); the
   runner inlet from a steady SC + GV run (section 6, 3).

Each item: evidence first on v2512, the STL byte-identity rule, tests, the
testing-agent review, a browser checklist with the app named, STOP for the
owner's merge.

## 1. Decisions (owner)

- **D1, meshing method (after E1 and its evidence step, section 3):** VAWT STL
  projects mesh in the general pipeline. "mesh_method VAWT" is a named set of
  zone settings there: no rim feature snapping at the interface cylinder, and
  a zone background grid centred on the zone with cells of exactly the zone
  cell size. On the E1 rotor that gives the old method's mesh point for point.
  The wake box (P2) is added to the general pipeline. HAWT keeps today's
  settings. The old pipeline is no longer used for new projects; its code and
  tests stay until the owner approves their removal (as for the old app).
- **Axis (U1-1):** the axis point defaults to the area-weighted centroid of the
  rotor surface; D is the swept diameter about it. Both are shown on the rotor
  page and in the 3D pane and are applied only by the preset button (section 4).
- D2 tunnel options, D3 transient on a static mesh, D4 test changes, port
  8502: as in the U1 plan.
- **D5, .msh import and the stationary test (owner, 2026-10-27):** .msh import
  is confirmed for every case type. The blade/airfoil test becomes "Foil /
  object test (stationary)": any object with no rotating zone (airfoil,
  hydrofoil, bluff body), air or water presets, STL or .msh, the domain sized
  from the object.

## 2. E1: one rotor, both methods (v2512)

Rotor: 3 NACA0018 blades, chord 0.1 m, R 0.5 m, span 1 m, axis z
(`tests/fixtures/vawt/three_blade.py`). Both presets centred the zone on the
bounding-box centre (section 4), the same zone for both methods, so the
comparison holds; the rotation was about that point. Scripts and logs:
`/root/u1_e1` in WSL.

| | Old method, no wake | General method (today) | Old method, wake box |
|---|---|---|---|
| Cells | 144,152 | 144,422 | 486,949 |
| Max non-orthogonality, rotating / rest | 40.1 / 39.0 deg | 49.4 / 39.3 deg | 40.1 / 25.4 deg |
| Max skewness | 1.14 | 2.38 | 1.14 |
| Max aspect ratio, rotating zone | 3.1 | 8.1 | 3.1 |
| Min interpolation weight, rotating | 0.28 | 0.13 | 0.28 |
| AMI sum(weights) at rest | 0.964-1.29 | 0.948-1.508 (WARNING) | 0.964-1.29 |
| Over one revolution (36 x 10 deg) | 0.957-1.33 | 0.883-1.511 | 0.957-1.33 |
| 13 pimpleFoam steps | clean, Co 2.55 | clean, Co 2.57 | clean, Co 2.55 |

384 cylinder segments instead of 96 changed nothing (0.949-1.506 at rest).

## 3. The evidence step: the general method meshed the old way

Two causes, found by changing one thing at a time on the general method's
own cases:

1. Explicit feature snapping onto the interface cylinder's rims
   (`explicitFeatureSnap true` with the cylinder's .eMesh, both sides). Without
   it (faceted STL or exact searchableCylinder alike): AMI 0.978-1.31 at rest,
   0.967-1.35 over a revolution, non-orthogonality 38.9 deg, skewness 1.15.
2. The zone's background grid: fitted to the bounds plus a margin (cells just
   below the zone cell size) leaves slivers under the zone's end caps (aspect
   8.3, weight 0.106 at z = +-0.548, caps at +-0.55). The old rule
   (`vawt.case_generator.rotor_grid`: centred on the zone, exactly the zone
   cell size, 2 cells of margin) gives the old mesh: 144,152 cells, the same
   points file as the old method, AMI 0.964-1.292 / 0.957-1.332.

On a 3-blade HAWT rotor (G0 geometry, axis x) the same settings did not reach
the target: the HAWT zone is a short disc whose caps are much of the
interface.

| HAWT | General (today) | Old way |
|---|---|---|
| Non-orthogonality, rotating | 47.9 deg | 42.7 deg |
| Aspect ratio | 4.56 | 4.52 |
| Min interpolation weight | 0.11 | 0.19 |
| AMI at rest | 0.925-1.011 | 0.802-1.373 |
| Over one revolution | 0.820-1.015 | 0.791-1.470 |

So: VAWT zones take the old settings, HAWT keeps today's (section 1). The
HAWT preset already dips below 0.85 over a revolution: future list.

## 4. U1-1: the rotor axis point and D

The VAWT preset (both apps; `vawt/presets.py`, reused by
`machines/presets/vawt.py`) centred the rotating zone, the domain and the
rotation on the rotor's bounding-box centre, and took D as the largest extent
normal to the axis. For an odd blade count the box centre is off the axis: on
the E1 rotor by 0.098 m, so the blades would swing between 0.394 and 0.579 m
from the rotation point; and D was 0.918 m instead of the swept 1.018 m, so
the zone, the domain and the cell sizes were about 10% small.

Audit of every preset:

| Preset | Axis point | Affected |
|---|---|---|
| VAWT (both apps) | bounding-box centre | yes: fixed |
| HAWT (`hawt_rotor`) | area-weighted centroid (since G4) | no |
| Francis | the user's origin (0,0,0 until entered) | no |
| Custom, imported meshes | the user's entry; nothing suggested | no |

Earlier evidence: E1 used the box centre (comparison valid, section 2); the
V0-V2 and G0 VAWT evidence and the VAWT tests use the symmetric 4-blade
fixture, whose box centre is on the axis (unaffected, and unable to catch
this); G0's 3-blade HAWT used the centroid; the M2a real tests ran the owner's
meshes with the test default (x through the origin), but none of them has a
cylindrical rotating zone (GV: seven passage zones; SC: one default_zone) and
the solver test was static. The old VAWT projects have no saved
configuration.

Now:

- `vawt.rotor_metrics.compute_rotor_metrics(vertices, axis, faces=...,
  centre_uv=...)`: for a confirmed axis, `axis_point` (the centroid of the
  surface faces, or centre_uv) and D and the sweep radius about it. Every app
  caller passes the faces. `centre` stays the box centre, for reports. One
  centroid function (`surface_centroid`) for VAWT and HAWT.
- The VAWT preset centres everything on `axis_point`, with the swept D.
- The rotor page shows the axis point ("the surface centroid" or "entered"),
  the swept D about it and, when apart, how far the box centre is. The point
  can be entered; "Use the surface centroid" goes back. The 3D pane has a
  "Rotor axis" view (the rotor, the axis through the point, the swept circle,
  the box centre), shown in Setup too while the draft cannot be drawn. Only
  the preset button applies the point (`axis_point` in the preset inputs, VAWT
  and HAWT).
- Saved projects are unchanged: their zone values are in their configuration.
- The measured radius offered for a tip-speed ratio (Rotation) is labelled
  "outer swept radius (blade corners)", with the caption that a tip-speed
  ratio usually uses the blade mounting radius, which may be slightly smaller
  (owner).
- Tests: `tests/unit/machines/test_u1_axis.py` (the true axis put at
  (0.3, -0.2); every test fails with the old measure patched back in),
  `tests/unit/ui_machines/test_u1_axis_app.py`.
- Approved test changes (D is now the swept diameter: on the symmetric 4-blade
  fixture 1.0591 m, 2 sqrt(0.52^2 + 0.1^2), instead of its extent 1.04 m):
  the fixture gains `RotorSpec.swept_diameter`; test_presets (8),
  test_rotor_metrics (5), test_v2_validation (2), test_vawt_case_generator (1)
  and test_vawt_validation (1) expect the swept D; the V0 dictionary comparison
  builds its config with V0's inputs (box centre, D 1.04); the G4 three-blade
  test compares the box extent, not the box measure's D, with the swept D.

## 5. Future list (U1)

Recorded only; not built in U1.

- HAWT interface evidence: today's preset dips to a minimum sum(weights) of
  0.82 over a revolution. Try a longer zone along the axis and other end-cap
  treatments; target: minimum sum(weights) at or above 0.9.
- Measure STL meshes against the two-level checkMesh rule, and tighten the
  0.85-1.5 AMI range with evidence.
- Blade test: a steady-state option (D3).
- "Make a cellZone from region N" helper (topoSet + setsToZones).
- Quicker studies of big machines: runner and draft tube only, a single
  passage.
- trame for the 3D pane if plotly is not enough.
- A blade generator from tables, G8 VAWT with pole. (CAD import, the
  mesh-independence study and Fluent export moved to section 7.)
- The heavy lock for STL runs (docs/msh_import_notes.md 13.6).
- Flow arrow for imported meshes (owner, after M2b): the 3D pane draws no
  flow direction for an imported mesh (it reads the setup scene, which an
  imported mesh does not have); take it from the inlet patches of the mesh.
- "Open in ParaView" (owner, 2026-10-27): ParaView 6.2.0 is installed
  (`C:\Program Files\ParaView 6.2.0\bin\paraview.exe`). The app finds
  paraview.exe itself (Program Files, the Start-menu shortcut's target, PATH),
  saves the path in the app settings, and offers a "locate ParaView" picker
  when it is not found.

## 6. After U1: details (owner, after C1; the order is section 0)

1. Run the case from the app (owner, 2026-10-29): start case/Allrun in the
   background with its progress (steps, hours left, the outer-loop record),
   cancel, and the log, so no terminal is needed. C1 writes the Allrun; the
   app today only prepares the case (CLAUDE.md scope: needs the owner's
   approval of the scope change).
2. Prepare a case to run on a cluster (owner, 2026-10-27, M2b answer 1;
   moved here after C1): decomposePar, a run script, a README. C1 already
   writes an imported machine's decomposeParDict and Allrun (run order).
3. Evidence: the swirl inlet against the guide vanes' own outflow. A steady
   MRF/simpleFoam run of SC + GV (about 11 M cells: check it fits WSL first),
   its outlet profile mapped onto the runner inlet; compare the Euler head
   with the approximate swirl's 6.1 m (12.29 m net at Francis-99 PL;
   docs/c1_startup_notes.md 1).

## 7. Next milestone after U1: mesh upgrades

Recorded only (owner, 2026-10-27); planned after U1, with evidence first.

a) Airfoil/hydrofoil generator: NACA 4-digit or a Selig .dat coordinate file
   to a 2D or extruded C-grid (Gmsh or blockMesh), with layers sized from a
   target y+.
b) Boundary-layer coverage report per patch from snappyHexMesh, with a
   warning and suggested fixes.
c) CAD import (STEP/IGES) via Gmsh.
d) Mesh-independence study: 3 meshes at a fixed ratio, GCI.
e) Export to Fluent .msh (foamMeshToFluent, the V7 item).
f) cfMesh as a second mesher, if it builds for v2512.

Order (owner): U1-1 (this axis fix) is merged first; then M2b (multi-part
machine from several mesh files, branch m2b-assembly); then the rest of U1
on u1-one-app rebased onto main.

## 8. U1 in full: plan (2026-11-01, for the owner's review)

Branch `u1-full` from main 5662b02 (`u1-one-app` holds only U1-1, merged).
Done so far: U1-1 (axis point and D, 1425d63), E1 and its evidence step
(sections 2-3), D1 decided. Not built yet: everything below. Each step:
evidence on v2512 first, existing STL projects mesh byte-identically, tests,
exact counts, the testing-agent review, a browser checklist in the machines
app (http://localhost:8502), STOP.

### U1-2 VAWT in the general pipeline (D1, P2-P9)

- A zone setting `mesh_method` on the rotating zone: GENERAL (today's
  settings, the default, so every saved project meshes byte-identically) or
  VAWT (no rim feature snapping at the interface cylinder; the zone's
  background grid centred on the zone, cells of exactly the zone cell size, 2
  cells of margin, as `vawt.case_generator.rotor_grid`). The VAWT preset sets
  VAWT; HAWT keeps GENERAL.
- P2 wake box in the general pipeline (a refinement box behind the rotor,
  corners and level, editable in Domain); USE_VAWT_WORKFLOW and the
  VAWT_ONLY_SETTING refusal of the wake are removed for it.
- P3 EXTENDED preset beside SIMPLE; P5 `fluent_msh: true` kept with an INFO
  (export comes with mesh upgrades, section 7e); P6 "cell size used in the
  mesh"; P7 the plan's reasons in Review; P8 rotor span H; P9 the caption.
- P4 rotor-only and CELL_ZONE: still not meshed (no workflow builds them
  once the old one is retired); refused with a "Switch to AMI" button
  instead of "use the VAWT workflow".
- Evidence E1b: the E1 rotor through the general pipeline with VAWT
  settings gives the old method's mesh point for point (section 3, now as
  app code); with the wake box: cells, checkMesh per zone, AMI at rest and
  over a revolution, 13 pimpleFoam steps.

### U1-3 Three-step new project; .msh and several .msh for every type

- New project: 1 case type (VAWT, HAWT, Francis, Foil / object test,
  Custom; one line each; the landing radio key kept), 2 mesh source (Build
  from STL, Import .msh, Several .msh files; for every type), 3 domain (the
  suggested domain per type, editable, with its reason; "the mesh is the
  domain" for .msh).
- MSH_MACHINE_NOT_SUPPORTED and NO_IMPORT are removed for VAWT.
- Evidence E2: a Gmsh and a Fluent 3-blade VAWT .msh (single file, and
  rotor + domain as two .msh with the M2b ask-before-pairing flow), as VAWT
  and as HAWT, through AMI checks, case setup and a few solver steps (tests
  only).

### U1-4 Foil / object test (stationary) (D2, D3, D5)

- A new case type: any object with no rotating zone (airfoil, hydrofoil,
  bluff body), STL or .msh, air or water presets, the domain sized from the
  object (5c upstream, 10c downstream, +-5c on the lift axis: starting
  values). Chord and span measured along confirmed axes, applied by a button.
  Angle of attack turns the inflow. Time in flow-throughs (c/U), transient on
  a static mesh (D3). Case setup gains the zero-rotating-zone case.
- Evidence E3 (D2): an extruded NACA0012 at 5 deg, tunnel (a) inlet/outlet
  faces against (b) FAR_FIELD (freestream), compared by the flow angle one
  chord ahead; FAR_FIELD is added only if (b) wins. A 2D option (one cell
  thick, empty sides) against 3D by run time. A .msh foil.

### U1-5 Old projects

- The folders in `~/vawt_projects` are listed (none has a saved
  configuration) with "Start a new VAWT project from this STL"; the original
  is never written (D1: E4 skipped).

### U1-6 Retire the old app

- `run_vawt_app.*`, the launcher and the desktop shortcut open the machines
  app on 8502. `app/vawt_app.py`, the old pipeline and their tests stay,
  unused, until the owner approves removal. Notes that name 8501 updated
  (README, docs/msh_import_notes.md, script headers, the vawt_app docstring).

### Tests that would change (D4 approved; each listed in its commit)

`test_m1_config_validation::test_a_vawt_project_cannot_import_a_mesh`,
`test_m1c_core::test_a_vawt_project_is_told_to_use_a_custom_project`,
`test_g7_service::test_vawt_preset_leaves_out_what_only_the_vawt_app_meshes`,
`test_machine_field_coverage` (declarations), `test_upload_notes` only if
needed. New this time, for approval: the tests that assert USE_VAWT_WORKFLOW
for a wake box, and the schema-version tests if the schema goes to 5.

### Open for the owner

1. Schema 5 for `mesh_method`, the wake in the general pipeline and the
   foil type (as for 3 and 4), or optional fields under 4?
2. Order: U1-2 to U1-6 as above, one STOP after each, or one STOP at the
   end of U1?
3. "Not converged per step": also shown now on the Case page for imported
   machines (the solve estimate), or only when run-from-app ships?
