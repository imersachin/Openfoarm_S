# VAWT Mesh Generator — V0 Method Notes

| | |
|---|---|
| Milestone | V0, method proof and baseline (`docs/vawt_mesh_generator.md` section 18) |
| Target | OpenFOAM v2512 (openfoam.com / OpenCFD), Ubuntu 24.04 under WSL2 |
| Build | `OpenFOAM-2512`, build `_bd2b6720-20260127`, `label=32;scalar=64`, package `openfoam2512 2512.0-2` |
| Machine | 32 cores, 31 GB RAM (WSL2), Python 3.12.3 |
| Evidence | `tests/fixtures/vawt/v0/` — dictionaries that ran, every command log, exit codes |
| Reproduce | `PYTHON=<venv python> bash tests/fixtures/vawt/v0/run_all.sh` (267 s on this machine; 52 steps, the 9 non-zero exits are the intended failures) |

Everything below is from runs on this installation. Statements without a log
reference are marked as not verified.

---

## 1. Summary

1. Both candidate methods produce a valid rotating-zone mesh on v2512 for the
   fixture rotor: two meshes merged into a cyclicAMI pair (E1), and a single
   snappyHexMesh pass with the cylinder as a zoned surface (E2).
2. `CELL_ZONE` mode works only with the single-mesh method (E2a). The two-mesh
   method gives two disconnected regions.
3. The Fluent `.msh` written by `foamMeshToFluent` keeps no cell zones and
   writes the AMI patches as `pressure-outlet`. The rotating zone does not
   survive. This matters for owner decision 3.
4. Absolute layer sizing works (`relativeSizes false` + `firstLayerThickness`),
   but the V1 preset first layer (D/5000) adds no layers on the fixture rotor.
5. Two failures exit 0 and must be caught by checking results: a mesh point
   inside the rotor solid, and `createPatch` naming a missing patch.
6. Three defects in the existing generic workflow appear on v2512 (section 9).
   The existing real-OpenFOAM integration tests fail on this version.

---

## 2. Fixture and experiment geometry

The V1 fixture rotor (`tests/fixtures/vawt/rotor.py`): shaft plus four box
blades, axis z, centred at the origin, D = 1.04 m, H = 1.2 m, 176 triangles,
five closed bodies. `common/make_geometry.py` writes it plus an open copy and a
copy with blade body 2 inside-out.

All cases use the V1 SIMPLE preset values for this rotor:

| Item | Value |
|---|---|
| Domain | x −3.12…7.28, y ±1.56, z ±2.16 (−3D/+7D, ±1.5D, rotor ±1.5D); cell D/9; 90 × 27 × 38 cells |
| Patches | `inlet` (x min), `outlet`, `lateral_min/max` (y), `axial_min/max` (z) |
| Rotating zone | cylinder r 0.78 (1.5D/2), z ±0.66 (rotor ±0.05H), cell D/22 |
| Wake | x −1.56…6.76, y ±1.404, z ±1.8, level 1 (clamped as in V1) |
| Rotor surface | level 1–2, features level 2 (`includedAngle 150`), 3 layers |

Common files (`common/system/`): `controlDict`, `meshQualityDict` (same values
as the existing generator, plus a `FoamFile` header), and minimal `fvSchemes`
(empty sections) and `fvSolution` (empty). These were sufficient for every
command used.

---

## 3. Answers to section 10

### Q1 Target version

OpenFOAM v2512 (OpenCFD), installed from the official repository
(`https://dl.openfoam.com/repos/deb noble main`, served from SourceForge), in
WSL2 Ubuntu 24.04. Environment: `source /usr/lib/openfoam/openfoam2512/etc/bashrc`;
`WM_PROJECT=OpenFOAM`, `WM_PROJECT_VERSION=v2512`. All utilities used are in the
base `openfoam2512` package (no ParaView, no tutorials).

### Q2 Which method produces a valid interface

Both. Logs: `E1_ami_two_mesh/logs/`, `E2_single_mesh/logs/`.

