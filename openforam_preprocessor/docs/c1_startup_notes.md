# C1: start-up and time stepping for real machines (plan and evidence)

Milestone C1 (owner, after M2b): how a real machine's case starts and steps,
measured on the owner's runner + draft tube before anything is built, and
offered in the app as labelled choices with the hours a revolution. Plan
first; this file is the plan and its evidence. Branch `c1-startup`.

## 1. The operating point: Francis-99 BEP (NTNU)

The four files are the Francis-99 model turbine (NTNU): 28 guide vanes, 15
blades + 15 splitters, runner reference diameter 0.349 m (the app measured a
0.350 m runner outlet). The published best efficiency point, labelled
"Francis-99 BEP (NTNU)" here and in the case: N = 335.4 rpm, Q = 0.203 m3/s,
net head 11.91 m, guide vane angle 9.84 deg. Part load: 406.2 rpm / 0.071
m3/s; high load: 369.6 rpm / 0.221 m3/s.

- Runner speed: 335.4 rpm = 35.12 rad/s, a revolution 0.179 s; 1 deg a step
  is 0.497 ms.
- Direction: clockwise about +z, from the guide vanes in the mesh (their
  chord turns the flow towards -theta). Checked to agree (owner's ask): the
  runner turns at omega -35.12 rad/s about +z (clockwise), the evidence inlet
  swirls clockwise: cylindricalInletVelocity's rpm on a test ring (v2512):
  rpm -100 gave -20.54 m/s towards -theta (exactly omega r), so rpm -295.3
  is clockwise; and the guide vanes turn the flow clockwise. A validation
  check (a WARNING when the runner turns against the inlet swirl) comes with
  the swirl inlet choice (section 4): today's flow-rate inlet has no swirl to
  compare with.
- Guide vane angle, its definition: Francis-99 measures it from the fully
  closed position ("0 deg is fully closed"; the shutdown runs from 9.84 deg,
  BEP, to 0.8 deg: Chalmers' LES of the Francis-99 transients, OpenFOAM
  Workshop 13 paper OFW13-23-126). Not from the tangential direction.
- Guide vane opening in the mesh, in both conventions (GV passage):
  - from the tangential direction: the chord (0.089 m) lies 15.5 deg from it
    at the trailing edge (r 0.333 m), 22.6 deg at mid-chord;
  - from closed: the vane turned about its pivot (fitted to the round stem
    footprint, FaceGroup37/38: r 0.3716 m from the axis, stem radius 6.6 mm),
    all 28 together, touches its neighbour after 3.6-3.9 deg (the full
    outline; 3.6-4.7 deg at mid-height only): an opening of about 3.6-4.7 deg
    from closed, with 4.1-5.3 mm between neighbours now.
  Neither is 9.84 deg. Not called a mismatch: the result rests on the pivot
  fitted from the stem and on "closed" as the vanes touching; the vane's
  outline also differs by up to 19 mm between the cover and mid-height (its
  shaped ends). The Francis-99 guide vane drawing (pivot, closed angle) would
  settle it: asked of the owner. Owner (after C1 review): no drawing needed;
  the opening is recorded as "confirmed by file name GV-03-91 and
  measurement 3.6-4.7 deg" (the correction below).
- As a flow angle at the runner inlet, 9.84 deg gives an Euler head
  of 11.3 m (u Vt / g with u = 11.23 m/s at r 0.32 m, Vt = Vr / tan 9.84 deg
  = 9.90 m/s, Vr = Q / A = 1.716 m/s over the 0.1183 m2 inflow ring) against
  the 11.91 m net head: consistent with an efficiency near 0.93.
- Runner + draft tube inlet: without the guide vanes the app's flow-rate
  inlet is normal to the runner inflow face (no swirl). For the evidence the
  inlet carries the BEP swirl: cylindricalInletVelocity, radial -1.716 m/s,
  Vt 9.90 m/s at r 0.32 m (rpm -295.3, clockwise). A swirl inlet from the
  guide vane angle is proposed as an app choice (section 4).

### Correction (owner, 2026-10-28): the mesh is the part-load version

- The guide vane file is named GV-03-91-AG5-HighRe.msh; its header carries no
  PL/BEP/HL label (Fluent "Dimensions:" and block comments only), but 03-91
  reads as 3.91 deg: test case 1's part-load angle (NTNU, Francis-99 test case
  1: PL 3.91, BEP 9.84, HL 12.44 deg, from closed). The measured opening,
  3.6-4.7 deg from closed, agrees. Test case 2 lists PL at 6.72 deg (a later
  measurement set with one runner speed for all points); the owner's speeds
  (335.4, 406.2, 369.6 rpm) differ between points, so they are test case 1's.
