# M2b: a machine from several mesh files (plan and evidence)

Branch `m2b-assembly`, from main after U1-1 was merged (1425d63). PLAN ONLY:
nothing here is built yet; the owner reviews it first.

Files: the owner's four Fluent meshes in `/root/meshes` (never committed):
GV-03-91-AG5-HighRe (guide vanes, one passage), RU-AG5-HighRe (runner, one
passage), DT-HighRe-uns (draft tube, full), SC-SV-HH-HighRe (spiral casing
with stay vanes, full). E0 (docs/msh_import_notes.md 13) converted them and
assembled the machine by hand (`/root/e0_francis`); the evidence below reuses
those conversions. Scripts: the session scratchpad `m2b/`; data
`/root/m2b_ev` in WSL. Machine: i9-14900 (WSL: 16 cores, 32 threads), WSL
31 GB of the host's 63.7 GB.

## 1. Evidence (v2512)

### 1.1 Passage count from the periodic patches

Each part's boundary surface was extracted as the app does (surfaceMeshExtract,
0.6-6.6 s, 0.08-0.74 GB). Two patches of one part are a periodic pair when
they have the same face count and a rotation about the machine axis maps one's
face centroids onto the other's (largest miss below 1e-6 of the part's size);
N = 360 / angle.

| Part | Periodic pairs | Angle | N |
|---|---|---|---|
| GV | FaceGroup16/17, 33/34, 51/52 | 12.857143 deg (spread 1.6e-7) | 28.0000 |
| RU | r1_per1_k / r1_per2_k, k = 1..5 | 24.000000 deg (spread 6.3e-8) | 15.0000 |
| SC, DT | none | | full 360 |

0.02 s per part. Both counts are the owner's (28, 15). Both files type these
zones periodic / periodic-shadow, but the converted patches are plain
`patch`es and the files do not give the angle: the rotation test pairs them
and measures it.

### 1.2 Copies and stitching

E0's rings (rotate a copy, mergeMeshes, doubling: 5 merges for 28, 6 for 15;
then stitchMesh -perfect per pair): every periodic patch has **0 faces** left
in the ring's boundary file, and checkMesh finds 1 region per ring.

| Ring | Cells | Time | Peak RAM |
|---|---|---|---|
| GV, 28 copies, 3 stitches | 7,884,800 | 61 s (+ checkMesh 14 s) | 7.5 GB |
| RU, 15 copies, 5 stitches | 10,780,800 | 136 s (+ checkMesh 20 s) | 10.2 GB |
| Machine: 3 merges, createPatch, topoSet | 22,984,188 | 133 s | 18.2 GB |

About 5.5 min in all, before the checks. Each stitch leaves a faceZone
(`FaceGroup16FaceGroup17CutFaceZone`).

**Name clash found:** GV and RU both have patches called FaceGroup22 and
FaceGroup37. mergeMeshes joins patches of the same name: E0's machine has one
FaceGroup22 of 11,488 faces (28 x 256 from GV, a wall, plus 15 x 288 from RU)
and one type for both. The assembly must prefix each part's patch names.

### 1.3 Interface candidates

For every pair of patches of different parts (periodic ones left out): the
share of each one's area lying on the other's meridional (r, z) trace (within
1.5 x the other's typical face size), and the full-ring areas (a passage
patch's area x N). 41 pairs overlap at all, found in 24 s:

| Candidate | Coverage each way | Full-ring areas (m2) |
|---|---|---|
| SC.SV-Outlet ~ GV.FaceGroup27 (r 0.3822) | 100 / 100 % | 0.14127 / 0.14128 |
| GV.FaceGroup13 ~ RU.r1_inflow_1 + r1_inflow_2 (r 0.3200) | 100 / 100 % each | 0.11828 / 0.05957 + 0.05871 = 0.11828 |
| RU.r1_outflow_1 + r1_outflow_2 ~ DT.DT_INLET | 100 / 86.3 %, 100 / 91.7 % | 0.05074 + 0.04704 = 0.09778 / 0.09775 |
| best of the rest (GV.FaceGroup14 ~ RU.r1_hub_up_1_1) | 5.8 / 11.6 % | 0.02060 / 0.00435 |

The three candidates are exactly E0's pairs; the next is far below. A side
can be two patches of one part (RU's two halves of a passage); their areas
then add up to the other side's. Patch types do not help: SC-Inlet and
DT_INLET convert as walls.

### 1.4 AMI weights over one blade pitch

moveDynamicMesh -checkAMI on the 23 M machine, the runner turned 24 deg in
12 x 2 deg: 1,021 s (85 s a step, serial), 15.7 GB.

| Pair | At rest | Over the pitch |
|---|---|---|
| SV-GV | 0.99969-1.00011 | 0.9997-1.0001 |
| GV-RU | 0.999998-1.000004 | 1.0000-1.0000 |
| RU-DT (runner side) | 0.648203-1.00005 | 0.6475-0.6499 (min) |

At 24 deg the RU-DT weights are 0.648203 / 0.756369, the at-rest values to
every printed digit: the runner mesh repeats every 24 deg. The low minimum is
E0's shroud-edge row (0.21 % of the runner side's area below 0.95).

### 1.5 Seconds per solver step

pimpleFoam with the app's fvSchemes and fvSolution (2 + 2 correctors: max
non-orthogonality 84.9 deg), kOmegaSST, water, runner at 1000 rpm, 1 deg a
step, decomposed with scotch:

| Mesh | Ranks | Result |
|---|---|---|
| Machine, 23.0 M | 16 | out of memory before the first step ended (32.1 GB used; stopped by a watchdog) |
| Machine, 23.0 M | 8 | out of memory in the first U solve (33.1 GB) |
| Runner ring + draft tube, 11.7 M | 16 | 24.0 GB (2.1 kB a cell); steps 1-4: 185, 157, 128, 174 s |

The 11.7 M case was badly posed (inlet on the runner's rotating inflow face,
impulsive start: max Courant 101 at step 2) and diverged after step 4 (506
and 720 s for steps 5 and 6). decomposePar: 23 M in 35 s / 15 GB.

What this says honestly: on this PC a solve of the whole machine needs about
2.1 kB x 23 M = 48 GB, more than WSL's 31 GB. At about 160 s a step for
11.7 M on 16 cores, the whole machine would take about 5-6 min a step (36 h
a revolution at 1 deg a step), from a scaled, not a measured, figure.

### 1.6 3D pane and ParaView

- Decimation (the app's vertex clustering) of the machine's 2.19 M boundary
  faces: 0.5-0.6 s. Today's share per patch by face count gives the draft
  tube 2,633 triangles (59 mm) and the clashed patches 20; a budget per part
  (20 k each) gives DT 16,120 (24 mm), SC 13,140, RU 10,154, GV 6,986. The
  clustering also lands well below its limit (47 k of 100 k): its coarsening
  step (x 0.7) is too large.
- Windows reads the 23 M mesh (2.4 GB: points, faces, owner, neighbour) at
  `\\wsl.localhost\Ubuntu-24.04\root\...` at 364 MB/s (6.5 s). ParaView 6.2.0 has since been
  installed (owner, 2026-10-27); "Open in ParaView" is on the U1 future list.

## 2. Plan

### a) Multi-file import (commit M2b-1)

- A project's mesh source can be **several .msh files**: pick a folder (all
  its .msh files) or several files, with the M2a disk picker and the copy into
  WSL. Each is listed with its header summary (`scan.summarize`: format,
  declared cells, cell zones, boundary zones, unnamed ones).