| | E1 two meshes | E2 single mesh |
|---|---|---|
| Sequence | outer: blockMesh → snappyHexMesh; rotor: blockMesh → surfaceFeatureExtract → snappyHexMesh → topoSet; mergeMeshes → createPatch | blockMesh → surfaceFeatureExtract → snappyHexMesh; AMI: createBaffles |
| Source | spec section 10 first candidate | v2512 template `etc/templates/inflowOutflowRotating` |
| Cells | 608,926 (outer 487,630 + rotor 121,296) | 559,710 (zone 71,784) |
| Interface faces | AMI1 3,040 / AMI2 4,480 (non-conformal) | AMI1 = AMI2 = 3,776 (conformal) |
| AMI `sum(weights)` | source 0.991–1.331, target 0.995–1.371, mean 1.010 / 1.006 | 1.000–1.126, mean 1.0018 |
| Max skewness | 0.84 | 0.86 (CELL_ZONE), 2.59 after createBaffles |
| `CELL_ZONE` mode | not possible (two disconnected regions) | yes, the snapped mesh as is |
| Zone cell size | independent (D/22 here) | outer size / 2^n only (D/18 here) |
| Independent caching (spec 9.2) | yes: outer and rotor are separate cases | no: any change re-meshes everything |
| snappyHexMesh time | outer 28 s + rotor 24 s | 102 s (72 s of it layers) |
| Layers on rotor | 1.94 of 3, 62.9 % of target thickness | 1.80 of 3, 58 % |

All three meshes (E1 merged, E2a, E2b) report `Mesh OK.` without
`-allGeometry` and `Failed 1 mesh checks` (concave cells) with it
(`merged_flags_*.log`, `E2*_flags_none.log`; see section 9 D3). Cell counts
were identical in every repeated run.

E1 detail:
- The patch created by snappyHexMesh for a `searchableCylinder` is named after
  the geometry entry (`AMI_outer`, `AMI_rotor`); see `logs/*_boundary`.
- The rotor sub-case's block patch (`rotorBackground`) loses all its faces
  and is already absent after snappyHexMesh (`rotor_boundary` lists only
  `rotor` and `AMI_rotor`).
- The cell zone is created in the rotor sub-case before merging, with
  `boxToCell` over everything, so it is exactly the rotor mesh. It survives
  `mergeMeshes`: `rotating`, 121,296 cells (`merged_cellZones.head`).
- `mergeMeshes -overwrite <master> <add>` writes into the master case; the
  merged case starts as a copy of the outer case.
- AMI weight sums above 1 come from the two sides discretising the cylinder
  differently (outer level 1 of D/9 = D/18 vs rotor D/22). Not verified:
  matching the two sizes more closely would reduce them.

E2 detail:
- `refinementSurfaces` entry for the cylinder: `faceZone rotatingZone;
  cellZone rotating; cellZoneInside inside;` (template form).
- `createBaffles` with the template's dictionary (`internalFacesOnly true`,
  faceZone `rotatingZone`, master/slave cyclicAMI) converts the face zone into
  the pair. Max skewness rises to 2.59 (checkMesh still reports OK).

### Q3 How the outer mesh excludes the cylinder volume

The cylinder is a `refinementSurfaces` entry (`patchInfo { type patch; }`) and
`locationInMesh` is outside it. snappyHexMesh keeps only the region containing
the point, so the cylinder becomes the boundary patch `AMI_outer` (3,040 faces,
closed). Evidence: `E1_ami_two_mesh/outer/system/snappyHexMeshDict`,
`logs/outer_boundary`, `logs/outer_03_checkMesh.log`.

### Q4 CELL_ZONE mode

Single snappyHexMesh pass with the cylinder as a zoned surface (E2a): the mesh
has cell zone `rotating` (71,784 cells) and an internal face zone
`rotatingZone` (3,776 faces), one connected region. Evidence:
`E2_single_mesh/logs/cellzone_*`. This is the form the v2512 template uses for
MRF. Whether a solver accepts it is outside this project's scope (no
solver was run).

### Q5 Absolute layer sizing

