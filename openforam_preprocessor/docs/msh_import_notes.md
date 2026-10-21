# .msh Import (M1-MSH) — E0 Method Notes

| | |
|---|---|
| Milestone | M1-MSH: E0, evidence before application code (owner, answer 6); E6 after the owner's review; M1b, the import in the application (section 8) |
| Target | OpenFOAM v2512 (openfoam.com), build `_bd2b6720-20260127`, package `openfoam2512 2512.0-2`, Ubuntu 24.04 under WSL2 |
| Gmsh | Python package `gmsh==4.15.2` in `/root/venvs/ofpp` (Python 3.12.3); apt packages `libglu1-mesa 9.0.2-1.1build1`, `libxft2 2.3.6-1build1`, `libopengl0 1.7.0-1build1` (owner, answer 1) |
| Geometry | **Synthetic only.** No real Fluent or Gmsh files from users yet (owner, answer 2) |
| Evidence | `tests/fixtures/msh/e0/`: the dictionaries that ran, every command log, exit codes, boundary files, zone names, file heads |
| Reproduce | `PYTHON=<venv python with gmsh> bash tests/fixtures/msh/e0/run_all.sh` (about 33 s on this machine, 93 commands) |
| Read back | `tests/unit/mesh_import/test_m1_e0_logs.py` (38 tests) and `test_m1_e6_logs.py` (E6, added after the owner review), no OpenFOAM needed |

Every statement below is from a run on this installation unless it is marked
**not tested**. Exit codes are in each experiment's `logs/exit_codes.txt`.

---

## 1. Summary

1. **Gmsh ASCII converts directly, 2.2 and 4.1 alike.** `gmshToFoam` reads
   MSH 2.2 and MSH 4.1 ASCII and gives the same mesh (E1). **Binary MSH fails
   in either version** ("Can only read ascii msh files."). Re-saving with the
   gmsh package as MSH 2.2 ASCII fixes binary files.