- The operating point is therefore taken from test case 1, PL, labelled
  "Francis-99 PL (NTNU, test case 1, GV 3.91 deg)": N = 406.2 rpm (42.54 rad/s,
  clockwise), Q = 0.071 m3/s, net head 12.29 m. The head is from a published
  table that lists PL at 406.2 rpm and 0.071 m3/s with 12.29; not confirmed on
  NTNU's own page (not reachable).
- The inlet swirl at PL: 3.91 deg is an opening from closed, not a flow angle
  (and neither was 9.84 deg, used for the BEP swirl above: that was wrong in
  kind, though it gave a plausible Euler head). The flow angle is taken from
  the mesh instead: the guide vanes' trailing-edge camber direction, 7.8 deg
  from tangential (fitted over the last 8-12 mm of the vane at mid-height).
  Angular momentum kept from the vanes to the runner inflow (same channel
  height): Vt = Vr / tan(7.8 deg) = 4.38 m/s with Vr = 0.600 m/s at r 0.32 m
  (cylindricalInletVelocity rpm -130.7, clockwise). Caveat: this gives an
  Euler head of 6.1 m (u Vt / g, u = 13.61 m/s), half the 12.29 m net head:
  low even for part load. The swirl is reported as measured from the mesh,
  not tuned to the head; the gap is open (deviation from the vanes'
  direction, or the vaneless gap) and asked of the owner. Owner (after C1
  review): keep the swirl inlet, labelled "approximate (from vane
  trailing-edge angle)"; evidence after U1: a steady MRF run of SC + GV, its
  outlet profile mapped onto the runner inlet, the Euler heads compared
  (docs/u1_one_app_notes.md 6).
- The rank runs (BEP, speed only) stay valid; the start-up and time-stepping
  runs are made at PL (the BEP driver was stopped before them).
- A check for later (owner): a Francis case whose operating point does not
  match the guide vane opening measured from the mesh gets a WARNING (the
  opening from closed against the operating point's published angle), with
  the turning-direction check (section 4).

## 2. The PC (read-only checks, owner approved)

- Windows: Intel Core i9-14900, 24 physical cores (8 performance with two
  threads each, 16 efficiency), 32 logical; 63.7 GB, 41 % used at the check
  (37.8 GB free; Memory Compression 9.2 GB, ANSYS and SpaceClaim open).
- No `%UserProfile%\.wslconfig`: WSL has its defaults, 31 GB (half the RAM)
  and all 32 logical processors (lscpu: 16 cores x 2 threads, 1 socket).
- Open MPI 4.1.6 in WSL: 16 slots by default (17 refused); 24 and 32 ranks
  run with `--use-hwthread-cpus`.

### Memory plan

- Measured (M2b-4): 1.88 kB a cell solving (pimpleFoam, kOmegaSST, ACMI,
  16 ranks); estimates carry a 1.25 margin.
- What fits in WSL as it is (31 GB): up to about 13 M cells solving (runner +
  draft tube, 11.7 M: 22.0 GB measured). The whole machine (23.0 M) needs
  about 43 GB measured rate, 54 GB with the margin: it does not fit.
- With `.wslconfig` raised (the owner's decision): Windows itself used about
  26 GB at the check, so WSL could have about 36-38 GB in practice (memory=
  38GB would leave Windows 26 GB): still short of the whole machine (43 GB)
  at today's Windows use; the whole machine needs Windows' use below about
  15 GB, or a cluster (the future-list item: a case prepared to run on a
  cluster).
- The app says this plainly before a solve (M2b-4: memory against WSL's free
  memory, with the study offered); C1 adds the limit WSL could have (the
  host's RAM less Windows' current use) to the warning, so the owner sees
  whether raising `.wslconfig` would be enough.

## 3. Evidence (v2512, runner + draft tube)

Cases made from the app's own case (scripts in the session scratchpad,
c1/), mesh linked: 11,671,680 cells, RU-DT ACMI, mesh non-orthogonality max
64.4 deg, smallest cell 3.5e-12 m3 (near-wall layers, aspect ratio up to
37.8). Every run logs the Courant field's maximum and where (fieldMinMax,
location), mapped to its part and the nearest patch on the merged mesh's
boundary surface. The rank runs are at BEP; everything else at PL (406.2
rpm: 1 deg a step is 0.410 ms).