Log: `E3_absolute_layers/logs/`. The effective `addLayersControls` of each
variant is saved as `<variant>_addLayersControls`.

| Variant | Entries | Result |
|---|---|---|
| E3a | `relativeSizes false; thicknessModel firstAndExpansion; firstLayerThickness 2.08e-4; expansionRatio 1.2; minThickness 1e-4` | accepted; **0 % of faces extruded** |
| E3b | same without `thicknessModel` | identical to E3a |
| E3c | `thicknessModel firstAndExpansion` without `firstLayerThickness` | exit 1: `Entry 'firstLayerThickness' not found in dictionary ".../addLayersControls"` |
| E3d | as E3a with `firstLayerThickness 2.46e-3` | 1.94 of 3 layers, 63 % of target, 74 % of faces extruded |

Required with `relativeSizes false`: `firstLayerThickness` (metres),
`expansionRatio`, `minThickness` (metres). `thicknessModel firstAndExpansion`
is optional and gives the same result (E3a = E3b). The annotated dictionary
(`etc/caseDicts/annotated/snappyHexMeshDict`) also lists `firstAndOverall`,
`finalAndExpansion`, `overallAndExpansion`, `firstAndRelativeFinal` (not run).

Why E3a adds nothing: D/5000 = 0.208 mm, three layers total 0.66 mm, next to
blade cells of about 11.8 mm (D/22/4). The layer check then finds 14,576 faces
with interpolation weight < 0.05 and removes every extrusion. E3d sets the
final layer to about 0.3 of the finest blade cell, the same ratio the relative
preset uses, and layers are added.

### Q6 Exit codes and output

Success: every step of E1, E2 and E3 (except E3c) exits 0 (`logs/exit_codes.txt`).
Failures (`E4_failures/logs/`):

| Case | Command | Exit | First error line |
|---|---|---|---|
| Mesh point on a background-cell edge (V1 preset point `(0.65 0 0)`) | snappyHexMesh | 1 | `Point (0.65 0 0) is not inside the mesh or on a face or edge.` |
| Mesh point inside a blade | snappyHexMesh | **0** | none; meshes the blade interior (4,536 cells), checkMesh `Mesh OK.`; log: `found point ... in global region 5 out of 7 regions` |
| `system/blockMeshDict` missing | blockMesh | 1 | `cannot find file ".../system/blockMeshDict"` |
| STL in surfaceFeatureExtractDict missing | surfaceFeatureExtract | 1 | `No surfaces specified/found for entry: rotor.stl` |
| `.eMesh` referenced but not extracted | snappyHexMesh | 1 | `Could not open ".../constant/triSurface/rotor.eMesh"` |
| createPatch source patch missing | createPatch | **0** | `FOAM Warning : Cannot find any patch names matching AMI_outer`; empty patches created |
| createBaffles faceZone missing | createBaffles | 1 | `Cannot find faceZone rotatingZone` |
| mergeMeshes added case without mesh | mergeMeshes | 1 | `Cannot find file "points" in directory "polyMesh"` |
| checkMesh without mesh | checkMesh | 1 | same |
| foamMeshToFluent without mesh | foamMeshToFluent | 1 | same |

Other observations:
- checkMesh exits 0 when it reports failed checks (`Failed 1 mesh checks.`
  with exit 0 in E1/E2). Validity must come from the parsed output.
- Fatal errors go to stderr (`*.stderr.log`); the progress log goes to stdout.
- `checkMesh -meshQuality` reads `system/meshQualityDict` as a file of its own
  and fails without a `FoamFile` header (first E1 run; see section 9).

### Q7 foamMeshToFluent

Writes `<case>/fluentInterface/<case directory name>.msh` (ASCII). Zone
sections of each file are kept in `logs/*msh_zones.txt` (the files themselves
are 106–116 MB for 0.56–0.61 M cells and are not kept).

| | Cell zones in `.msh` | Interface patches | Other patches |
|---|---|---|---|
| E1 merged (AMI) | one: `fluid` | `AMI1`, `AMI2` as `pressure-outlet` | `rotor` as `wall`; the six domain patches as `pressure-outlet` |
| E2a (CELL_ZONE) | one: `fluid` | face zone not written | as above |
| E2b (AMI) | one: `fluid` | `AMI1`, `AMI2` as `pressure-outlet` | as above |