2. **Gmsh without physical groups converts silently.** 2.2: exit 0, one patch
   `patch0`, cell zone `cellZone_0`, checkMesh OK. 4.1: exit 1 ("Unhandled
   element 15", the point elements Gmsh writes when nothing is grouped); after a
   re-save to 2.2, the same silent `patch0`. Faces left out of every group go to
   `defaultFaces`, also with exit 0. MSH 1 keeps no names (`patch0`…`patch2`).
3. **Fluent round trip matches** (E2): patch names, face counts, OpenFOAM types
   (`patch`/`wall`) and cell counts are identical through `foamMeshToFluent` →
   `fluent3DMeshToFoam` and → `fluentMeshToFoam`. The source cell zone is lost
   (already known, V-notes Q7): `fluent3DMeshToFoam` writes one zone `fluid-1`
   over all cells, `fluentMeshToFoam` none, even with `-writeZones`. **E6: the
   loss is `foamMeshToFluent`'s**, which writes all cells as one fluid section;
   `fluent3DMeshToFoam` keeps every fluid cell section as its own cell zone.
4. **`fluentMeshToFoam` silently removes wall baffles** (E3): the `plate` and
   `plate-shadow` walls disappear and their faces become interior faces; exit 0,
   checkMesh OK. `fluent3DMeshToFoam` keeps both walls, and checkMesh reports
   "Number of identical duplicate faces (baffle faces): 50".
5. **A rotating case from an imported mesh works end to end** (E4): Gmsh
   volumes become cell zones, the two copies of the zone surface become an AMI
   pair with `createPatch`, checkMesh and the G7 folder checks pass, the G6 case
   setup writes the case, and `pimpleFoam` runs 10 steps (AMI sum(weights)
   1.00–1.03). Case setup leaves every mesh file byte-identical (invariant 14).
   The same mesh through Fluent has one cell zone (`fluid-1`) and two plain
   patches: no rotating zone, because the export wrote one cell section (E6).
6. **A truncated Fluent file converts with exit 0 to a broken mesh** (E5):
   `fluent3DMeshToFoam` writes 538 of 2000 cells; checkMesh also exits 0 while
   reporting "Failed 9 mesh checks". Exit codes are not evidence of success.
7. **Reading the box from an ASCII file is cheap** (E5): 0.78 s for a 185 MB
   Fluent file (1,030,301 nodes), against 3 s for `fluent3DMeshToFoam`. Binary
   files are not read raw (answer 4: convert once at scale 1 instead).
8. **Scaling:** `fluent3DMeshToFoam -scale 0.001` and conversion at scale 1
   followed by `transformPoints -scale (0.001 0.001 0.001)` give identical points
   (E2). The second is what answer 4 asks for.

---

## 2. Results

### 2.1 E1 Gmsh (plan E0.1, E0.2)

`common/make_gmsh.py box`: x 0…1, y and z 0…0.5 m, tetrahedra; groups `inlet`,
`outlet`, `walls`, volume `fluid`. Every converted mesh: 2842 cells.

| File | gmshToFoam | Patches (faces) | Cell zones |
|---|---|---|---|
| 2.2 ASCII | 0 | inlet 118, outlet 118, walls 872 | fluid 2842 |
| 4.1 ASCII | 0 | same | same |
| 4.1 ASCII → re-saved 2.2 | 0 | same (byte-identical file to the 2.2 write) | same |
| 4.1 binary → re-saved 2.2 | 0 | same | same |
| 2.2 binary, 4.1 binary | **1**, "Can only read ascii msh files." | — | — |
| 2.2, no groups | **0** | patch0 1108 | cellZone_0 2842 |
| 4.1, no groups | **1**, "Unhandled element 15 … Perhaps you created a higher order mesh?" | — | — |
| 4.1, no groups → re-saved 2.2 | 0 | patch0 1108 | cellZone_0 2842 |
| 2.2, walls not grouped | **0** | inlet 118, outlet 118, **defaultFaces 872** | fluid 2842 |
| MSH 1 (`$NOD`, no `$MeshFormat`) | 0 | patch0 118, patch1 118, patch2 872 | cellZone_0 2842 |
| missing file | **134**, core dump ("Cannot dereference nullptr") | — | — |

Every patch from `gmshToFoam` has type `patch`: types are set afterwards (as
the G4 assembly does with `foamDictionary`).

### 2.2 E2 Fluent round trip (plan E0.3, E0.6)

blockMesh box 20 × 10 × 10 (2000 cells), `inlet`/`outlet` (`patch`), `walls`
(`wall`), cell zone `half` (1000 cells) → `foamMeshToFluent` (ASCII, 334 kB).

| Case | Patches (type, faces) | Cell zones | checkMesh |
|---|---|---|---|
| source | inlet patch 100, outlet patch 100, walls wall 800 | half 1000 | OK |
| `fluent3DMeshToFoam` | same | **fluid-1 2000**; face zone interior-1 5500 | OK, 2000 cells |
| `fluentMeshToFoam` | same | **none** | OK, 2000 cells |
| `fluentMeshToFoam -writeZones` | same | **none** | OK |

The `.msh` zone sections (`(39 …)`) carry Fluent types: `pressure-outlet` for
inlet and outlet, `wall` for walls. `foamMeshToFluent` writes every non-wall
patch as `pressure-outlet`, so real files will carry more types (velocity
inlet, interface, symmetry) than this test shows: **not tested on real files**.

Scaling: the box written in mm (1000 × 500 × 500) and converted at scale 1
has the box (0 0 0)(1000 500 500). `-scale 0.001` and `transformPoints` after
conversion give points identical to the source (difference 0).

### 2.3 E3 Wall baffles (plan E0.4)

An approximation of a Fluent wall / wall-shadow pair: `createBaffles` on a face
zone (50 faces at x 0.5) → walls `plate` and `plate-shadow` → Fluent → back.
The `.msh` has two `wall` zones, `plate` and `plate-shadow`.

| | Patches | checkMesh | `mergeOrSplitBaffles -detectOnly` |
|---|---|---|---|
| source | … plate wall 50, plate-shadow wall 50 | OK, 50 baffle faces | 100 duplicate faces |
| `fluent3DMeshToFoam` | … plate wall 50, plate-shadow wall 50 | OK, 50 baffle faces | 100 duplicate faces |
| `fluentMeshToFoam` | **no plate patches** | OK, no baffle faces | 0 |

Real Fluent shadow zones (`wall-shadow` naming, shadow face threads):
**not tested on real files**.

### 2.4 E4 Rotating case (plan E0.5)

`common/make_gmsh.py rotating` (MSH 4.1 ASCII): box x −1…3, y and z −1…1 m;
cylinder zone r 0.5, x −0.2…0.2 around a block `rotor`; two volumes that are
not fragmented, so each keeps its own copy of the cylinder surface (groups
`ami_stat`, `ami_rot`).

| Step | Result |
|---|---|
| gmshToFoam | 24197 cells; zones stationary 23408, rotating 789; 9 patches, all `patch`; checkMesh OK, 2 regions |
| `foamDictionary` rotor → wall; `createPatch` | AMI1 (from ami_stat) and AMI2 (from ami_rot), cyclicAMI, 328 faces each |
| checkMesh | OK; AMI1 and AMI2 "closed singly connected"; 2 regions |
| G7 `check_mesh_folder` (strict limits) | AMI pair (AMI1, AMI2), 2 regions, VALID, WITHIN_LIMITS, no issues |
| G6 case setup | ω 31.4159 rad/s (300 rpm), radius 0.5, zone `rotating`; 11 files; mesh files unchanged; case mesh a byte-identical copy |
| pimpleFoam | 10 steps, Courant max 0.84, AMI sum(weights) 1.00–1.03, End |
| same mesh → Fluent → `fluent3DMeshToFoam` | one zone fluid-1 24197; AMI1/AMI2 as plain `patch` (in the `.msh`: `pressure-outlet`); 2 regions |

The case setup configuration used a box domain and a cylinder zone matching
the Gmsh geometry, because no imported-mesh configuration exists yet (M1b).

A Fluent file with several cell zones (a rotating fluid zone) and interface
zones: **not tested on real files**; a hand-edited two-zone file is E6.

### 2.6 E6 Where the Fluent cell zone is lost (owner review)

The E4 `foamMeshToFluent` export (`E4/ami/fluentInterface/ami.msh`) has one
cell section and one fluid name, although the OpenFOAM mesh had two zones
(stationary cells 0…23407, rotating 23408…24196):

```
(12 (0 1 5e85 0 0))
(12 (1 1 5e85 1 0)
(39 (1 fluid fluid-1)())
```

`common/split_fluent_cells.py` splits that section by hand at cell 23408
(sections `(12 (1 1 5b70 1 0)` and `(12 (14 5b71 5e85 1 0)`, names
`stationary` and `rotating`); nodes and faces are unchanged.

| Step | Result |
|---|---|
| `fluent3DMeshToFoam` | exit 0; "CellGroup: 1 start: 0 end: 23407", "CellGroup: 20 start: 23408 end: 24196"; cell zones **stationary 23408, rotating 789** |
| checkMesh | OK, 24197 cells, 2 regions |

**`foamMeshToFluent` is responsible; `fluent3DMeshToFoam` keeps cell zones.**
Per the owner's rule, the plan does not change: the region-to-cellZone helper
stays on the later list. A real Fluent rotor/stator file: **not tested on real
files**.

### 2.5 E5 Bad inputs (plan E0.7) and cost of the raw box

| Input | Converter | Exit | Message |
|---|---|---|---|
| Fluent cut to half | fluent3DMeshToFoam | **0** | none; 538 of 2000 cells, checkMesh "Failed 9 mesh checks" (exit 0) |
| Fluent cut to half | fluentMeshToFoam | 134 | "bad size -1", core dump |
| HDF5 signature (`.msh.h5`, `.cas.h5`) | fluent3DMeshToFoam | 1 | "Do not understand characters: H" |
| empty | fluent3DMeshToFoam | 1 | "Mesh is not 3D, dimension of grid: 0" |
| empty | gmshToFoam | 134 | core dump |
| Gmsh file | fluent3DMeshToFoam | 1 | "Do not understand characters: $" |
| Fluent file | gmshToFoam | 1 | "expected word, found … punctuation '('" |
| missing | fluent3DMeshToFoam | 1 | "not found" |
| binary Fluent | — | — | **not tested on real files** (no way to write one here) |

Raw box (`common/raw_bbox.py`, a prototype): the 1,000,000-cell blockMesh
exported to Fluent is 185 MB; reading its box took 0.78 s, converting it 3 s.
Binary MSH is not read raw ("binary MSH").

---

## 3. Requirements found for M1b

| # | Finding | Requirement |
|---|---|---|
| 1 | ASCII 2.2 and 4.1 convert directly; binary fails | Binary Gmsh (any version) is re-saved as 2.2 ASCII when gmsh is installed. See section 5, question 1, for ASCII 4.x |
| 2 | No physical groups converts silently (2.2) | Detect from the file: no `$PhysicalNames` (or no tagged entities) → BLOCKING `GMSH_NO_PHYSICAL_GROUPS` before converting |
| 3 | Ungrouped faces → `defaultFaces`, exit 0 | After converting: a `defaultFaces` patch with faces → BLOCKING `GMSH_UNGROUPED_FACES` (the fix: group every boundary surface) |
| 4 | MSH 1 loses names | `$NOD` → BLOCKING `MSH_FORMAT_UNSUPPORTED` (re-save as 2.2) |
| 5 | `gmshToFoam` and `fluentMeshToFoam` core-dump on some inputs | Check the file before converting (exists, not empty, detected format); never show a stack trace as the reason |
| 6 | `fluentMeshToFoam` drops baffles silently | Use `fluent3DMeshToFoam` for 3D Fluent meshes. See section 5, question 2 |
| 7 | Truncated file: exit 0, broken mesh | Compare the converted cell count with the header declaration (`(12 (0 1 <last> 0))`) and require checkMesh to pass; otherwise BLOCKING `MSH_CONVERT_FAILED` |
| 8 | Baffles are visible in checkMesh | `MSH_BAFFLE_PAIR` from "identical duplicate faces (baffle faces)" plus the two patches that share them |
| 9 | `foamMeshToFluent` writes one cell section; `fluent3DMeshToFoam` keeps every section (E6) | Rely on the file's own cell zones; one zone over all cells for a rotating machine → BLOCKING `MSH_NO_CELL_ZONE` (answer 5) |
| 10 | All Gmsh patches are `patch` | Types come from the patch table and are set after conversion (`foamDictionary`), never by case setup |
| 11 | ASCII raw box is cheap | Raw box for ASCII Fluent and Gmsh; binary → convert once at scale 1 (answer 4) |
| 12 | Both scalings are identical | Convert at scale 1, `transformPoints` after the units are confirmed; the same conversion is promoted (answer 4) |

---

## 4. Owner answers to the plan (recorded)

1. gmsh pinned in `requirements.txt` (optional extra `msh` in `pyproject.toml`)
   and installed after the apt list was shown. The proxy stays in the shell
   environment only. Without gmsh, a 4.x file is BLOCKING with "re-save as MSH
   2.2"; that path gets a test.
2. No real Fluent samples yet: binary, wall/wall-shadow and interface cases are
   "not tested on real files" (section 6).
3. Schema bump accepted: existing projects mesh byte-identically after
   migration (tested); `imported_mesh` and STL bodies are mutually exclusive
   (a validation issue); the source hash marks the project stale when the file
   changes; the converter name and OpenFOAM version are recorded; the zone
   radius is shown as "computed from the zone".
4. Box: raw read only when cheap; otherwise convert once at scale 1 into a
   scratch folder ("from the converted mesh"), then `transformPoints` and
   promote that conversion; the scratch folder is never verified.
5. No cell zone: BLOCKING only, with the fix (a Physical Volume for the
   rotating region). The region-to-cellZone helper is on the later list.
6. Stop after E0 for review.

---

## 5. Owner decisions after the E0 review

1. ASCII MSH 2.2 and 4.1 convert directly, with or without gmsh. Only binary
   files need gmsh (re-save as 2.2 ASCII); binary with gmsh missing is
   BLOCKING. No physical groups is BLOCKING either way; MSH 1 is BLOCKING
   (no patch names to recover).
2. `fluent3DMeshToFoam` only; a 2D Fluent file is BLOCKING.
3. `defaultFaces` is BLOCKING; the message gives the face count and the
   bounding box of those faces, so they can be found in Gmsh.
4. checkMesh "Failed N mesh checks" is a failure whatever the exit code.
5. Files are checked before converting (not empty, valid header), so no
   converter crash (exit 134) reaches the user.
6. Where the Fluent cell zone is lost: E6 (section 2.6). `fluent3DMeshToFoam`
   keeps zones, so no change.

---

## 6. Not tested on real files (owner, answer 2)

- Binary Fluent meshes (`(2010 …`/`(3010 …` sections).
- Real Fluent wall / wall-shadow pairs.
- Fluent interface zones (sliding mesh). Several fluid cell zones: only a
  hand-edited `foamMeshToFluent` file (E6).
- Fluent zone types other than `wall` and `pressure-outlet`.
- Very large real files (GB): only the 185 MB synthetic file was timed.

## 7. Later items

- "Make a cellZone from region N" (`topoSet` + `setsToZones`): future list,
  not this milestone (owner, answer 5).
- A UI for the import (M1c): this milestone is the core (M1b).
- ~~Flow-rate inlets on an imported mesh~~: done in M1c (section 10.1).
- STL meshes' AMI range (owner, M1c): measure the sum(weights) STL meshes
  actually produce, then tighten their 0.85-1.5 range with that evidence.
- A Courant estimate for an imported mesh: it has no configured cell size, so
  none is given (the solver's adjustable step still limits the Courant number).

---

## 8. M1b: the import in the application

Package `machines/msh_import/`; the run is a branch of `MachinePipeline` (same
run lock, status file, run record, artifact manifest, environment check and
resource preflight). Evidence: `tests/unit/mesh_import/test_m1_*.py` (fake
OpenFOAM, `tests/fakes_msh.py`) and `tests/integration/test_m1_msh_real.py`
(OpenFOAM v2512). Test meshes: `tests/fixtures/msh/m1/` (coarse versions of the
E0 geometry, 4–246 kB each; `make_fixtures.sh` rebuilds them byte-identically).

| Module | Does |
|---|---|
| `detect.py` | Format from the content (never the extension), version, ASCII or binary, dimension, patch and cell zone names, Fluent zone types and declared cell count, the raw box (ASCII, streamed in batches of 200,000 rows). Every pre-conversion finding about the file |
| `validation.py` | The project checks before any command (section 8.2); the file is read once per version (path, size, modification time) |
| `cases.py` | `cases/imported/` (the converter's case), `cases/merged/` (createPatchDict, amiWeightsDict), the commands |
| `resave.py` | Binary or unproven Gmsh versions to MSH 2.2 ASCII, run as its own process |
| `checks.py` | What the converter wrote: cells against the declared count, `defaultFaces` with its count and box, patches lost, cell zones; baffles from checkMesh; the zone radius computed from the zone |
| `pipeline.py` | The run (below) and the scratch conversion for a box |
| `inspection.py` | What the UI shows before importing: the box and its source, suggested types and AMI pairs, patch areas and centroids as pair evidence |

### 8.1 The run

```text
validate (no OpenFOAM command on any stopping finding)
-> machine_import_mesh: re-save (binary Gmsh) -> fluent3DMeshToFoam | gmshToFoam
   (scale 1) -> transformPoints -scale (f f f) (units other than m) -> checks
   -> reports/mesh_import.json                                   cases/imported/
-> machine_assemble: copy -> createPatch (AMI pairs <patch>_ami, renamed
   patches) -> foamDictionary (every other patch's type)          cases/merged/
-> machine_check_mesh: checkMesh -allTopology -meshQuality, postProcess AMIWeights
-> machine_validate_mesh -> machine_case_setup (with physics)          case/
```

- Cached as every machine operation: the import on the file's content hash,
  the converter, the re-save, the scale, the dictionaries and the tool
  identity; the assembly on the imported mesh files and the patch settings;
  the check on the merged mesh. A patch-type change re-runs the assembly,
  the check and case setup, never the conversion; a physics change re-runs
  case setup only. Case setup leaves both meshes byte-identical
  (invariant 14; `test_case_setup_never_changes_the_imported_mesh`, and on
  v2512 `test_a_rotating_mesh_is_imported_set_up_and_runs`).
- The import is recorded only after its checks pass; a failed or cancelled
  import leaves no record and the next run converts again.
- `reports/mesh_import.json`: the file, its hash, format and version, the
  converter, whether it was re-saved, the OpenFOAM project, version and
  executables, units and scale, cells (and the declared count), patches,
  cell zones, and the zone radius "computed from the zone" (owner answer 3).
- checkMesh "Failed N mesh checks" fails the mesh (validity INVALID) whatever
  the exit code; on v2512 a coarser rotating mesh does exactly that with exit
  0 (`test_a_mesh_failing_checkmesh_with_exit_0_fails`).
- **The bounding box (owner answer 4):** read from the file when it is ASCII;
  otherwise `MachineService.convert_for_box` converts once at scale 1 into
  `cases/imported_scratch/` (under the run lock) and the box is shown "from
  the converted mesh". The import moves that polyMesh into `cases/imported/`
  instead of converting again when the scratch record matches the file's
  hash, the converter and the tool, then scales and checks it like any
  conversion (`logs/imported/00_promote.log`). The scratch case is never
  recorded as a verified artifact.
- Resource preflight on the file's declared cells (Fluent), else nodes x 6
  (Gmsh; a heuristic, labelled so), else not assessed (binary Gmsh; INFO).

### 8.2 Checks before converting

| Finding | Severity |
|---|---|
| Imported mesh with STL bodies, zones, domain, joints or wake (`MSH_WITH_STL`) | BLOCKING |
| Missing, empty, unreadable, unknown or HDF5 file; Gmsh header incomplete; MSH 1; another Gmsh major version | BLOCKING |
| Gmsh without physical groups, or without a Physical Volume | BLOCKING, with the fix |
| Binary Gmsh without the gmsh package; ASCII Gmsh other than 2.2 or 4.1 without it | BLOCKING |
| Fluent without its dimension or cell-count header; without zone names (a file cut short); 2D | BLOCKING |
| Binary Fluent sections (`FLUENT_BINARY_UNTESTED`) | WARNING |
| The file changed since it was chosen (`MSH_SOURCE_CHANGED`, from its hash) | BLOCKING |
| No rotating cell zone, an unknown one, or the only one of the mesh (`MSH_NO_CELL_ZONE`) | BLOCKING, with the Gmsh or Fluent fix |
| No AMI pair for the rotating zone (`MSH_NO_INTERFACE`); a pair naming an unknown patch, the same patch twice, a patch in two pairs, or a typed patch | BLOCKING |
| A mesh patch with no type and in no pair; a patch from an unknown mesh patch or another source kind; two rows for one mesh patch; a name ending in `_ami`; no inlet or outlet; duplicate names | BLOCKING |
| Patch or cell zone names OpenFOAM dictionaries cannot hold (`MSH_NAME_INVALID`) | BLOCKING |
| Largest extent in the chosen units outside 0.01–10,000 m (`MSH_UNITS_SUSPECT`, configurable) | WARNING |
| A VAWT or VAWT-with-pole project (`MSH_MACHINE_NOT_SUPPORTED`; use Custom) | BLOCKING |

### 8.3 After converting

| Finding | Severity |
|---|---|
| No readable mesh, or no cells (`MSH_CONVERT_FAILED`) | BLOCKING |
| Cells other than the file declares (`MSH_CELL_COUNT_MISMATCH`) | BLOCKING |
| `defaultFaces` with faces (`GMSH_UNGROUPED_FACES`): the count and the box of those faces | BLOCKING (owner decision 3) |
| A patch named in the file missing from the mesh (`MSH_PATCH_LOST`) | ERROR |
| A configured cell zone missing (`MSH_NO_CELL_ZONE`) | BLOCKING |
| Baffle faces in checkMesh (`MSH_BAFFLE_PAIR`): kept as two walls unless paired | WARNING |
| As every machine mesh: patch presence and types, AMI pairing and sum(weights), quality limits | as G7 |

### 8.4 Findings during M1b (v2512)

1. **createPatch with a patch's own name does nothing, with exit 0.** A
   `createPatchDict` entry `name AMI1; ... patches (AMI1);` reported "Moving
   faces from patch AMI1 to patch 7" and left AMI1 a `patch`, not
   `cyclicAMI`. M1b therefore made each AMI patch under a new name,
   `<patch>_ami`. Superseded in M1c (section 10.2): foamDictionary sets the
   type in place, so the patches keep their own names.
2. **Fluent cell zone names contain "-"** ("fluid-1"), which
   `machines.mesh_folder.cell_zone_names` does not read (word characters
   only). The import reads zone names with its own pattern; the G7 helper is
   unchanged.
3. On the M1 rotating test mesh (3,383 cells), both routes (Gmsh 4.1, and
   the two-zone Fluent file) import, pair, pass checkMesh (2 regions), set up
   and run 10 pimpleFoam steps; AMI sum(weights) 1.00–1.09. The same mesh
   written in mm and imported with units mm gives the metre mesh's points
   (within 1e-9 m).
4. Units matter to the quality limits too: the rotating test mesh read as mm
   (4 mm across) fails the minimum cell volume (7.9e-14 against 1e-13 m³) and
   warns `MSH_UNITS_SUSPECT`; read as cm it passes. Wrong units are caught
   twice, never silently scaled.

### 8.6 Fixes after the M1b testing review

`tests/unit/mesh_import/test_m1_review_fixes.py`; the real converters in
`test_m1_msh_real.py` (last two tests).

- **HIGH:** a file cut short inside a node section raised an unhandled
  RuntimeError (a StopIteration inside a generator). Now BLOCKING
  `MSH_FILE_UNREADABLE` ("the file ends inside a node section").
- **HIGH:** a Gmsh file without `$Nodes` passed detection, and gmshToFoam
  aborted on it (exit 134, seen on v2512). A Gmsh file without `$Nodes` or
  (ASCII) without the end of `$Elements` is now BLOCKING `MSH_FILE_TRUNCATED`.
- **HIGH:** Fluent zone sections with a field after the name
  (`(39 (12 wall walls 1)())`, which fluent3DMeshToFoam reads) or extra spaces
  were not read, so a valid file was refused, or a zone was lost silently.
  They are read now; a zone section that still cannot be read is BLOCKING
  `FLUENT_ZONE_UNREADABLE`, never dropped.
- **MEDIUM:** the fake converter read names with the detector under test, so
  a detection miss could not show. It has its own parser now, and on v2512
  every fixture's detected patches and cell zones equal what the converter
  writes. The scratch promotion is tested through the service and on v2512;
  the refused promotions are tested with the real tool identity (a changed
  file; `force`).
- **LOW:** the import report's zone radius is rewritten on every run (it
  follows a changed origin while the import is reused); a stopped import
  removes its mesh; AMI pair evidence (`mesh_patch_geometry`) comes only from
  a mesh of the file with the given hash; the scratch conversion checks the
  environment with the project's OpenFOAM profile; `force` never promotes.
- Not changed: the units check is skipped for a binary file until it is
  imported (its box is known only from the scratch conversion, which the UI
  shows beside the units); the gmsh package version is not in the re-save's
  cache key (the re-saved file is checked like any conversion); a UTF-8 BOM
  before `$MeshFormat` is BLOCKING `MSH_FORMAT_UNKNOWN`.

### 8.5 Configuration

`imported_mesh` (optional): `source_path`, `source_units` (required, never
assumed), `sha256` (the file when chosen), `zones` (`name` for the physics,
`cell_zone` as in the mesh, `axis`, `origin`; the radius is computed from the
zone, never entered), `ami_pairs` (`first`, `second`: confirmed by the user,
never made up). Patches come from `MESH_PATCH` sources; a patch may be
renamed (createPatch). An absent `imported_mesh` is left out of the saved
file, so an existing project's file and hashes are what they were: on main
(cb07c48) and on this branch, five STL fixture projects give the same saved
file and the same meshing inputs (`tests/fixtures/msh/m1/stl_baseline.json`,
`test_existing_projects_are_saved_and_meshed_byte_identically`).

---

## 9. Schema version 3 (owner decision after M1b)

The owner approved the bump and the change to the version assertion in
`test_g6_case_setup.py::test_physics_is_optional_and_versioned` (2 -> 3, and
the migrated version in the same test). `MACHINE_SCHEMA_VERSION = 3`.
`tests/unit/mesh_import/test_m1_schema_version.py`:

- A version 2 project (as main wrote it) loads as version 3 with no issue,
  and its meshing inputs are main's (`stl_baseline.json`). The saved file
  differs from main's in the version number only: the fingerprint compares it
  with that line as main wrote it (`stl_baseline.py`).
- A version 2 project meshed before the upgrade: no section stale, and a run
  after the upgrade reuses every stage, as a rerun without it does (no
  OpenFOAM command).
- Older code: main (cb07c48, taken from git with `git archive`) opening a
  version 3 project, with or without `imported_mesh`, gives
  `SCHEMA_VERSION_NEWER` only ("written by a newer version of this
  application"; "Open this configuration with the newer application version
  that wrote it."), never an unknown field. The same code still opens a
  version 2 project, so the test shows the version check and not old code
  failing to load. Skipped without git or that commit.

---

## 10. M1c items 1-3 (owner, after the M1b review)

`tests/unit/mesh_import/test_m1c_core.py` (fake OpenFOAM) and
`tests/integration/test_m1_msh_real.py` (v2512).

### 10.1 A flow-rate inlet's area, measured from the mesh

- The area of a patch is the sum of its face areas, each the magnitude of its
  area vector as OpenFOAM's `magSf` (triangles from each edge to the mean of
  the face's points; `checks.patch_areas`). Labelled "measured from the mesh".
- Before the import: INFO `INLET_AREA_AFTER_IMPORT` instead of BLOCKING. The
  UI's validation uses the imported mesh of the same file, else its
  bounding-box conversion (scaled to the configured units) when there is one.
- In the run: case setup checks the physics again with the areas of the
  assembled mesh (renamed patches under their new names). A flow-rate inlet
  whose patch has no faces is BLOCKING `INLET_AREA_UNKNOWN` ("could not be
  measured from the mesh"); case setup is skipped, the mesh is kept.
- Reports: `mesh_import.json` `patch_area_m2` (every patch), and
  `case_setup.json` `inlet_area_m2` with "measured from the mesh".
- v2512: the two-zone Fluent rotating mesh with Q = 20 m³/s: inlet area 4.000
  m² (the 2 m x 2 m face), inlet speed 5 m/s, `flowRateInletVelocity`, 10
  pimpleFoam steps.

### 10.2 AMI pairs keep their names

- foamDictionary on `constant/polyMesh/boundary`, per side: `type` -set
  `cyclicAMI`; `neighbourPatch`, `transform noOrdering`, `matchTolerance
  0.0001` -add. createPatch remains for renamed patches only. No fallback to
  `<patch>_ami` was needed: on v2512 checkMesh reports "Mesh OK" with
  `ami_stat` and `ami_rot` "ok (closed singly connected)", AMIWeights
  0.99999-1.0004, and both rotating routes run 10 pimpleFoam steps on the
  original names.
- `mesh_import.json` `names_used`: original name -> name used (an AMI patch,
  and every patch not renamed, keeps its own). Renaming a patch onto another
  patch of the mesh is BLOCKING `RESERVED_PATCH_NAME`.
- Not tested on real files: a Fluent interface zone written as `wall` keeps
  its `inGroups (wall)` entry when made cyclicAMI.

### 10.3 AMI weights

- Imported meshes: WARNING `AMI_WEIGHTS_OUT_OF_RANGE` outside 0.95-1.05
  (`MachineThresholds.imported_ami_weights_range`), labelled "at rest (0
  deg)". STL meshes keep 0.85-1.5 (owner; future list, section 7).
- checkMesh prints no AMI weights on v2512; the weights come from postProcess
  AMIWeights on the mesh as converted, as for STL meshes. The application
  does not run solvers, so not from a first solver step.
- **One revolution without solving the flow works on v2512**:
  `moveDynamicMesh -checkAMI` with a solid-body rotation of the zone
  (`revolution.py`: one revolution per second, 36 steps of 10 deg, nothing
  written) rebuilds the AMI at each step and prints its sums. Plain
  `moveDynamicMesh` prints none. -checkAMI also writes VTK files of the AMI
  patches (596 KB on the fixture), so it runs on a copy of the assembled mesh
  in `cases/ami_revolution/`, removed afterwards.
- Labelled "over one revolution (36 x 10 deg)", with the same 0.95-1.05
  WARNING (owner, M1c).
- On the M1 rotating fixtures (Gmsh 4.1 and the Fluent file, the same mesh):
  at rest 0.99999-1.0004; over one revolution **0.9946-1.104**, in 0.6-0.7 s.
  So the at-rest check passes a mesh whose interface goes outside 0.95-1.05
  as it turns.
- The optional action ("check over one revolution",
  `MachineService.check_ami_revolution`) runs under the run lock and writes
  `reports/ami_revolution.json` with min/max per pair over all angles, the
  steps done, failed mesh checks of the turned mesh, and the hash of the
  assembled mesh it checked (`revolution.mesh_hash`), so a result for an
  older mesh can be shown as such. Fewer steps than planned is ERROR
  `AMI_REVOLUTION_FAILED`.
- `parse_ami_weights(every=True)` takes the range over all reports of a
  pair; the default (first report only, for solver logs) is unchanged.

### 10.4 VAWT

Still not offered for an imported mesh; `MSH_MACHINE_NOT_SUPPORTED` now says
"To use an imported VAWT mesh, create a Custom project."

---

## 11. M1c: the UI

`tests/unit/ui_machines/test_m1c_app.py` (headless, fake OpenFOAM),
`tests/integration/test_m1c_ui_real.py` (v2512).

- **Mesh source** (Project): HAWT, Francis and Custom offer "Build from STL /
  Import .msh". The choice is the draft's `imported_mesh`; no new field. An
  imported mesh shows Project, **Imported mesh**, then the profile's other
  sections without Rotor / Water passage / Rotating zone / Domain /
  Refinement / Boundary layers (`MachineProfile.for_mesh`). Switching a
  filled draft asks first (it removes the other source's settings and the
  patch table; Revert brings them back until Save). VAWT: "To use an imported
  VAWT mesh, create a Custom project."
- **Imported mesh**: upload (up to 200 MB) or a path inside WSL; the detected
  format, version, ASCII or binary, converter (and the gmsh re-save), the
  OpenFOAM version, patches with their Fluent types, cell zones, declared
  cells; the file's own findings, also while the draft is incomplete; the
  bounding box (read from the file, or "Convert to show the box" -> "from the
  converted mesh") with its size in metres for every unit, and the units
  chosen against it; a changed file is found ("Use the file as it is now");
  the rotating zone (name, the cell zone from the file, axis, a point on the
  axis; radius "computed from the zone" after the import); the AMI pair
  (suggestions with each side's faces, area and centre, used only by their
  button, or chosen by hand); and the names in the mesh (original name ->
  name used).
- **Patches**: "Add missing patches" lists the mesh's patches except the AMI
  pair, with types suggested from the name or the Fluent zone type, applied
  only by "Use the suggested types".
- **Operating point (Francis), Rotor speed**: the imported zone. A flow-rate
  inlet before the import is INFO "measured from the mesh when it is
  imported".
- **Mesh** results: the import (converter, OpenFOAM version, units and
  scale, cells, cell zones, zone radius, names used with each patch's area
  "measured from the mesh"), the quality report, the AMI weights "at rest (0
  deg)" with the 0.95-1.05 WARNING, and **Check over one revolution (36 x 10
  deg)** (min/max per pair, steps, failed mesh checks; "an earlier mesh" when
  the mesh has changed since). **Case setup** results apart: the inlet area
  "measured from the mesh" and, for a tip-speed ratio, the tip radius used.
- **Tip radius** (Rotor speed, tip-speed ratio; owner after items 1-3):
  beside "Tip radius R (m)": "Measured: X m from rotor body <name>" (STL: the
  zone's rotating bodies) or "from rotor patch <name>" (imported: the
  rotating-wall patches, once converted), set only by "Use measured R". The
  derived rpm shows "with tip radius R = X m". INFO `TIP_RADIUS_DIFFERS` when
  the typed R is more than 5 % from the measured one.
- The 3D setup view has nothing to draw for an imported mesh (INFO); the
  Mesh view shows the imported mesh after a run.
- Found while testing: viewing the zone form added an empty zone to the
  draft (a section must never edit the draft by being shown). The zone is
  now added when a file is chosen, and a test visits every screen and checks
  that the draft is unchanged.

### 11.1 Fixes after the M1c testing review (PASS WITH FINDINGS; no HIGH)

- **MEDIUM: a check over one revolution showed as current after the zone's
  axis, origin or cell zone changed** (the mesh, and so its hash, stays the
  same). The check is now keyed on the mesh and the files that turn it
  (`revolution.check_key`); the Mesh view says "an earlier mesh or rotating
  zone" otherwise.
- **LOW: an AMI patch kept its converter's group.** fluent3DMeshToFoam writes
  a Fluent wall zone with `inGroups 1(wall)`; made cyclicAMI in place it stayed
  in the wall group (v2512, reproduced). Each side's `inGroups` is removed
  (`foamDictionary -remove`, exit 0 also without one). Real test with the
  Fluent rotating file's interface zones relabelled wall.
- **LOW: the measured tip radius took every rotating wall of the project**;
  now only the rotating-wall faces on the zone's cells (a rotor of another
  zone is left out). Read once per version of the mesh files.
- **LOW: the run kept the INFO "measured when it is imported"** after the area
  was measured; removed once answered.
- **Test gaps**: the STL measured radius is checked against the rotor STL's
  points computed in the test, and "Use measured R" sets it; a HAWT project
  runs on an imported mesh through the UI.

## 12. Browser checklist (owner, before the merge)

Start the Machines app (`scripts\run_machines_app.cmd`, or inside WSL `bash
scripts/run_machines_app.sh`) with OpenFOAM v2512 and open
http://localhost:8502. Not the VAWT app (`run_vawt_app.cmd`, port 8501): it
takes a VAWT rotor STL only and has no .msh import. Files:
`tests/fixtures/msh/m1/` (upload them, or give their path inside WSL).
Write the result in the last column.

**A. Gmsh 4.1 rotating mesh (`rotating_41.msh`), Custom project**

| # | Do | Expect | Result |
|---|---|---|---|
| A1 | New project, Custom. Project: "Use: Import .msh (Fluent or Gmsh)" | Navigation: Project, Imported mesh, Patches, the physics sections, Review, Run, Mesh, Case setup, ...; no Rotating zones, Domain, Refinement or Boundary layers | |
| A2 | Imported mesh: upload `rotating_41.msh` | GMSH 4.1 (ASCII), gmshToFoam, OpenFOAM v2512; patches ami_rot, ami_stat, inlet, outlet, rotor, y_min, y_max, z_min, z_max; cell zones rotating, stationary | |
| A3 | The box | "read from the file": min (-1, -1, -1), max (3, 1, 1); the metres row 4 x 2 x 2 | |
| A4 | Units: metres, Apply | The units finding goes | |
| A5 | Rotating zone: name rotor, cell zone rotating, axis +x, point (0, 0, 0), Apply | "Zone radius: computed from the zone once the mesh is imported." | |
| A6 | AMI: "Use ami_stat / ami_rot" | The pair is listed; names table: ami_stat -> ami_stat (AMI) | |
| A7 | Patches: Add missing patches, Use the suggested types; y/z min/max SLIP, rotor ROTATING_WALL; Apply patches | No AMI patch in the table; inlet INLET, outlet OUTLET | |
| A8 | Fluid (air); Rotation: tip-speed ratio 5, R 2, U 5, direction Positive; Apply | "Measured: not available yet"; "ω = ... rpm, with tip radius R = 2 m" | |
| A9 | Save; Run: Start run | The run SUCCEEDED | |
| A10 | Mesh | "with gmshToFoam on OpenFOAM v2512"; "AMI ami_stat / ami_rot at rest (0 deg): sum(weights) 1-1 (WARNING outside 0.95-1.05)"; names used with patch areas (inlet 4) | |
| A11 | Mesh: **Check over one revolution (36 x 10 deg)** | **WARNING** "AMI sum(weights) of ami_stat / ami_rot over one revolution (36 x 10 deg) spans 0.995-1.1, outside 0.95-1.05"; the line 0.9946-1.104; 36 of 36 steps, 0 failed; about a second | |
| A12 | Case setup | SUCCESS; rotation of `rotating` "from the tip-speed ratio with tip radius R = 2 m" | |
| A13 | Rotation again | "Measured: 0.4031 m from rotor patch rotor" (the fixture's rotor block, y +-0.4, z +-0.05); INFO TIP_RADIUS_DIFFERS for R = 2 m; "Use measured R for rotor (0.4031 m)" sets it and the INFO goes | |
| A14 | 3D pane | Setup view: "An imported mesh has no setup view"; Mesh view: the imported mesh | |

**B. Two-zone Fluent mesh (`fluent_rotating.msh`), Francis project**

| # | Do | Expect | Result |
|---|---|---|---|
| B1 | New project, Francis turbine; Project: Use: Import .msh | No Water passage or Wall cells (y+); Operating point kept | |
| B2 | Upload `fluent_rotating.msh` | FLUENT (ASCII), fluent3DMeshToFoam, cells declared 3,383; every patch pressure-outlet (as foamMeshToFluent writes them, so no type is suggested from it); cell zones rotating, stationary | |
| B3 | Units m; zone runner = cell zone rotating, +x, (0, 0, 0); AMI ami_stat / ami_rot; patches as A7 | | |
| B4 | Fluid (water); Operating point: Q 20 m³/s at inlet, N 100 rpm, direction | Before the run: INFO "measured from the mesh when it is imported", not BLOCKING | |
| B5 | Save, run | SUCCEEDED; Case setup: "Inlet inlet: area 4 m² (measured from the mesh: the sum of the face areas)"; 0/U has flowRateInletVelocity (case file viewer) | |
| B6 | Mesh: Check over one revolution | The same WARNING as A11 (the same mesh) | |

**C. One bad file per BLOCKING rule** (Custom project, Import .msh; the
message shows in Imported mesh as soon as the file is chosen)

| # | File | Expect (BLOCKING) | Result |
|---|---|---|---|
| C1 | `bad/empty.msh` | MSH_FILE_EMPTY | |
| C2 | `bad/not_a_mesh.msh` | MSH_FORMAT_UNKNOWN | |
| C3 | `box_v1.msh` | MSH_FORMAT_UNSUPPORTED: Gmsh MSH 1 | |
| C4 | `bad/gmsh_bad_header.msh` | MSH_HEADER_INVALID: "the Gmsh $MeshFormat header is incomplete" | |
| C5 | `box_22_nogroups.msh` | GMSH_NO_PHYSICAL_GROUPS | |
| C6 | `bad/gmsh_no_volume.msh` | GMSH_NO_PHYSICAL_VOLUME | |
| C7 | `bad/gmsh_no_nodes.msh` | MSH_FILE_TRUNCATED: no $Nodes | |
| C8 | `bad/gmsh_cut_short.msh` | MSH_FILE_UNREADABLE: the file ends early | |
| C9 | `bad/fluent_2d.msh` | FLUENT_2D | |
| C10 | `bad/fluent_no_zones.msh` | FLUENT_NO_ZONES | |
| C11 | `bad/fluent_bad_header.msh` | MSH_HEADER_INVALID: no Fluent dimension or cell count header | |
| C12 | `rotating_41_noymin.msh` (y_min in no physical group): set up as A3-A7 (no y_min row), save, run | The run stops: GMSH_UNGROUPED_FACES, with the face count and their box (y = -1); no mesh is kept | |
| C13 | `fluent_box.msh`, zone cell zone fluid-1 | MSH_NO_CELL_ZONE: "the only cell zone of the mesh" | |
| C14 | After A1-A7: append a line to the file outside the app, reopen Imported mesh | "The file has changed since it was chosen"; MSH_SOURCE_CHANGED in Review; "Use the file as it is now" clears it | |
| C15 | VAWT project, Project section | "To use an imported VAWT mesh, create a Custom project."; no mesh source choice | |

Not in the browser list: a binary Gmsh file without the gmsh package
(GMSH_BINARY_NEEDS_GMSH; gmsh is installed here; covered by unit tests).

## 13. M2a: large single meshes (owner approval after E0 on the Francis turbine)

E0 on the owner's Francis turbine (four Fluent files: GV and RU single
passages, DT and SC-SV full 360°; 23 M cells assembled by hand) is reported
in the session record; the files are never committed (`/root/meshes`, opt-in
tests with `MACHINES_E0_DIR`). The owner's decisions: binary for every
imported mesh; a two-level checkMesh for imported meshes only (unclassified
failures stay BLOCKING); the revolution check in the background (36 x 10 deg
by default, coarser sweeps offered above 10 min, 45 deg preferred over 30
deg); a machine-wide heavy-step lock that says who holds it; a disk file
picker with a copy into WSL; a preflight from the measured E0 figures.

### 13.1 Evidence before the commits (v2512, this machine: 31 GB RAM, 32 cores)

**1. checkMesh wording per defect class.** Tiny blockMesh cubes (4 x 4 x 4)
with one defect each, checked plain, with `-allGeometry -allTopology`, and
with the app's flags (`-allTopology -meshQuality`):

| Defect made | `***` lines (all three runs unless noted) | `-meshQuality` table rows |
|---|---|---|
| two point layers swapped | High aspect ratio cells; **Zero or negative cell volume detected** (16 cells); Number of non-orthogonality errors; Error in face pyramids | non-orthogonality > 65; face pyramid volume < 1e-13 |
| one point pushed through its neighbours | Number of non-orthogonality errors: 4; **Error in face pyramids**: 16 faces are incorrectly oriented (volumes stay positive) | the same two |
| one face's points reversed | **Open cells found**; non-orthogonality errors; Error in face pyramids (`-allGeometry` adds face tets, Concave cells) | non-orthogonality |
| last boundary face removed | **Boundary openness** ... possible hole; Open cells (`-allTopology` adds "Open cells found, number of cells: 1 ... zipUpMesh") | none |
| shear 80 deg | `*`Number of severely non-orthogonal (> 70 degrees) faces: 96 (one star: not a failure); **Max skewness = 5.67, 32 highly skew faces** | non-orthogonality > 65 |
| stretch x 5000 | **High aspect ratio cells found**, Max aspect ratio: 5000 (`-allGeometry` adds Cells with small determinant) | face-decomposition tet quality < 1e-15; cells with determinant < 0.001 |

"Concave cells" is only checked with `-allGeometry`, which the import does
not run; `-meshQuality` reports concavity in its table. The table ("Checking
faces in error") counts as one failed check and has no `***` line.

On the owner's meshes, `-allTopology -meshQuality`:

| Mesh | Time / peak RAM | `***` | Table rows (non-zero) |
|---|---|---|---|
| GV passage | 2.0 s / 0.4 GB | Max skewness 5.52, 11 faces | non-orthogonality > 65: 28,008; tet quality: 123 |
| RU passage | 5.0 s / 1.0 GB | Mesh OK | none |
| DT | 7.9 s / 2.1 GB | Mesh OK | none |
| SC-SV | 39.8 s / 7.1 GB | Max skewness 8.13, 384 faces | non-orthogonality 42,291; tet quality 550; skewness 384; interpolation weight < 0.05: 95; determinant < 0.001: 380 |
| GV ring (7.9 M) | 42.0 s / 11.1 GB | none | non-orthogonality 793,800; tet quality 3,444 |
| RU ring (10.8 M) | 57.4 s / 14.9 GB | Mesh OK | none |
| machine (23 M) | 170 s / **31.9 GB** | SC skewness | as SC plus the GV ring |

Owner decision on these rows: tet quality, interpolation weight, cell
determinant, volume ratio and face twist are WARNINGs, shown per zone; face
pyramid volume stays BLOCKING.

The cost is `-allTopology`: at 23 M cells it alone takes 141 s and 31.9 GB
(`-meshQuality` alone 71 s / 20.7 GB; plain 52 s / 19.2 GB). It is also what
reports wall baffles ("identical duplicate faces", needed for MSH_BAFFLE_PAIR:
no such line without it on the E3 baffle mesh). `mergeOrSplitBaffles
-detectOnly` counts the same faces (100 = 2 x 50) in 8.1 s / 9.2 GB at 23 M.
M2a keeps `-allTopology` for a single file (SC: 7 GB) and models its RAM in
the preflight; the 23 M assembly is M2b's question.

**2. The tip radius from the zone's own walls, by tools.** A topoSet faceSet
of the chosen wall patches, subset to the faces of the zone's cells, made a
faceZone; surfaceMeshExtract wrote it with every patch in one pass (23 M:
topoSet 12.3 s / 11.9 GB, extraction 7.2 s / 9.2 GB). A wall of another zone
in the list (SV-SS) dropped out (882,834 to 813,600 faces). The app makes one
faceZone per cell zone instead (the zone's outer surface: `cellToFace
outside`, its boundary faces included), so any choice of walls is measured
later without a tool run: a patch face is in the zone when the same face
(the same point labels; the extraction merges points) is in the zone's
surface. RU passage: all 82,976 patch faces found in exactly one zone
surface; SC: 384,961 of 384,961. Cost: RU (12 zones) 1.7 + 1.6 s, SC 7.7 +
6.9 s / 0.9 GB, the machine (22 zones) 19.6 + 9.2 s / 10.1 GB and a 300 MB
.vtp (zone surfaces repeat the faces between zones).

**3. AMI weights per face at rest.** `postProcess -constant` with the
AMIWeights function object and `writeFields true` writes
`postProcessing/AMIWeights1/constant/<patch>_{src,tgt}.vtp` with a per-face
`weightsSum`: 9.5 s / 9.2 GB at 23 M (as without), a few MB. RU to DT at
rest: runner side 830 faces below 0.95, 0.209 % of the area, all at r 0.1751
m, z -0.1908 (the shroud edge); draft-tube side 64 faces, 0.044 %, at r
0.0414 m (the hub edge), the picture `-checkAMI` gave over the revolution.

**4. Copy from a Windows drive.** A 586 MB file on E: not read for a month,
copied into WSL in 8 MB chunks with sha256 computed while copying: 2.25 s
(261 MB/s) cold, 1.78 s warm; re-hashing the copy 0.26 s; hashes equal.

**5. One checkMesh pass with sets and fields.** `-meshQuality -writeSets vtk
-writeFields (nonOrthoAngle skewness cellDeterminant faceWeight minTetVolume
minPyrVolume cellVolumeRatio aspectRatio)`: SC 29.8 s / 5.8 GB, the machine
89.5 s / 21.0 GB. The fields go to `constant/` (1.5 GB binary at 23 M), the
sets to `postProcessing/constant/<set>/<set>.vtp` (a cell set as its faces);
the region cell sets (`region0..3`, 312 MB at 23 M) and `cellToRegion` only
to `constant/polyMesh/sets` and `constant/`. checkMesh's field names in
v2512: aspectRatio cellAspectRatio cellDeterminant cellRegion cellShapes
cellVolume cellVolumeRatio cellZone faceWeight faceZone minPyrVolume
minTetVolume nonOrthoAngle skewness wallDistance (no twist, no concavity).

**6. Cuts by a tool.** The `surfaces` function object (`cuttingPlane`,
`interpolate false`, `triangulate false`, `fields ()`) through `postProcess
-constant` writes each cut's cell outlines as polygons: DT 2.5 s / 0.5 GB,
the machine 16.0 s / 10.8 GB.

**7. A surface case beside the mesh.** topoSet and surfaceMeshExtract in a
case whose `constant/polyMesh` files are links to another case's: the
source's points, faces, owner, neighbour, boundary, cellZones and faceZones
were byte-identical afterwards; only the case's own faceZones and sets were
written.

### 13.2 Binary meshes and the extracted surface (commit 1)

Every imported case (`imported`, `merged`, `imported_scratch`) writes binary;
STL meshing keeps its ASCII controlDict. Nothing reads an imported mesh's
points, faces or owner in Python. What stays text in a binary mesh and is
read: the boundary file (a dictionary; `foam_reader.read_boundary` accepts
its binary header, every other reader still refuses one), the owner header's
nCells, the zone names.

`machines/msh_import/surface.py`: `cases/<case>_surface/` links to the mesh,
topoSet makes `zoneSurface_<zone>` for each cell zone, surfaceMeshExtract
writes `boundary.vtp`, and `regions.json` records the region names (from the
log) and the stat of the mesh files it was made from; a surface of another
mesh is not used. From it: patch areas (as magSf, float32 points), boxes,
area-weighted centroids (the AMI pair evidence), the zone radius (its outer
surface), the rotor walls of a zone (tip radius), the scratch conversion's
box (the boundary's points). The import extracts it only after the cheap
checks accept the mesh (cell count, zones, lost patches), so a rejected mesh
costs nothing more; the defaultFaces box is read from it.

A new operation `machine_mesh_views` after checkMesh makes the merged mesh's
surface and two cuts through the first mesh zone (the plane through its axis
normal to the first other axis, and across the axis at its mid-span), with
`cuts.json` recording the planes. Display only: a failure is a WARNING
(MESH_VIEW_FAILED), nothing is recorded and the next run tries again. The
mesh view of an imported project draws the boundary by patch (each patch
simplified by vertex clustering on its own grid) and the cuts; M1c could not
draw it at all (its cut planes came from `rotating_zones`, empty for an
imported project). checkMesh's problem locations are drawn since commit 2 (13.3).

An M1 project (ASCII, no surface) has no current surface: the UI treats it
as not yet converted, and the next run imports it again (the import's
recorded outputs now include the surface).

Real v2512 (`tests/integration/test_m2a_real.py`): the rotating test mesh's
inlet 4.0 m², zone radius 0.500 m, tip radius from the rotor patch, both
cuts; the owner's files (opt-in) converted binary for their box and measured
from the surface: interface radii within 0.5 mm of E0's text reads (GV
FaceGroup13 0.3200, FaceGroup27 0.3822; RU r1_inflow_1 0.3200, r1_outflow_1
0.1752; DT_INLET 0.1752; SV-Outlet 0.3822). Conversion plus surface: GV 0.6 s,
RU 1.4 s, DT 2.1 s, SC 10.1 s.

### 13.3 checkMesh in two levels, quality per zone, AMI detail (commit 2)

`machines/msh_import/quality.py`, for imported meshes only (STL and VAWT keep
the one-level rule; `mesh/validator.py` is unchanged). Each `***` line and
each non-zero `-meshQuality` table row of checkMesh's log is classified:

| Level | Classes (checkMesh wording, evidence 1) |
|---|---|
| BLOCKING | zero or negative cell volumes; open cells; wrongly oriented faces (face pyramids); open boundary; face pyramid volume (table); faces over 90 deg non-orthogonal (on neither list, so BLOCKING, named) |
| WARNING | skewness; non-orthogonality; aspect ratio; concave cells; tet quality, interpolation weight, cell determinant, volume ratio, face twist (table; owner, after evidence 1) |
| BLOCKING | any other line, and a "Failed N mesh checks" count the lines do not explain (the table counts as one failure and has no `***` line) |

Without a BLOCKING class the mesh is VALID, quality REVIEW, status review:
one MESH_QUALITY_WARNING per class, with checkMesh's own lines and, per cell
zone, the cells past the limit, their share and the worst value. With one,
CHECKMESH_FAILURE names the BLOCKING classes. The checkMesh pass adds
`-writeSets vtk -writeFields (nonOrthoAngle skewness faceWeight aspectRatio)`:
only those fields agree with checkMesh's counts (evidence 9: GV's table has
0 faces on cells with determinant < 0.001 where the cellDeterminant field has
1,282 cells below it; minTetVolume has no cell <= 0 where the table has 123
tet-quality faces; cellVolumeRatio 14 cells where the table has 0), so tet
quality, determinant, volume ratio, twist and concavity are given as face
totals, said so. A cell field takes the worst of the cell's faces. The
per-zone figures go to `reports/mesh_quality_zones.json` and the mesh
report; the fields, `cellToRegion` and the region sets are then deleted; the
VTK sets stay in `cases/merged/postProcessing/constant/` for ParaView and are
drawn as problem markers in the mesh view.

Real v2512, the import's own command on the owner's files (opt-in): GV
WARNING only (non-orthogonality: down 11,282 of 66,560 cells, worst 78.5 deg;
skin 5,724 of 71,680, 81.6 deg; up 802, 72.8 deg; upStream 267, 68.5 deg.
Skewness: upStream 11 cells, 5.52. Tet quality: 123 faces, no per-zone
field), RU and DT Mesh OK, SC WARNING only (skewness 768 cells, 8.13;
non-orthogonality 40,337 cells, 84.9 deg; interpolation weight 200 cells,
0.0198; tet quality and determinant as totals). checkMesh: GV 1.4 s, RU 3.5 s,
DT 6.1 s, SC 32.5 s. The M1 coarse rotating mesh (non-orthogonality only)
now imports with a WARNING instead of failing.

AMI weights of an imported mesh run with `writeFields true`
(`machines/msh_import/ami_detail.py`); an AMI_WEIGHTS_OUT_OF_RANGE warning
then says, for each side, the share of its area below the range's lower
end, where (radius and position along the rotation axis), which edge, and
the side's own extent. On the 23 M machine's files: "RU_DT_ru: 0.21 % of its
area (830 of 36000 faces) below 0.95, at r 0.175 to 0.1752 m, z -0.1908 to
-0.1907 m (its outer (largest-radius) edge); the side spans r 0.04136 to
0.1752 m ..."; "RU_DT_dt: 0.044 % ... at r 0.04135 to 0.04154 m (its inner
(smallest-radius) edge)".

### 13.4 The file picker, the copy into WSL, the launcher (commit 3)

`machines/disk.py`. Roots: the WSL home and each Windows drive in
`/proc/mounts` (9p/drvfs under `/mnt`). A folder or file is used only when it
resolves (links followed) inside a root; an entry that is a link out of a
root is not listed. Pasted paths: `E:\Meshes\Francis` -> `/mnt/e/Meshes/Francis`,
`\\wsl$\Ubuntu-24.04\root\meshes` (or `\\wsl.localhost\...`) -> `/root/meshes`
(another distribution is refused with the reason), Linux paths as given.
The remembered mesh folder is an app setting (`~/.machines/settings.json`,
`MACHINES_SETTINGS` for another file), listed when the section opens.

A folder lists its sub-folders and `.msh` files with size, date and a
summary from the headers only (`machines/msh_import/scan.py`: grep of the
lines starting with "("; the owner's four files 0.01-0.04 s in WSL, the GV
file 0.14 s on C:): format, ASCII/binary, declared cells, cell zones,
boundary zones and how many have no name. "Use" takes a file in WSL where it
is; "Copy and use" copies a file on a Windows drive into
`<project>/inputs/mesh/` first: 8 MB chunks, sha256 while copying, `.part`
renamed only after its size and hash are read back and match, a free-space
check (size + 5 %) first. The copy runs on a thread of the app with a status
file beside the target, so the page shows its progress (MB, MB/s, time left)
and a Cancel button, also after a refresh; a copy finished while the page
was away is offered with "Use it"; one left "copying" by a stopped app shows
as interrupted. Uploads up to 200 MB stay, with the owner's note.

Fluent face zones without a name (`detect.py`): the face section headers
`(13 (id first last type ...)` give each face zone's id and boundary type
(hex; the name sections' ids are decimal: RU `(39 (17 outflow r1_outflow_1)`
is header zone `11`). A non-interior zone without a name is listed as the
converter names it, `FaceGroup<decimal id>`, with its type, and an INFO
MSH_UNNAMED_ZONES says so (GV: 25 FaceGroups, 0 patches before; RU: 6).
On the owner's four files the detector's patch names equal the converted
mesh's (opt-in test). An INFO MSH_TYPE_FROM_FILE names patches whose name
says inlet or outlet (words inlet, outlet, inflow, outflow) while the file
types them wall (SC-Inlet, SV-Outlet, DT_INLET): the file's types are not
final.

Launcher: `scripts/run_machines_app.sh` finds the Python with the app's
packages (VAWT_PYTHON, the remembered one, `~/venvs/*/bin/python`, `~/.venv`,
the app's `.venv`, python3), remembers it, and opens the browser once
`/_stcore/health` answers (wslview, else cmd.exe start; MACHINES_NO_BROWSER=1
to not). Tested with nothing set: it found `/root/venvs/ofpp/bin/python` and
answered in 2 s. `scripts/make_desktop_shortcut.cmd` puts a "Rotating
Machines" shortcut to `run_machines_app.cmd` on the desktop.

### 13.5 Preflight per step, one heavy step at a time, the revolution check in the background (commit 4)

`machines/msh_import/resources.py`: each heavy step of an imported mesh
(conversion, surface, checkMesh, AMI weights, views, one revolution step) is
estimated from the E0 measurements, scaled by the cells (the last checkMesh
of this mesh, else the file's declared count) and the share of hexahedra
(hex-dominant or mixed figures; unknown counts as mixed), with a 1.25
margin. Each step's real peak is read while it runs (the process's VmHWM in
`/proc/<pid>/status`, through the runner's `on_start`; STL runs pass none)
and recorded in `reports/resource_history.json` by the file's content hash;
the next preflight of that mesh uses the larger of the model and the
measured peak x 1.25. Before the operations: the largest step over the free
RAM is BLOCKING (STEP_RAM_BLOCKED, nothing started), over 70 % a WARNING
(STEP_RAM_HIGH); `reports/step_resources.json` lists every step with its
basis. It runs before M1's whole-mesh preflight, which stays for the cell
limit. Weak evidence, written with the figures: one machine, four parts, one
assembly; the cell types alone change them about 2 times.

`machines/heavy_lock.py`: `~/.machines/heavy.lock` (`MACHINES_HEAVY_LOCK`
for another file), the run lock's stale-holder rules, held by an imported
mesh's run while its OpenFOAM operations run and by the revolution check;
its record names the project, the step, when it started and the estimate. A
second run or check waits, cancellably, and says for whom ("francis:
checkMesh, started 14:02, about 3 min left"). STL runs do not take it.

The revolution check (`MachineService.start_revolution`) runs on the app's
own thread (a registry apart from mesh runs), under the project's run lock
and the heavy-step lock, with its progress from the log's "Time =" lines,
the elapsed time and the estimate, and a Cancel (the step stops as
cancelled; AMI_REVOLUTION_CANCELLED in the report). 36 x 10 deg is the
default; when it is estimated over 10 min, 8 x 45 deg (preferred: on a
15-blade runner 8 positions, 30 deg only 4) and 12 x 30 deg are offered with
their estimates (23 M cells: 36 x 10 deg about 64 min with the margin). The
label always states the steps. Real v2512: 8 x 45 deg on the rotating test
mesh, in the background, the lock released afterwards.

### 13.6 Later (owner's future list, M2a)

- Measure STL meshes against the two-level rule before deciding for them;
  measure STL AMI sums and tighten 0.85-1.5 with evidence.
- M2b: when the runner is built from N passage copies, sweep one pitch
  (360/N deg) in about 12 steps, labelled "over one blade pitch (24 deg,
  12 x 2 deg), equivalent to a full revolution because the runner mesh
  repeats every 24 deg".
- M2b: cyclicACMI for partly overlapping interfaces (evidence item); the
  23 M assembly's checkMesh without -allTopology (31.9 GB), baffles counted
  by mergeOrSplitBaffles -detectOnly instead (8 s / 9.2 GB).
- M2c: a decimation grid per part; the zone colours and the cut plane.
- Case setup reads the per-zone quality and, above 70 deg non-orthogonality,
  sets non-orthogonal correctors and limited schemes, each labelled with the
  reason ("max non-orthogonality 81.6 deg in GV").
- The heavy-step lock for STL runs (snappyHexMesh) too.