### 3.1 Ranks (BEP, fixed 0.2 deg, 5 steps, swirl inlet)

| Ranks | solve (5 steps) | peak memory |
|---|---|---|
| 8 | 1,081 s | 20.4 GB |
| 16 | 1,057 s | 23.3 GB |
| 24 (hardware threads) | 1,066 s | 23.3 GB |
| 32 (hardware threads) | 1,119 s | 23.7 GB |

16 is the fastest, barely ahead of 8: the solve is bound by memory
bandwidth, not cores. Default: 16 ranks (the physical performance-core
threads Open MPI offers without `--use-hwthread-cpus`).

### 3.2 Start-up with the app's PIMPLE settings: every start diverges

The app's case: nOuterCorrectors 2, nCorrectors 1,
nNonOrthogonalCorrectors 0, no relaxation; linearUpwind for U. PL, fixed
1 deg, 16 ranks, 5 steps:

| Start | max Co step 1 | step 2 | step 5 | continuity (sum local) last |
|---|---|---|---|---|
| S0 from rest, normal inlet | 82.5 | 2.8e3 | 2.7e12 | 2.3e4 |
| S0 from rest, swirl inlet | 82.4 | 2.4e3 | 3.8e12 | 3.9e5 |
| S1 potentialFoam start | 82.5 | 3.3e3 | 3.1e12 | 1.6e5 |
| S2 runner speed ramped over a revolution | 74.9 | 1.6e3 | 3.1e10 | 1.3e4 |
| S3 from the steady MRF fields | 178 | 2.0e4 | stopped at step 4 (6.6e6 at step 3) | 2.4 |

The rank runs (0.2 deg, five times smaller steps) diverge the same way:
max Co 17.9, 598, 2.5e4, 4.2e6, 9.7e9. Each step multiplies the Courant
maximum by about 30, whatever the start and the step size; momentum's
smoothSolver hits its 1000-iteration limit from step 2. The ramp shows it is
not the runner's motion: at step 1 the runner has turned 0.003 deg and the
Courant maximum is already 75. Where: step 1's maximum is in the runner,
0.2-0.7 mm from a blade or the shroud (the thin wall cells); the growth then
sits at the runner inflow corners (r 0.315 m, z +-0.03 m, next to the hub
and shroud walls RU_FaceGroup81/83) and at the shroud by the runner outlet
(r 0.176 m, z -0.155 m, at the ACMI with the draft tube). Seconds a step are
not quoted: a diverging step costs up to 550 s (iteration limits).

The steady MRF run, with relaxation (simpleFoam, U 0.7, p 0.3, consistent,
nNonOrthogonalCorrectors 1), runs stably on the same mesh and interface: 100
iterations, continuity 0.035 -> 0.0012, 12.6 s an iteration, 20.1 GB. So the
mesh and the ACMI can be solved; the transient settings are the cause.

### 3.3 Robust PIMPLE settings: every start runs

