# Rotating Machinery — G0 Method Notes

| | |
|---|---|
| Milestone | G0, method proof (`docs/rotating_machinery.md` section 15) |
| Target | OpenFOAM v2512 (openfoam.com / OpenCFD), Ubuntu 24.04 under WSL2 |
| Build | `OpenFOAM-2512`, build `_bd2b6720-20260127`, `label=32;scalar=64`, packages `openfoam2512` and `openfoam2512-tutorials` `2512.0-2` |
| Machine | 32 cores, 31 GB RAM (WSL2), Python 3.12.3 |
| Geometry | **Synthetic only.** The real STLs of section 17 are not provided yet (section 7) |
| Evidence | `tests/fixtures/machines/g0/`: the dictionaries that ran, every command log, exit codes |
| Reproduce | `PYTHON=<venv python> bash tests/fixtures/machines/g0/run_all.sh` (about 4 min on this machine: 120 commands, 216 s of command time); then `common/check_rotation.sh` |
| Read back | `tests/unit/machines/test_g0_logs.py` (no OpenFOAM needed) |

Everything below is from runs on this installation. Statements without a log
reference are marked as not verified. The solver runs are hand-run evidence
only (owner, conflict 1): no solver setting enters the application from G0.

---

## 1. Summary

1. **Cylinder domain:** both candidate methods give valid meshes: an O-grid
   from blockMesh alone, and a box background cut to the cylinder by
   snappyHexMesh (R1). Recommendation: the snappy cut (section 3.1).
2. **Imported domain:** both formats work and give identical meshes. Patch
   names come from the `solid` names or the file names; a type can be set per
   region (R2). A binary STL given as named regions silently becomes one
   patch. An open set of surfaces silently meshes the outside: snappyHexMesh
   exits 0 and checkMesh says OK.
3. **HAWT axial rotating zone:** the V0 two-mesh method (method A) works
   unchanged for a disc zone on the flow axis, with either domain method (R3).
4. **Two interfaces:** four separately meshed Francis-like parts merge into one
   mesh with two sliding AMI pairs, with conformal and non-conformal discs
   (R4). The stationary joint between two stationary parts works as a
   cyclicAMI pair in both cases. `stitchMesh` joins it only when the discs
   happen to be conformal; on non-conformal discs it exits 0 and leaves an
   invalid mesh.
5. **Pole:** both owner options mesh and run (R5): a pole split by the zone
   whose inner part rotates, and a stationary pole inside a hole through an
   annular zone. A third form also ran: the split pole with its inner part
   held stationary, valid only because the pole is a surface of revolution
   about the axis.
6. **Solver smoke test:** `pimpleFoam` with a solid-body rotating zone
   completed 10 time steps in 9 of 10 runs (R3–R5). The exception is the
   invalid non-conformal stitched mesh. The boundary conditions that ran are in
   section 4.
7. **Region rule:** the number of mesh regions equals the number of separately
   meshed parts, minus stitched joints (2, 3 or 4 here; section 3.6).

---

## 2. Geometry

`tests/fixtures/machines/g0/common/make_geometry.py` writes every surface, in
metres. Sizes were chosen for small, fast meshes. **They are test values, not
presets or recommendations.**

| Set | Contents |
|---|---|
| `hawt` | Hub r 0.06 m, x ±0.08; 3 lofted blades r 0.04–0.5, chord 0.10–0.05, thickness 0.016, 25°→5° to the rotor plane; axis x, flow +x. Domain cylinder r 2, x −2…5, regions `inlet`/`outlet`/`side` |
| `duct` | Closed 2.0 × 0.5 × 0.5 duct turned 20° about z (no face on a background plane). As one ASCII STL with regions, as one file per patch, and as binary STL |
| `francis` | Francis-*like* passage along z (flow +z), pipe r 0.3. Four closed fluid-domain STLs with named regions: `casing` (z −0.6…−0.3), `guide` (8 vanes, 30° to the axis), `runner` (hub + 5 blades, 40° to the axis), `draft` (widens to r 0.45 at z 0.9) |
| `pole` | The V1 fixture rotor's four blades (D 1.04, axis z), a pole r 0.03 through the whole V0 domain, an annular zone r 0.08–0.78, z ±0.66 (regions `outer`, `inner`) |