- Each file gets a part: Spiral casing, Guide vanes, Runner, Draft tube or a
  custom name, and a short prefix (SC, GV, RU, DT or the user's), used for its
  patches and zones.
- Each file is converted on its own (M2a: binary, checks per file), its
  surface extracted, and its passage count detected (1.1) about the machine
  axis. Shown: "Guide vanes: 1 of 28 passages (12.857 deg; 3 periodic pairs)"
  or "full 360 deg (no periodic pair)". The user confirms or corrects N.
  When no rotation matches, or 360/angle is not a whole number, the copies
  cannot be made: BLOCKING, with the pair and the angle.
- **Assembly, in the background** under the heavy-step lock, with progress
  per step and a preflight per step (models from 1.2, scaled by cells):
  1. prefix every patch with its part (`GV_FaceGroup22`); the cell zones are
     prefixed too when two parts share a zone name;
  2. per passage part: copies by doubling, then stitchMesh -perfect per
     periodic pair; the proof: each periodic patch has 0 faces afterwards and
     checkMesh finds one region per ring, else BLOCKING with the faces left;
  3. merge the parts (mergeMeshes);
  4. checkMesh in two levels (M2a), and at this size without -allTopology
     (23 M: 31.9 GB with it), wall baffles counted by mergeOrSplitBaffles
     -detectOnly instead (8 s, 9.2 GB).
- The E0 numbers are shown beside the preflight: about 5.5 min and 18 GB for
  the 23 M machine.

### b) Interfaces, asked before pairing (commit M2b-2)

- **Which part rotates**: asked, never assumed (one part, or none).
- **Candidates** (1.3): each shown with both sides' radius and axial range,
  the overlap area, the coverage each way and the full-ring areas; both sides
  highlighted in the 3D pane. Suggested only when both coverages are at least
  80 % and the full-ring areas agree within 2 % (sides of two patches grouped).
  The user confirms, corrects (picks the patches) or rejects each, and can add
  a pair by hand.
- **Joint type per pair**: rotating-stationary: AMI. Stationary-stationary:
  AMI or stitch; stitch is offered only when the faces match one to one (SV-GV:
  75,043 against 62,720 faces, so AMI). cyclicACMI for partly overlapping
  sides, with the evidence: RU-DT has 0.21 % of the runner side's area below
  0.95 (the shroud edge row) and coverages of 100 / 86-92 %.
- Only then are the pairs created (createPatch, under their own names), and
  their weights at rest shown (with the M2a detail below 0.95).

### c) 3D for big meshes (commit M2b-3)

- Decimation sized per part: the display limit split between the parts, then
  per patch; a finer coarsening step so the limit is used.
- Colours by part (and by zone), each with an on/off toggle; a cut plane
  through the machine axis; the interface pairs highlighted.
- Short zone names with the full name on hover (as in M2a).
- **Open in ParaView**: the app writes `<project>.foam` beside the assembled
  case and shows its Windows path (`\\wsl.localhost\Ubuntu-24.04\...`), with
  the steps: File > Open in ParaView, paste the path, choose
  "OpenFOAM" reader, Apply. 2.4 GB reads in about 7 s; ParaView itself then
  needs Windows memory for 23 M cells.
- **trame**: not now. It would give a server-side VTK view (no decimation)
  but adds a second web server, a port and the VTK stack; worth a trial once
  plotly with per-part decimation is in, on the 23 M machine (future list).

### d) Revolution check (commit M2b-4)

- Runner built from N copies: one pitch, 12 steps, labelled "over one blade
  pitch (24 deg, 12 x 2 deg), equivalent to a full revolution because the
  runner mesh repeats every 24 deg" (1.4: 17 min on the machine). Unknown N:
  36 x 10 deg as now, with coarser sweeps offered above 10 min (45 deg before
  30 deg).

### e) Run-time estimate (commit M2b-4)

- Before a solve of the assembled machine: the memory it needs (2.1 kB a
  cell, measured; 48 GB for 23 M) against what WSL has, and a time from
  measured seconds per step: the first steps of the run are timed, and the
  estimate is shown as "about N min per step, about H h per revolution at
  1 deg/step", updated as steps finish. Until a step is measured, a figure
  scaled from the last measured case, labelled as scaled.
- The evidence for this commit: the app's own case on the assembled machine
  (or on the runner + draft tube while the whole machine does not fit).

### Tests

Unit tests on small fixtures (a periodic sector made by blockMesh and copied:
N detected, stitched, 0 faces left; two parts with a clashing patch name;
interface candidates on coaxial cylinders); the fake OpenFOAM for the UI; the
existing tests unchanged. Opt-in real tests on the owner's files
(`MACHINES_E0_DIR`), as in M2a.

## 3. Owner's answers (2026-10-27)

1. **Memory: (c).** The app warns before any solve whose memory estimate
   exceeds WSL's free memory and offers the runner + draft tube study. The
   owner decides on `.wslconfig` personally. Future list: "prepare the case
   to run on a cluster" (decomposePar, run script, README).
2. **Prefixes: always.** Every patch and cell zone name gets the part's short
   name (`GV_FaceGroup13`, `RU_fluid`).
3. **Stitch faceZones: kept**, named by part, and shown as "seams" in the
   overview (not as patches).
4. **Pitch sweep:** 12 x 2 deg over one pitch by default; 6 x 4 deg offered
   when the estimate is over 10 min; the label states the steps.
5. **Where:** "several .msh files" is a mesh source of Francis and Custom now
   (every case type after U1), not a separate project type.

Also: rerun the runner + draft tube timing with the app's own case setup, so
the seconds per step and the memory are measured on a well-posed case (1.5's
test case started from rest with the inlet on the rotating inflow face and
diverged after step 4).

## 4. Built

### M2b-1: several .msh files as a mesh source

- **Configuration.** `imported_mesh` is either the single file (`ImportedMesh`,
  unchanged: an existing project is written and hashed as before) or a
  `MachineParts`: the parts (name, prefix, file, units, sha256, passages,
  periodic pairs), the machine axis and a point on it, the rotating zone and
  the AMI pairs. A pydantic tagged union; the tag is taken out of error
  locations, so a missing unit is still `imported_mesh.source_units`. The
  schema version stays 3: raising it would change an existing test
  (test_g6_case_setup asserts 3), which needs the owner's approval; an older
  app reading a several-file project names the unknown field `parts`.
- **Where.** A third mesh source, "Import several .msh files (a machine in
  parts)", of every profile that offers an import (Francis, Custom, HAWT).