The OpenFOAM cell zone `rotating` is not in any `.msh`. Every non-wall patch
becomes `pressure-outlet`. Not verified (no Fluent available): whether Fluent
can recover the rotating region of the AMI meshes by separating disconnected
regions. For `CELL_ZONE` the zone cannot be recovered from the file. If the
`.msh` is the primary deliverable (decision 3), the export needs more than
`foamMeshToFluent`.

### Q8 surfaceCheck

Log: `E6_surface_check/logs/`. surfaceCheck exits 0 on the closed, open and
inside-out rotor. It reports closed/open and the number of parts, which the
Python validator also reports. It does not identify the inside-out body: the
closed and the inverted rotor give the same output (`More than one normal
orientation`, 5 zones), because each body is a separate zone. The Python
per-body check finds the inverted body (`python_checks.json`). surfaceCheck
also writes `.obj`/`.vtp` files into the working directory. Conclusion: it adds
nothing the Python validator lacks for these defects; not used.

---

## 4. Method decision (owner)

The spec's section 9.2 table ("outer and rotor meshes do not depend on each
other") assumes the two-mesh method. The evidence supports two options:

- **A. E1 for `AMI`, E2a for `CELL_ZONE`.** Keeps independent caching and
  independent zone cell size for the main mode; `CELL_ZONE` uses the only
  method that supports it. Cost: two meshing paths to build and test.
- **B. E2 for both modes.** One path, the upstream template's method,
  conformal interface. Cost: section 9.2 no longer holds (every meshing change
  re-meshes everything), zone cell size limited to outer / 2^n, and the whole
  mesh is layered (slower).

Recommendation: A, because section 9.2's independent caching is named as the
main source of saved time, and only A keeps it. B is the smaller build if
caching of the two regions matters less than one code path.

**Decided by the owner after V0: A.** Implemented in V2 (`vawt/case_generator.py`);
the generated AMI and single-mesh `CELL_ZONE` cases mesh on v2512
(`tests/integration/test_vawt_case_generation.py`).

---

## 5. Requirements found for later milestones

| # | Finding | Evidence | Milestone |
|---|---|---|---|
| R1 | Mesh points must not lie on background-cell faces or edges. The V1 presets put both points on the axis planes; with even cell counts these are cell faces, and the inner point was rejected. The generator (or presets) must offset points by a fraction of a cell. | E4 `location_on_cell_edge` | V2 (V1 presets) |
| R2 | Absolute layers: warn when the final layer is far below the local cell size; the D/5000 preset produces no layers on the fixture. | E3a vs E3d | V1 validation follow-up |
| R3 | After snappyHexMesh, confirm the kept region is the intended one (interface patch present with faces, cell zone size, bounds), because a mesh point inside the rotor exits 0. V1's check catches this before meshing only for closed surfaces. | E4 `location_in_rotor` | V3 |
| R4 | After createPatch, confirm each created patch has faces; it exits 0 when its source patch is missing. | E4 `createPatch_missing_source_patch` | V3 |
| R5 | Do not judge validity from checkMesh's exit code; it exits 0 with failed checks. | E1/E2 checkMesh logs | V3 |
| R6 | Every sub-case needs `system/fvSchemes` and `system/fvSolution` (empty sections suffice) and a `FoamFile` header in `meshQualityDict`. | E1 first run; section 9 | V2 |
| R7 | Expect `Number of regions: 2` for an AMI mesh; it is not an error. | `merged_flags_none.log` | V3 |
| R8 | Integration point 7: the ASCII STL artifact is costly for large rotors (section 7). | `baseline/stl_baseline.json` | owner, before V3 |
| R9 | Run OpenFOAM commands from the case directory. v2512 `mergeMeshes` appends `/processor` to the master case path when the working directory's name ends in "processor" (e.g. `openforam_preprocessor`, `my_preprocessor`; not `my_tool` or `processor_runs`) and then fails: `Cannot find file "points"`. `openfoam/runner.py` already uses `cwd=case_root`. | found in V2 (`test_vawt_case_generation.py`) | V3 |