The same case with nOuterCorrectors 5 (outer residual control U 1e-4, p
1e-3), nCorrectors 2, nNonOrthogonalCorrectors 1, turbOnFinalIterOnly
false, relaxation U, k, omega 0.7 and p 0.3 on the outer iterations, 1 on
the final one. PL, swirl inlet, 16 ranks, 4 steps:

| Run | step | max Co, steps 1-4 | continuity last | s a step | h a revolution | peak |
|---|---|---|---|---|---|---|
| P1 potentialFoam start | 1 deg | 69, 85, 79, 68 | 9.9e-9 | 267 | 26.7 | 22.0 GB |
| P2 from rest | 1 deg | 69, 85, 79, 68 | 9.4e-9 | 269 | 26.9 | 22.1 GB |
| P3 potentialFoam start | 2 deg | 124, 148, 127, 100 | 1.2e-8 | 260 | 13.0 | 22.0 GB |
| P4 from the steady MRF fields | 1 deg | 49, 28, 25, 25 | 9.8e-10 | 258 | 25.8 | 22.5 GB |

- All four are stable: the Courant maximum stays flat (69-148) where the
  app's settings multiplied it by 30 a step. It sits in the runner's wall
  cells (0.3-1.3 mm from the hub or shroud by the runner inflow), so a
  Courant limit of 1-2 would mean tiny steps for nothing (the S/T plan's
  "maxCo 2" run, about 0.005 deg a step, was not run).
- Each step used all 5 outer iterations. Correction (found after the
  review): the residual control these runs were given was the openfoam.org
  keyword (outerCorrectorResidualControl); v2512 reads `residualControl`
  inside PIMPLE, so P1-P4 had none and always ran 5. The pitch runs (3.4)
  and the app use `residualControl`; v2512 then logs "PIMPLE: not converged
  within 5 iterations" for a step that reaches the limit, which the app
  counts. pFinal stopped at its 50 iterations in P1-P4.
- The start matters little for stability once the settings are robust: P1
  and P2 are nearly the same. The MRF start gives the developed flow at
  once (Courant maximum 25, continuity 1e-9) and costs 100 MRF iterations,
  21 minutes, about five 1-deg steps.
- 2 deg a step halves the hours a revolution (13.0 against 26.7) and was
  stable over its 4 steps; only 4 steps were run.
- Seconds a step from the log, the first step left out; hours a revolution
  = s a step x 360 / degrees a step / 3600.

### 3.4 2 deg against 1 deg over one blade pitch (owner, after review)

E1 2 deg x 12 steps and E2 1 deg x 24 steps (24 deg each), from the S3
steady MRF fields (100 iterations), the robust settings of 3.3 with
`residualControl` as the app writes it, PL, swirl inlet, 16 ranks. Torque
is the z moment of the runner's walls (forces, CofR 0) from the solver log.
Both ran under the memory watchdog (2026-10-31); it never stopped either.

| Run | steps | stable | steps not converged in 5 outer | s a step | h a revolution | peak | Windows free, min |
|---|---|---|---|---|---|---|---|
| E1 2 deg | 12/12 | yes, rc 0 | 12 of 12 | 297 | 14.9 | 24.0 GB | 16.9 GB |
| E2 1 deg | 24/24 | yes, rc 0 | 24 of 24 | 270 | 27.0 | 24.2 GB | 17.7 GB |

Torque, N·m, at the same runner angle:

| angle | 2 deg | 4 | 8 | 12 | 16 | 20 | 24 |
|---|---|---|---|---|---|---|---|
| E1 2 deg | -1979 | -166 | 120 | 138 | 156 | 162 | 169 |
| E2 1 deg | -399 | -51 | 26 | 57 | 76 | 84 | 82 |

- Both step sizes are stable over a whole blade pitch: no divergence, rc 0.
- No step of either run met the residual control within 5 outer
  iterations ("PIMPLE: not converged within 5 iterations" every step). The
  steps are not converged to 1e-4/1e-3; the app reports this count.
