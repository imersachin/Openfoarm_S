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
  settle it: asked of the owner.
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
  direction, or the vaneless gap) and asked of the owner.
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
- Each step uses all 5 outer iterations (the residual targets are not met
  in 5; pFinal stops at its 50 iterations): the steps are stable, not
  converged within the step. More outer iterations cost about 50 s each.
- The start matters little for stability once the settings are robust: P1
  and P2 are nearly the same. The MRF start gives the developed flow at
  once (Courant maximum 25, continuity 1e-9) and costs 100 MRF iterations,
  21 minutes, about five 1-deg steps.
- 2 deg a step halves the hours a revolution (13.0 against 26.7) and was
  stable over its 4 steps; only 4 steps were run.
- Seconds a step from the log, the first step left out; hours a revolution
  = s a step x 360 / degrees a step / 3600.

## 4. Plan for the app (after review; nothing built yet)

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