Simplifications, because the generator does no CAD booleans:
- Vanes and runner blades end 15–30 mm short of the pipe wall.
- The runner hub floats inside the runner part.
- A real Francis machine is radial-inflow with a spiral casing. Only the
  topology was tested: four parts, a stationary joint and two sliding
  interfaces.

---

## 3. Results

Validity rule as in the engine: checkMesh `-allTopology -meshQuality`, and
validity from the parsed output, never the exit code (V0 R5). All meshes
below report `Mesh OK.` unless stated.

### 3.1 R1 Cylinder domain (spec 5.2)

Logs: `R1_cylinder_domain/logs/`.

| | (a) O-grid, blockMesh | (b) box cut by snappyHexMesh |
|---|---|---|
| Dictionary | `ogrid/system/blockMeshDict`: centre square plus four blocks with `arc` edges (form as the tank in mixerVesselAMI2D) | `snappy/system/*`: box 0.125 m cells; `domain_cylinder.stl` with regions; `surfaceFeatureExtract` for the end edges |
| Cells | 50,176 | 45,472 |
| `inlet` / `outlet` / `side` faces | 896 / 896 / 3,584 | 812 / 812 / 4,928 |
| Max non-orthogonality, skewness | 33.9, 0.57 | 19.1, 0.46 |
| Time | blockMesh < 1 s | 4 s in all |
| With a rotating zone (R3) | AMI warning, see 3.3 | clean |

**Recommendation: (b).**
- It is the same path as the box and imported domains (section 3.2): one
  snappy-based domain path for all three domain kinds.
- Patch names and types come from the surface regions.
- With a zone cut out (R3) it gave the cleaner interface.
- The O-grid needs no snappy pass for the domain itself and its cells follow
  the wall. Nothing here needed that.

Not run: mixerVesselAMI2D builds its AMI pair directly in blockMesh (patches
`AMI1`/`AMI2` in `blockMeshDict.m4`). That is a third interface method for
cylindrical zones.

### 3.2 R2 Imported domain (spec 5.3)

Logs: `R2_imported_domain/logs/`, plus `python_checks.json` (what a Python
reader sees: format, region names, closedness).

| Input | snappyHexMesh exit | Patches (faces) | Cells | Python check |
|---|---|---|---|---|
| One ASCII STL, regions `inlet`/`outlet`/`wall` | 0 | `inlet` 56, `outlet` 56, `wall` 986 | 2,048 | 3 regions; closed |
| One file per patch (`inlet.stl`, …) | 0 | identical | 2,048 | closed union |
| Same surface as binary STL | 0 | **`duct` 1,098 only** | 2,048 | binary; no region names |
| Per-file set without `outlet.stl` | 0 | `inlet` 130, `wall` 2,026, **`background` 3,928** | **14,552** | union open (4 open edges) |

- **Region names (spec 5.3):** in `geometry`,
  `regions { <solid> { name <patch>; } }` names the patch. In
  `refinementSurfaces`,
  `regions { <solid> { level (..); patchInfo { type ..; } } }` sets the level
  and type per region. Without the rename a patch is `<surface>_<region>`
  (annotated dictionary). One file per patch: the geometry name is the patch
  name.
- **Binary STL as named regions:** it becomes one patch named after the
  surface, the inlet and outlet are lost, and nothing fails. Detecting binary
  takes the size rule: `84 + 50 × triangles` bytes. A file may start with
  "solid" and still be binary (V1 fixture).
- **Open domain:**
  - snappyHexMesh keeps the region containing the mesh point, which now
    includes the background box outside the duct.
  - The box's `background` patch survives with faces, and checkMesh reports
    OK.
  - The closedness check before meshing catches it (spec 10, BLOCKING).
  - A surviving background patch with faces after meshing is the result
    signature.

### 3.3 R3 HAWT, axial flow (V0 method A)

Logs: `R3_hawt/logs/`.

| Mesh | Cells | Regions | Interface faces | Max non-ortho / skewness |
|---|---|---|---|---|
| outer, O-grid domain | 68,632 | 1 | `AMI_outer` | 41.6 / 0.61 |
| outer, snappy domain | 56,352 | 1 | `AMI_outer` | 41.5 / 0.72 |
| rotor (hub + 3 blades, level 3) | 57,312 | 1 | `AMI_rotor` | 61.8 / 3.13 |
| merged, O-grid outer | 125,944 | **2** | AMI1 5,968 / AMI2 2,712 | 61.8 / 3.13 |
| merged, snappy outer | 113,664 | **2** | AMI1 3,232 / AMI2 2,712 | 61.8 / 3.13 |

