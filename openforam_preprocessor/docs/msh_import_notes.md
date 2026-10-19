# .msh Import (M1-MSH) — E0 Method Notes

| | |
|---|---|
| Milestone | M1-MSH E0, evidence before application code (owner, answer 6: stop after E0) |
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
