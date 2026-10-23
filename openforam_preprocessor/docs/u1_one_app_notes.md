# U1 "one app": notes

Milestone U1 makes the machines app the one app: a three-step new project
(case type, mesh source, domain), a blade/airfoil test profile, parity with
`app/vawt_app.py`, old projects, and the old app retired (port 8502). Branch
`u1-one-app`, from main after g9-msh-import was merged (233e27d).

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
- CAD import (STEP/IGES), a mesh-independence study, a blade generator from
  tables, Fluent export (V7), G8 VAWT with pole.
- The heavy lock for STL runs (docs/msh_import_notes.md 13.6).

Order (owner): U1-1 (this axis fix) is merged first; then M2b (multi-part
machine from several mesh files, branch m2b-assembly); then the rest of U1
on u1-one-app rebased onto main.