- The first step's large negative torque (-1979 and -4396 N·m) is the
  switch from the MRF fields to the moving mesh; it is gone within 3 to 5
  steps. Reported with that caveat, not as a result.
- The torque depends on the step: at 24 deg the 2 deg run gives 169 N·m,
  the 1 deg run 82 N·m. Neither has settled: E1 still rises 2-4 N·m a step;
  E2 levels near 85 and falls the last 3 steps. One blade pitch from the
  MRF start is too short for a torque. The expected 100-140 N·m (section 1)
  is bracketed, not matched.
- Cost: 2 deg halves the hours a revolution (14.9 against 27.0 with forces
  written every step; 13.0 against 26.7 in 3.3 without). The extra outer
  work at 2 deg does not show as a longer step.
- For the app: 2 deg stays the "slow effects" choice it is labelled; a
  torque or pressure-pulse study needs 1 deg or less, and more than one
  pitch after the start. Both unchanged from the plan in section 4.

## 4. Plan for the app (reviewed; built in C1, section 5)

1. Transient settings for real machines (imported meshes): the robust
   PIMPLE set of 3.3 becomes the default for a rotating imported-mesh case.
   Today's set (nOuter 2, nCorrectors 1, no relaxation) diverged in every
   run. Existing STL projects keep their settings byte-identical; the change
   is limited to imported-mesh machines and stated in the case's notes.
2. Start-up, a labelled choice (default first):
   - "Potential flow" (potentialFoam before the first step; seconds);
   - "Steady MRF first" (simpleFoam with the rotating zone as MRF, N
     iterations, then the transient from its fields; about 21 min at 100
     iterations here): the developed flow from the first step;
   - "From rest".
   The ramp is not offered: it diverged like the others with the app's
   settings and brings nothing with the robust ones.
3. Time step, a labelled choice: "1 deg a step" (default) and "2 deg a
   step (faster, 4 steps tested)", each shown with the hours a revolution:
   from the PC's measured seconds a step once a run exists (read from the
   log), else the estimate from this evidence (about 23 s a step per
   million cells on 16 ranks with 5 outer iterations). A Courant-limited
   step is not offered for imported meshes (wall cells set it).
4. The swirl inlet from the guide vanes (runner without its guide vanes):
   cylindricalInletVelocity with Vr = Q / A and Vt = Vr / tan(flow angle),
   the flow angle measured from the guide vanes' trailing-edge camber (7.8
   deg here) or entered; turning direction from the guide vanes.
5. WARNINGs (validation): the runner turning against the inlet swirl; a
   Francis operating point whose published guide vane opening does not
   match the opening measured from the mesh.
6. Ranks and memory: 16 ranks by default (3.1); the memory warning adds the
   limit WSL could have with `.wslconfig` raised (section 2).

Open for the owner: the PL swirl's Euler head (6.1 m against 12.29 m net);
the Francis-99 guide vane drawing (pivot, closed angle); the PL head's
source; whether 2 deg should be tried over a longer run before it is
offered.

Owner's decisions (after review): 1. the robust settings for imported
machines (.msh cases), STL-built VAWT/HAWT unchanged until evidence says
otherwise; the final iteration of each step not relaxed; residualControl on
the outer loop and the steps that reach the limit reported; every setting
labelled with its reason. 2. The time step as "what are you studying?",
hours a revolution beside each choice: 2 deg slow effects (part-load vortex
rope), 1 deg marginal for rotor-stator pulses (about 13 steps a guide vane
passing at 28 vanes), 0.5 deg rotor-stator pulses; evidence 3.4. 3. The
swirl kept, labelled approximate; the SC + GV evidence after U1. 4. The
opening confirmed by file name and measurement.

## 5. Built (C1)