- **Sequence:** the zone is a `searchableCylinder` along x (r 0.6, x ±0.12).
  The sequence is V0 E1 unchanged: outer snappy with the cylinder removed;
  rotor blockMesh → surfaceFeatureExtract → snappyHexMesh → topoSet (whole
  sub-mesh = cell zone `rotating`); then mergeMeshes and createPatch.
- **Rotor mesh quality:** the 16 mm blades at level 3 (5 mm cells) push the
  rotor mesh to non-orthogonality 61.8 and skewness 3.13. Both are under the
  engine's limits (65, 4). Real blades will differ (section 7).
- **O-grid AMI warning:** with the O-grid outer, every AMI update logs
  `Invalid normal for source face 3632`. That is a face on the `x = −0.12`
  cap near the rim, paired with a face on the opposite cap; the warning
  appears 11 times in each O-grid run. The snappy outer gives no warning.

### 3.4 R4 Francis-like passage, separate fluid-domain STLs (owner decision 2)

Logs: `R4_francis/logs/`.

- **Method:** each part is meshed on its own:
  - a background box ending 1.5 cells beyond the part, so its faces lie
    mid-cell;
  - snappyHexMesh with the part's STL, regions renamed;
  - the mesh point inside the part's fluid.
- **Joining:** `mergeMeshes` adds the parts one at a time to a copy of the
  casing. The runner gets the cell zone `rotating` before the merge.

| Part | Cells | | Variant | Cells | Regions | Result |
|---|---|---|---|---|---|---|
| casing | 11,683 | | (a) AMI joints, conformal discs | 121,268 | 4 | OK; solver 10 steps |
| guide | 40,081 | | (a′) AMI joints, `guide_nc` (non-conformal discs) | 129,731 | 4 | OK; solver 10 steps |
| guide_nc | 48,544 | | (b) stationary joint stitched, conformal | 121,268 | 3 | OK; solver 10 steps |
| runner | 40,070 | | (b′) stitched, non-conformal | 129,731 | 3 | **Failed 1 mesh check**; solver stops |
| draft | 29,434 | | | | | |

- **Interface pairs:**
  - Sliding: `guide_out`↔`runner_in` → AMI1/AMI2, and `runner_out`↔`draft_in`
    → AMI3/AMI4.
  - Stationary joint in (a): `casing_out`↔`guide_in` → S1/S2 (cyclicAMI;
    the solver applies no motion to it).
  - In (a′) the joints are non-conformal (S2 and AMI1 have 2,244 faces,
    against 1,804 on the other side).
- **Stitching, (b) and (b′):** `stitchMesh -overwrite -integral casing_out
  guide_in`.
  - **(b):** the discs matched face for face (same cell size and offset), so
    the joint became internal and the parts formed one region.
  - **(b′):** stitchMesh exits 0 but leaves 144 faces in `casing_out` and
    creates 32 faces with skewness above 4 (max 5.63). The mesh is INVALID,
    and pimpleFoam stops with `Cannot find patchField entry for casing_out`.
  - **Conclusion:** separately meshed parts are not conformal in general, so
    stationary joints are made as cyclicAMI pairs.
- **stitchMesh writes `0/meshPhi`:** createPatch does not add the new
  patches to it. The next checkMesh therefore fails with
  `Cannot find patchField entry for AMI1 … file: 0/meshPhi`
  (`merged_stitch_05_checkMesh_with_meshPhi.log`, exit 1). Removing `0/`
  clears it.

### 3.5 R5 VAWT pole crossing the zone (owner decision 1: both)

Logs: `R5_pole/logs/`. The domain is the V0 SIMPLE domain (patches `inlet`,
`outlet`, `lateral_*`, `axial_*`).

