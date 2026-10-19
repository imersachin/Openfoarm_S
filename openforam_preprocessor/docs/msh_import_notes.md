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
- Flow-rate inlets on an imported mesh: the inlet area is not measured from
  the mesh yet, so a flow-rate inlet is BLOCKING `INLET_AREA_UNKNOWN` (use a
  velocity inlet).
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
   `cyclicAMI`. Each AMI patch is therefore made under a new name,
   `<patch>_ami` (reserved); the emptied source patches are removed.
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

## 9. For the owner

- **Schema version: kept at 2, against owner answer 3 (a bump to 3).** The
  existing test `test_g6_case_setup.py::test_physics_is_optional_and_versioned`
  asserts `MACHINE_SCHEMA_VERSION == 2`, and existing tests stay unchanged.
  With version 2, an older application opening a project with
  `imported_mesh` reports an unknown field instead of "written by a newer
  version". The bump is one line plus that assertion, if that test may
  change.