- **Read the parts.** Each part with a prefix is converted once at scale 1
  into `cases/part_<P>_convert/` with its surface (M2a's scratch conversion,
  per part). The passage count is found on that surface about the machine
  axis and shown as "1 of 28 passages (12.857143 deg; 3 periodic pairs:
  ...)"; a button confirms it (passages and the pairs go into the
  configuration). A patch around the whole axis is never one side of a pair.
  The owner's four files are read in 18 s (v2512).
- **The run.** The import operation, part by part: the conversion reused (or
  made), the passage count found again (a different one is BLOCKING
  PART_PASSAGES_CHANGED, e.g. after the axis changed), then the ring in
  `cases/part_<P>/`: scaled, prefixed (createPatch, topoSet; the zone `<P>`
  of all the part's cells from an empty set inverted), copied by doubling
  (transformPoints `-centre` on the machine axis, mergeMeshes), stitched
  (stitchMesh -perfect), the emptied periodic patches removed (createPatch
  with no patch; v2512: "Removed zero-sized patch", the seam faceZone kept),
  checkMesh: one region, no face on a periodic patch, else BLOCKING
  PART_RING_INVALID. `ring.json` records its inputs: a ring is reused while
  they are the same (one part changed rebuilds that ring only). The rings are
  merged into `cases/imported/` (the cell count must be their sum) and its
  surface extracted; from there the run is M2a's.
- **Checks of the merged mesh.** checkMesh without -allTopology, measured on
  the 23 M machine: 23.5 GB / 90 s (with it 31.9 GB / 170 s); the baffles by
  mergeOrSplitBaffles -detectOnly: 9.2 GB / 7.8 s, 0 duplicate faces. The
  mesh must have one region per part (MACHINE_REGIONS, BLOCKING). Resource
  models added (resources.py): assembly 0.95 kB and 14.4 us a machine cell
  (E0), checkMesh without -allTopology 1.05 kB and 3.9 us, baffles 0.4 kB.
- **Seams.** stitchMesh names its faceZone `<P>_<a><P>_<b>CutFaceZone`: named
  by part through the prefixed patches; listed with its faces in the import
  report (the overview shows them in M2b-3).
- **Checks before any conversion.** Per part: the file's own findings, a
  changed file, the units against its box, passages confirmed (BLOCKING
  PART_PASSAGES_NOT_CONFIRMED), a periodic patch of the file; prefixes and
  files used once. Then the single file's zone, AMI-pair and patch checks on
  the parts read as one mesh (prefixed patches without the periodic ones,
  the zones `<P>` and `<P>_<zone>`).
- **Tests.** Fixtures `tests/fixtures/msh/m2b/` (blockMesh, foamMeshToFluent;
  `make_fixtures.sh`): a casing ring, a 45 deg vane passage and a 60 deg
  runner passage whose patch and zone names clash. Unit tests (detection on
  and off the origin, the doubling for N up to 31, the dictionaries, the
  checks), UI tests with the fake OpenFOAM, and real v2512 tests: read,
  assemble (384 cells, 3 regions, 2 seams, AMI weights 1.0), reuse, a ring
  rebuilt alone, a passage count no longer found; opt-in on the owner's
  files (`MACHINES_E0_DIR`).