| | Rotating (split) pole | Stationary pole in a zone hole |
|---|---|---|
| Zone | cylinder r 0.78, z ±0.66 (`searchableCylinder`) | annulus r 0.08–0.78 (`zone_annulus.stl`, regions `outer`, `inner`) |
| Pole in outer mesh | `pole` (outside the zone), wall | `pole` (whole length), wall |
| Pole in rotor mesh | `pole_rot` (inside the zone) | none |
| Cells outer / rotor / merged | 111,808 / 90,272 / 202,080 | 120,424 / 96,880 / 217,304 |
| Regions | 2 | 2 (the hole's fluid belongs to the outer mesh) |
| AMI pairs (faces) | AMI1 3,352 / AMI2 4,712: side and caps, caps with the pole's hole | AMI1 3,504 / AMI2 4,784 (side and caps); AMI3 2,592 / AMI4 4,480 (hole) |
| AMI sum(weights), all updates | **0.72–1.40** | 0.96–1.19 (hole pair about 1.00) |

- **Split pole:** `pole_rot` with `movingWallVelocity` is the pole turning
  with the rotor.
- **Same mesh, stationary pole:** with `pole_rot` set to `noSlip` the run
  also completes (`solve_rotating_static`). The zone's cells move, but the
  pole's surface maps onto itself because it is a surface of revolution about
  the rotation axis. This would not hold for a non-circular or off-axis
  tower.
- **Why the split pole's weights are low:** the pole is refined to level 3 in
  the outer mesh and level 2 in the rotor mesh, so the holes in the two cap
  surfaces differ. Not verified: matching the levels would raise them.
- **The hole method:** it adds a second interface and a thin stationary gap
  (here 0.05 m) that needs refinement (level 3 on the hole and the pole). In
  return the stationary pole never touches an interface (spec 3.1).

### 3.6 Region rule (spec 11)

Every merged mesh had exactly as many regions as separately meshed parts,
minus stitched joints: R3 2, R4 4 (AMI joints) or 3 (stitched), R5 2. A
stationary region inside a zone hole is not a separate region; it is part of
the outer mesh. checkMesh prints `Number of regions: N` with a `*` note, not
a failed check (as V0 R7).

---

## 4. Solver smoke test (R6) and boundary conditions that ran

Evidence only. `pimpleFoam`, 10 fixed time steps, each step 1° of rotation
(`dt = (π/180)/ω`). Logs: `*/logs/solve_*.log`, AMI weights per step in
`solve_*_AMIWeights1_AMIWeights.dat`.

| Run | Fluid, inlet, rotation | Model | Steps | Max Courant (step 10) | AMI sum(weights) | Time |
|---|---|---|---|---|---|---|
| R3 O-grid | air, 5 m/s, ω 60 rad/s (λ 6) | k-ω SST | 10 | 6.70 | 0.90–1.35 (+ warning, 3.3) | 10 s |
| R3 snappy | same | k-ω SST | 10 | 6.70 | 0.95–1.35 | 8 s |
| R3 O-grid | same | laminar | 10 | 7.01 | 0.90–1.35 | 7 s |
| R4 (a) | water, 1 m/s, ω 20 rad/s | k-ω SST | 10 | 3.43 | 0.96–1.00 | 10 s |
| R4 (a′) | same | k-ω SST | 10 | 3.43 | 0.96–1.00 | 10 s |
| R4 (b) | same | k-ω SST | 10 | 3.43 | 0.96–1.00 | 9 s |
| R4 (b′) | same | — | **0** | — | — | fatal at start |
| R5 split pole | air, 5 m/s, ω 19.2 rad/s (λ 2) | k-ω SST | 10 | 2.48 | 0.72–1.40 | 12 s |
| R5 split, static pole | same | k-ω SST | 10 | 2.48 | 0.72–1.40 | 14 s |
| R5 hole | same | k-ω SST | 10 | 2.48 | 0.96–1.19 | 15 s |

Inlet turbulence: k and ω from spec 9.2 with I = 5 %, L = 0.1 m (air) or
0.03 m (water). Continuity errors at step 10 were 1e-11 to 1e-9 (sum local).
A run that completes is not evidence of a correct solution (invariant 15).

**Patch types to OpenFOAM conditions (spec 7), as they ran on v2512:**

| Spec type | U | p | k | ω | ν_t |
|---|---|---|---|---|---|
| Inlet | `fixedValue` | `zeroGradient` | `fixedValue` | `fixedValue` | `calculated` |
| Outlet | `inletOutlet` (inletValue 0) | `fixedValue` 0 | `inletOutlet` | `inletOutlet` | `calculated` |
| Wall | `noSlip` | `zeroGradient` | `kqRWallFunction` | `omegaWallFunction` | `nutkWallFunction` |
| Rotating wall | `movingWallVelocity` | `zeroGradient` | `kqRWallFunction` | `omegaWallFunction` | `nutkWallFunction` |
| Symmetry or slip | `slip` | `slip` | `slip` | `slip` | `slip` |
| Interface | `cyclicAMI` via `#includeEtc "caseDicts/setConstraintTypes"` | same | same | same | same |

- **Rotation:** `constant/dynamicMeshDict`: `dynamicFvMesh
  dynamicMotionSolverFvMesh; motionSolverLibs (fvMotionSolvers);
  motionSolver solidBody; cellZone rotating; solidBodyMotionFunction
  rotatingMotion; origin; axis; omega`.
  - A positive `omega` turns the zone right-handed about `axis` (verified).
    `common/check_rotation.sh` compares each mesh point at time 0 and after
    the 10 steps. Every moved point turned +10.000° (±0.0004°) about +x
    (R3, ω 60) or +z (R4, ω 20; R5 hole, ω 19.2), the angle measured from
    y toward z and from x toward y. Output: `rotation_check.txt`.
  - So the app's rotation direction (spec 9.3) maps to the sign of `omega`
    with `axis` as given.
- **Pressure:** kinematic (m²/s²), from the field dimensions
  `[0 2 -2 0 0 0 0]`, as spec 9.1 states.
- **Fields are named per patch, not by patch group,** so every patch is
  listed. A missing patch is a fatal error at start (R4 b′).

### Sources of the borrowed settings

| Setting | Taken from (v2512) |
|---|---|
| `fvSchemes`: Euler; `grad(U) cellLimited Gauss linear 1`; `div(phi,U) Gauss linearUpwind grad(U)`; turbulence `Gauss upwind`; laplacian and snGrad `limited corrected 0.33` | `tutorials/incompressible/pimpleFoam/RAS/propeller/system/fvSchemes` |
| `div(phi,omega)`, `wallDist { method meshWave; }` | `etc/templates/inflowOutflowRotating/system/fvSchemes` |
| `fvSolution`: GAMG `pcorr`/`p`/`pFinal`, smoothSolver U/k(/ω), `PIMPLE { correctPhi no; nOuterCorrectors 2; nCorrectors 1; nNonOrthogonalCorrectors 0; }`, relaxation 1, `cache grad(U)` | `…/propeller/system/fvSolution` (ω added to the U/k entries) |
| `dynamicMeshDict` entries | `…/propeller/constant/dynamicMeshDict` (same motion in mixerVesselAMI2D) |
| `transportProperties` form | `…/propeller/constant/transportProperties` |
| `turbulenceProperties`: `RAS { RASModel kOmegaSST; }` | `etc/templates/inflowOutflowRotating/constant/turbulenceProperties` |
| `turbulenceProperties`: `simulationType laminar` | `…/laminar/mixerVesselAMI2D/mixerVesselAMI2D/constant/turbulenceProperties` |
| Inlet/outlet conditions, `noSlip`, `movingWallVelocity`, `kqRWallFunction`, `nutkWallFunction`, `#includeEtc "caseDicts/setConstraintTypes"` | `…/propeller/0.orig/*` |
| `omegaWallFunction`, k/ω inlet/outlet forms | `etc/templates/inflowOutflowRotating/0/omega`, `0/k` |
| `slip` on far-field sides | `tutorials/incompressible/lumpedPointMotion/building/steady/0.orig/include/environ` |
| `createPatchDict` cyclicAMI entries | `…/propeller/system/createPatchDict` (also V0 E1) |
| `AMIWeights` function object | `…/propeller/system/AMIWeights` |
| O-grid as blocks with `arc` edges (form only) | `…/mixerVesselAMI2D/system/blockMeshDict.m4` |

Not borrowed: time step (1°/step, chosen for the smoke test), inlet values,
domain sizes. These are test values.

---

## 5. Requirements found for later milestones

| # | Finding | Evidence | Milestone |
|---|---|---|---|
| G1 | An open imported domain meshes the outside with exit 0 and `Mesh OK.`. Before meshing: closedness of the union (spec 10, BLOCKING). After meshing: a background-block patch that still has faces is an ERROR (the region kept is the wrong one). | R2 `open_*`, `python_checks.json` | G2 |
| G2 | A binary STL given as named regions becomes one patch and nothing fails (spec 10, ERROR). Detect binary by the size rule, not by a leading "solid". | R2 `binary_*` | G2 |
| G3 | Region names: `geometry … regions { <solid> { name <patch>; } }`; type and level per region in `refinementSurfaces … regions { }`. One file per patch: the geometry name is the patch name. | R2, R4 dictionaries | G2 |
| G4 | Joints between stationary parts meshed separately are cyclicAMI pairs. `stitchMesh` only for conformal joints, and then `0/meshPhi` must be removed before later steps. | R4 (a′), (b′), `*_with_meshPhi` | G5 |
| G5 | Region rule: expected regions = separately meshed parts − stitched joints (generalises V0 R7 / V3 `REGION_COUNT_UNEXPECTED`). | 3.6 | G3, G5 |
| G6 | Each imported part is meshed on its own background box, ending a whole number of cells plus a half beyond the part, so no part face lies on a background plane. Whether a coincident face fails was not tested. | R4 dictionaries | G2, G5 |
| G7 | Cylinder domain: the snappy cut (R1 b) as the one domain path; the O-grid gave an AMI warning with a cut-out zone. | R1, R3 | G2 |
| G8 | AMI weights can drop to about 0.7 where a body crosses an interface with different resolution on the two sides (R5 split pole). Report min/max sum(weights) from the `AMIWeights` function object in the case checks; the acceptance threshold is not established here. | R5 `solve_rotating*` | G6 |
| G9 | 1° of rotation per step gave max Courant 6.7–7.0 on the HAWT mesh and 2.5–3.4 elsewhere. Time-step defaults need the Courant limit as well as degrees per step (spec 9.6, 10). | section 4 | G6 |
| G10 | Surfaces of revolution about the rotation axis (runner shroud, coaxial pole) may be stationary walls inside a rotating zone (`noSlip`); other surfaces there must be rotating walls. | R4 `runner_wall`, R5 static pole | G3, G6 |
| G11 | Commands still run from their case directory (V0 R9). The repository's own directory name ends in "processor", so `mergeMeshes` and `pimpleFoam` are started with `cd`. | `lib.sh` `in_case` | G3–G6 |
| G12 | v2512 blockMesh warns on stderr that `convertToMeters` is a 180-month-old keyword for `scale`; both are accepted. | `*_blockMesh.stderr.log` | — |

---

## 6. Owner decisions (spec section 16) and plan answers

| | Decision |
|---|---|
| 1 Pole | Prove both on synthetic geometry (done, R5); the owner chooses when the real J-blade is ready |
| 2 Solver run from the app | Not needed for G0 |
| 3 Francis inputs | Separate fluid-domain STLs: casing, guide vanes, runner, draft tube (R4) |
| 4 Single passage | Full wheel only for now; single-passage periodic is a later item |
| 5 Fluent export | Not required now; R7 skipped; later item (V0 Q7: zones and AMI do not survive `foamMeshToFluent`) |
| Conflict 1 | G0 solver steps are hand-run evidence only and never enter the application |
| Conflict 3 | V6 comes before G7; V7 waits on decision 4 |
| Tutorials | `openfoam2512-tutorials` installed; propeller and mixerVesselAMI2D used as reference (section 4) |

---

## 7. Waiting for real geometry (spec section 17)

Not verified until the real STLs arrive:

- Meshing settings, cell counts and quality for real HAWT blades, a Francis
  runner and a J-blade rotor (thin trailing edges, layers, y⁺).
- Real Francis passage files: closedness, region names, gaps where vanes and
  blades meet hub and shroud, the radial-inflow spiral casing, and how the
  parts' interface surfaces line up.
- The real pole/strut arrangement of the J-blade VAWT, and the owner's choice
  between the two pole methods.
- Units, rotation axis and rotation direction for each machine.

**G5 Francis results: synthetic geometry only.** The G5 pipeline meshed the
G0 R4 passage (spec section 23): conformal 133,319 cells, AMI 0.938–1.000;
non-conformal 146,114 cells, AMI 0.900–1.000. The joint-coincidence limits
(1 % area, half a cell) and the preset values (cells D/24, solid regions at
level 2) are unverified on real CAD.

**HAWT domain distances (spec 8): unverified.** The G0 HAWT domain (2D
upstream, 5D downstream, radius 2D) is a test value. No cited source for
preset distances has been recorded yet.

---

## 8. Later items

- Single-passage Francis with rotational periodic boundaries (decision 4).
- Fluent export for rotating machinery (decision 5).
- Interface built in blockMesh (mixerVesselAMI2D form), not run.
- Matching interface resolution on both sides of an AMI pair crossed by a
  body (R5 split pole weights).