Done in V2: R1 (presets offset the mesh points; `MESH_POINT_ON_CELL_FACE`
ERROR against the actual background grids), R2 (`LAYERS_TOO_THIN_FOR_CELLS`
WARNING, using the zone's effective cell size), R6 (every sub-case gets
`fvSchemes`, `fvSolution` and a `meshQualityDict` with header). Also in V2:
`RESERVED_PATCH_NAME` (BLOCKING) for names the generated mesh uses itself.

---

## 6. Patch and zone names used

`inlet`, `outlet`, `lateral_min`, `lateral_max`, `axial_min`, `axial_max`
(V1 defaults), `rotor` (wall), interface pair `AMI1`/`AMI2` (`cyclicAMI`,
`matchTolerance 0.0001`, `transform noOrdering`), cell zone `rotating`,
face zone `rotatingZone` (E2).

---

## 7. Baselines (spec section 14.2 and integration point 7)

### Current dashboard (`baseline/dashboard_baseline.json`)

`streamlit.testing` AppTest, server-side wall time of one rerun, median of 3,
rotor of 100,352 triangles (`baseline/measure_dashboard.py`). STL parses are
counted at `trimesh.load_mesh`.

| Interaction | Time | STL parses |
|---|---|---|
| First render | 0.073 s | 0 |
| Open saved project | 0.026 s | 0 |
| Edit one field (acceptance limit / project name) | 0.025 / 0.027 s | 0 |
| Prepare case | 1.565 s | 2 |
| Show geometry view | 0.522 s | 2 |
| Edit one field while the geometry view is open | 0.549 s | 2 |

All 11 tabs run on every rerun, so switching tabs is not a separate server
interaction. Once the geometry view is open it is rebuilt, with two STL
parses, on every later interaction. Streamlit 1.65 warns that
`use_container_width` (used by the dashboard) is deprecated.

### Geometry artifact (`baseline/stl_baseline.json`)

Binary source STL, `baseline/measure_stl.py`, median of 3.

| Step | 100,352 triangles | 999,424 triangles |
|---|---|---|
| Source size (binary) | 5.0 MB | 50.0 MB |
| Artifact size (ASCII) | 31.6 MB | 312.3 MB |
| Import source (binary) | 0.038 s | 0.417 s |
| Transform | 0.009 s | 0.072 s |
| Encode ASCII | 0.701 s | 7.086 s |
| Encode + write artifact | 0.667 s | 7.068 s |
| Write-if-changed when unchanged | 0.654 s | 7.171 s |
| Re-import artifact (ASCII) | 0.326 s | 3.561 s |
| Hash source / artifact | 0.002 / 0.014 s | 0.023 / 0.144 s |

The write-if-changed check encodes the whole artifact even when nothing
changed. At 1 M triangles one geometry step costs about 10 s and the artifact
is 6.2 times the source size. Changing the format needs owner approval
(spec 3.3 point 7).

---

## 8. Owner decisions (spec section 19)

| # | Decision | Status |
|---|---|---|
| 1 | Version and platform | **v2512 (OpenCFD), WSL2 Ubuntu 24.04** (owner, V0) |
| 2 | `CELL_ZONE` in the first release | In configuration; UI only once proven (owner, V1). E2a now proves the mesh; exposing it is the owner's call. |
| 3 | `.msh` or OpenFOAM case as primary deliverable | **Open.** See Q7: the `.msh` loses the rotating zone. |
| 4 | Inlet on the minimum face of the flow axis | Confirmed (owner, V1) |
| 5 | Separate entry point | `app/vawt_app.py` (architecture sections 3, 18) |
| 6 | Largest rotor and cell count | **Open.** Measured at 100k and 1M triangles (section 7). |
| new | Method: A or B (section 4) | **A** (owner, after V0) |
| new | Existing-workflow defects (section 9): fix now, or in V3 | D1–D3 fixed on `fix/openfoam-v2512-meshing`; D4, D5 left as known limitations (owner) |
| new | ASCII artifact format for large rotors (R8) | **Open**; may stay open until V3/V7 (owner) |