- `case_setup/dictionaries.py`: an imported machine's controlDict (a fixed
  step of the chosen angle, no Courant limit) and fvSolution (nOuter 5 with
  residualControl U 1e-4 / p 1e-3, nCorrectors 2, nNonOrthogonalCorrectors 1
  or the > 70 deg rule's 2, turbulence every outer iteration, relaxation U
  0.7 / k, omega 0.7 / p 0.3 with Final 1), each entry with its reason in a
  comment; decomposeParDict (physics.ranks, default 16); Allrun (the run
  order: the start-up, then pimpleFoam on the ranks; as root it allows Open
  MPI to run as root); for the steady MRF start, mrf/ (simpleFoam with
  MRFProperties, the same 0/ fields; Allrun links its mesh, copies its last
  fields into 0/). `solution_settings`: the settings and reasons for the
  report and the screen. An STL-built case is unchanged.
- `case_setup/physics.py`: `SwirlInlet` (kind SWIRL: Q, flow angle from
  tangential, direction), `StartUpConfig` (method, MRF iterations),
  `GuideVaneOpening` (the operating point's and the mesh's opening, its
  source, the vane count), `ranks`; `STEP_STUDIES`.
- `case_setup/setup.py` / `fields.py`: the swirl inlet resolved
  (cylindricalInletVelocity about the zone's axis, its radius measured from
  the mesh: `surface.patch_radii`; an inlet facing the axis over less than
  90 % of its area is refused).
- `case_setup/validation.py`: SWIRL_INLET_NOT_IMPORTED and
  SWIRL_DIRECTION_NOT_SET (BLOCKING), RUNNER_AGAINST_SWIRL (WARNING),
  GUIDE_VANE_OPENING_MISMATCH (WARNING), GUIDE_VANE_RANGE_INVALID
  (BLOCKING); an imported machine may step 2 deg without ROTATION_STEP_LARGE.
- `machines/msh_import/solve_estimate.py`: the steps that reached the outer
  limit, counted from the case's log; hours a revolution for each choice;
  the scaled reference is the robust settings' 266.6 s a step (P1).
  `host_memory.py`: what WSL could have with .wslconfig raised (the host's
  RAM less Windows' own use, through powershell.exe; None when unreadable).
- Screens: Time (what are you studying?, hours a revolution, start-up, MRF
  iterations, ranks), Operating point (the swirl switch, flow angle and
  direction, the guide vane opening), Boundary (a SWIRL inlet for an
  imported machine), Case setup (the settings with reasons, the run order,
  the outer-loop record, WSL's ceiling in the memory warning).
- From the testing review: Allrun is a plain script (its shebang first, no
  generated-file header), stops at the first failed step (`|| exit 1`; the
  MRF start also when it wrote no fields), and refuses to start over an
  earlier run's output (log.*, processor*: a new setup keeps them, and
  RunFunctions would skip a step whose log exists); a user's own case/Allrun
  is kept (INFO CASE_RUN_ORDER_USERS); more ranks than the PC's logical
  processors is a WARNING (RANKS_ABOVE_CORES; v2512's runParallel
  oversubscribes, so it runs, slower); the new fields' findings show in
  Operating point and Time.
- Windows' free memory before any heavy run (owner, 2026-10-31, after a
  solve was stopped while ANSYS held the memory): WINDOWS_MEMORY_LOW
  (WARNING, "Windows has X GB free; close other heavy programs (e.g.
  ANSYS)") when Windows has less free than the run's estimate plus 6 GB; in
  the STL meshing preflight, the imported-mesh step preflight and the Case
  page's solve estimate (`host_memory.low_windows_memory`; the probe is the
  pipeline's `host_probe`). The evidence runs got the same watchdog: below
  6 GB on Windows (or 1.5 GB in WSL) the solver is stopped cleanly
  (stopAt writeNow: the step in progress ends and is written; checked on
  v2512) and the last completed step is recorded.
- Tests: `tests/unit/case_setup/test_c1_imported_case.py`;
  `tests/integration/test_c1_startup_real.py` (each start-up's Allrun on
  v2512, the swirl inlet on the fixture machine's outer ring). Two existing
  tests changed for the owner's approval: the correctors test (an imported
  case's plain correctors are now 2 / 1) and the physics-sections guard (the
  imported-only fields listed).