Decisions 3 and 6 may also stay open until V3/V7 (owner, before V2).

---

## 9. Defects in the existing workflow on v2512 (not VAWT code)

Found while running the existing integration tests and the experiments. Not
changed in V0 (no application code; the spec forbids changing the generic
workflow without approval).

| # | Defect | Evidence | Effect |
|---|---|---|---|
| D1 | `OpenFOAMMeshCaseGenerator` writes no `system/fvSchemes` or `system/fvSolution`; snappyHexMesh v2512 stops: `cannot find file ".../system/fvSchemes"`. | `pytest -m openfoam tests/integration`: 2 failed | Generic meshing fails on v2512 |
| D2 | The generated `meshQualityDict` has no `FoamFile` header; `checkMesh -meshQuality` stops: `problem while reading header for object meshQualityDict`. | first E1 run (fixed in `common/system/meshQualityDict`) | checkMesh step fails once D1 is fixed |
| D3 | `checkMesh -allGeometry` reports `***Concave cells (using face planes)` on ordinary snappyHexMesh meshes (`Failed 1 mesh checks`); `mesh/validator.py` then marks the mesh INVALID. Without `-allGeometry` the same mesh is `Mesh OK.` | `E1.../merged_flags_*.log` | Every snappy mesh reported invalid |
| D4 | `mesh/parser.py` reports checkMesh's patch-group summary row `".*"` as a patch. | `test_v0_checkmesh_logs.py` (strict xfail) | Extra fake patch in reports |
| D5 | `use_container_width` is deprecated in Streamlit 1.65. | dashboard baseline stderr | Warning now; error after removal |

---

## 10. Known issues

**Resolved:** fixed on `fix/openfoam-v2512-meshing` (D1–D3), which is merged
into `v2-vawt-case-generation`; both tests pass there. The record below is
kept as it was found.

Two existing tests fail on OpenFOAM v2512. They also fail on a clean checkout
of `main` (`7b623e9`, without any VAWT or V0 files), so V0 did not cause them;
before V0 they were skipped because no OpenFOAM was installed.

| Test | Result |
|---|---|
| `tests/integration/test_real_openfoam.py::test_full_pipeline_with_real_openfoam[False]` | FAILED |
| `tests/integration/test_real_openfoam.py::test_full_pipeline_with_real_openfoam[True]` | FAILED |

Exact error, both tests (`case/logs/03_snappyHexMesh.stderr.log`; the pipeline
reports `COMMAND_FAILED`, `snappyHexMesh exited with code 1.`):

```text
--> FOAM FATAL ERROR: (openfoam-2512)
cannot find file "<case>/system/fvSchemes"
```

Likely cause: defect D1 (section 9). `OpenFOAMMeshCaseGenerator`
(`mesh/generator.py`) writes `controlDict`, `blockMeshDict`,
`snappyHexMeshDict`, `meshQualityDict` and, when enabled,
`surfaceFeatureExtractDict`, but no `system/fvSchemes` or
`system/fvSolution`, which snappyHexMesh v2512 reads. blockMesh and
surfaceFeatureExtract succeed in the same runs. Once D1 is fixed, D2 (the
`meshQualityDict` header, which `checkMesh -meshQuality` needs) and D3
(`-allGeometry` concave cells marking the mesh INVALID) are expected to fail
the same tests next; this is inferred from the V0 runs, not yet observed in
these tests. The engine is not changed in V0; the fix is planned on a
separate branch.

---

## 11. Open risks

- The fixture rotor is boxes and a cylinder. Thin trailing edges, curved
  blades and struts may snap and layer differently; layer coverage (58–63 %)
  is already below target on the fixture.
- No solver was run: AMI and cell-zone meshes are checked by checkMesh only.
- Fluent import is not verified (no Fluent available).
- Timings are from one machine and serial runs.
