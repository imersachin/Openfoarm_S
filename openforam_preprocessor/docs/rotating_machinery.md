# Rotating Machinery Workflow — Build Specification

| | |
|---|---|
| Status | G0 complete on synthetic geometry (`docs/rotating_machinery_notes.md`); real-geometry items wait for section 17. G1 (configuration), G2 (domains and patches), G3 (bodies and zones) and G4 (HAWT preset and pipeline, synthetic rotor) complete. G5 not started. |
| Place this file at | `openforam_preprocessor/docs/rotating_machinery.md` |
| Builds on | `docs/vawt_mesh_generator.md` (V0–V5). V5 must be complete first. |
| Replaces | Nothing. The VAWT workflow becomes one preset of this one. |

---

## 1. Purpose

Generalise the VAWT mesh generator into one workflow for rotating machines:

```text
Bodies (solid STL)  +  Domain (imported or generated)  +  Patches (named, typed)
        → mesh with rotating zone(s)
        → incompressible transient case, ready to run
```

### 1.1 Machines, in order

| Order | Machine | Flow | Domain | New capability needed |
|---|---|---|---|---|
| 1 | Horizontal-axis wind turbine (HAWT) | External, along the rotor axis | Generated cylinder or box | Axial flow; cylindrical domain |
| 2 | Francis turbine runner | Internal, through passages | Imported (casing, guide vanes, runner, draft tube) | Imported multi-part domain; two sliding interfaces |
| 3 | VAWT with pole, any blade shape (J-blade, helical, straight) | External, across the rotor axis | Generated box or cylinder | Several bodies, rotating and stationary |
| Later | Other machines | — | — | Added as presets |

The existing VAWT workflow is kept as a preset and must keep working
unchanged throughout (section 12).

### 1.2 In scope

- One or more solid bodies, each rotating or stationary
- Domain: generated box, generated cylinder, or imported STL (two formats)
- Named, typed patches; inlet and outlet chosen by face
- Rotating zone(s) with sliding (AMI) interfaces
- Case setup for **incompressible, transient flow with a rotating mesh**:
  fields, boundary conditions, fluid properties, rotation, time step
- Everything already built: validation, caching, background runs, UI, reports

### 1.3 Out of scope

- Compressible flow, heat transfer, multiphase, cavitation, free surface
- Running the solver to a solution (the case is prepared, not solved; see section 16)
- Post-processing of results
- Temperature and other transported scalars (section 9.5)

---

## 2. Position in the Project

### 2.1 Hierarchy

For this work, this document ranks directly below `docs/architecture.md`,
alongside `docs/vawt_mesh_generator.md`. `CLAUDE.md` and `docs/architecture.md`
win every conflict; conflicts are reported, not resolved silently.

### 2.2 Scope change the owner must approve

The project rules currently define a mesh preprocessor and exclude CFD setup.
This specification adds case setup for one physics family. Before G6, these
documents need matching edits:

| Document | Change |
|---|---|
| `CLAUDE.md` §1 | Add: prepares ready-to-run incompressible transient cases for rotating machines; still does not run solvers |
| `CLAUDE.md` §3 | "Before rotating-machinery work, also read `docs/rotating_machinery.md`." |
| `CLAUDE.md` §19 | Milestones G0–G8 (section 15) |
| `docs/architecture.md` §3 | New packages (section 4) |
| `docs/architecture.md` §20–21 | Case setup is a separate layer that consumes mesh artifacts and never changes the mesh. A runnable case does not mean a correct solution. |
| `docs/openfoam_basics.md` §17–18 | Fields, `fvSchemes`, `fvSolution` are now generated for the approved physics family only |

All 13 architectural invariants stay. Two are added:

14. Case setup never modifies mesh artifacts.
15. A case that runs is not evidence that its results are correct.

---

## 3. Concepts

| Concept | Definition |
|---|---|
| Body | One solid STL surface: rotor, runner, blade, pole, hub, tower. Each is rotating or stationary. |
| Rotating zone | The fluid region that turns with the rotating bodies. Generated cylinder, or an imported closed surface. |
| Interface | The surface between a rotating zone and the stationary fluid, made into an AMI patch pair. |
| Domain | The outer boundary of the fluid region. Generated (box or cylinder) or imported. |
| Patch | A named part of the boundary with a type: inlet, outlet, wall, rotating wall, symmetry or slip, interface. |
| Physics | Fluid properties, turbulence model, inlet and outlet conditions, rotation speed, time controls. |

### 3.1 Rules

- Every body lies inside the domain. A stationary body may reach through a
  wall or slip face (a pole or tower; the part outside is not meshed), never
  through an inlet or outlet; a rotating body never reaches through any face
  (section 10).
- A rotating body lies entirely inside its rotating zone, and every rotating
  wall lies inside a rotating zone.
- A stationary body must not cross an interface. A pole that passes through
  the rotating zone uses one of two methods, chosen per machine (section 17):
  a split pole whose inner part rotates, or a stationary pole inside the hole
  of an annular zone. A split pole's inner part is never held stationary
  (section 18, decision 4).
- Rotating zones do not overlap.
- Each patch has exactly one type.

---

## 4. Repository Layout

Additive. No existing module is renamed or moved.

```text
openforam_preprocessor/
├── machines/                 general workflow
│   ├── config.py             bodies, zones, domain, patches (section 6)
│   ├── domains.py            generated box and cylinder; imported domain reader
│   ├── patches.py            patch naming, typing, inlet/outlet selection
│   ├── presets/              vawt.py, hawt.py, francis.py, vawt_pole.py
│   ├── validation.py
│   ├── case_generator.py
│   ├── operations.py
│   ├── pipeline.py
│   └── service.py
├── case_setup/               incompressible transient physics (section 9)
│   ├── physics.py            fluid, turbulence, rotation, time models
│   ├── fields.py             0/ files
│   ├── dictionaries.py       controlDict, fvSchemes, fvSolution, dynamicMeshDict, ...
│   └── validation.py
└── app/machines_ui/          Streamlit sections
```

`machines/` reuses `vawt/` modules wherever they already do the job and
generalises them by addition. The UI package is named `app/machines_ui/`, never
the same as a top-level package (see the V5 `vawt.config` import crash).

---

## 5. Domains

### 5.1 Generated box

Length, width, height, and placement relative to the bodies (upstream,
downstream, lateral, vertical distances). Six faces, each assignable a patch
type. Already implemented for VAWT; generalised to any flow direction.

### 5.2 Generated cylinder

Diameter (or radius), length, axis, and placement. Three faces: two ends
and the curved wall. Typical use: HAWT wind tunnel, with inlet and outlet
on the ends.

A cylinder cannot be built as one background block. Candidate methods, to be
proven in G0 on real OpenFOAM:

1. Several blocks arranged around the axis (an O-grid)
2. A box background mesh cut to the cylinder by snappyHexMesh, using the
   cylinder as the domain surface

G0 records which one is used, with evidence. **Used: method 2**, a box cut by
snappyHexMesh, patches from the cylinder surface's regions (section 18,
decision 1).

### 5.3 Imported domain

The user uploads the fluid domain's boundary as closed STL surfaces.
Both formats are supported:

| Format | Patch names come from | Limitation |
|---|---|---|
| One STL with named regions | `solid <name>` … `endsolid` blocks in the file | ASCII STL only: binary STL cannot store region names |
| One STL per patch | The file name (`inlet.stl` → `inlet`) | Files must join into a closed surface |

The app lists the regions it found and their areas, and the user assigns each
a type. A binary STL uploaded as "named regions" is reported as having one
region, with the suggested fix (export ASCII, or one file per patch).

Validation: the union of each part's regions is closed (each part on its own,
section 20); region names are valid OpenFOAM patch names and not reserved;
no two regions share a name, across all imported surfaces; each part's mesh
point lies inside its surface; every body lies inside (G3).

### 5.4 Choice per machine

| Machine | Default domain | Also allowed |
|---|---|---|
| HAWT | Cylinder | Box |
| VAWT, VAWT with pole | Box | Cylinder |
| Francis | Imported | — |

---

## 6. Configuration Model

One frozen, validated model with schema migration. Existing VAWT projects
migrate automatically and mesh identically (section 12).

```text
MachineProjectConfig
├── schema_version, project_name, openfoam_profile
├── machine                    VAWT | VAWT_POLE | HAWT | FRANCIS | CUSTOM
├── flow_axis                  main flow direction (axis minimum → maximum); required
│                              for VAWT and VAWT_POLE, where the box faces and the
│                              preset depend on it
├── bodies[]                   name, STL source, units, transform, motion
│                              (STATIONARY | ROTATING | SPLIT), zone. May be empty
│                              when the rotating walls are regions of imported
│                              surfaces (a Francis runner's blades)
├── rotating_zones[]           name, shape: CYLINDER | IMPORTED, axis, centre, size or STL,
│                              cell size, rotation (section 9.3)
├── domain
│   ├── kind                   BOX | CYLINDER | IMPORTED
│   ├── box / cylinder         dimensions and placement
│   └── imported               format: NAMED_REGIONS | ONE_FILE_PER_PATCH, files
├── patches[]                  name, type, source (generated face or imported region)
├── refinement                 per body, per zone, wake or region boxes and cylinders
├── layers                     per body (relative or absolute sizing)
├── snappy_quality, quality, max_global_cells
├── physics                    section 9 (optional until G6)
└── export
```

Rules:

- Source units are required for every uploaded STL.
- Every field has an entry in the dependency tables (section 11).
- Physics settings never invalidate the mesh (invariant 14).

---

## 7. Patch Types

| Type | Used for | Boundary conditions it implies (section 9.4) |
|---|---|---|
| Inlet | Velocity or flow-rate inlet | U given; p zero-gradient |
| Outlet | Pressure outlet | p given; U zero-gradient with backflow protection |
| Wall | Stationary walls, stationary bodies | No-slip |
| Rotating wall | Rotating body surfaces | Wall moving with the zone |
| Symmetry or slip | Far-field sides of a wind domain | Slip |
| Interface | AMI pairs, created by the pipeline | Coupled; not user-editable |

The exact OpenFOAM condition names are fixed in G0 and G6 from real runs on
v2512, not chosen from memory.

---

## 8. Machine Presets

Presets fill a draft configuration from measured body dimensions. Values are
starting points, never recommendations, and always editable.

| | HAWT | Francis | VAWT with pole |
|---|---|---|---|
| Rotor measure | Diameter D along the plane normal to the axis | Runner outlet diameter (user confirms) | Rotor diameter D, span H |
| Flow direction | Along the rotor axis | Defined by imported inlet and outlet | Across the axis |
| Domain | Cylinder, upstream and downstream lengths in D | Imported | Box (VAWT preset values) |
| Rotating zone | Cylinder (disc) enclosing the rotor | Imported runner-passage surface, or cylinder | Cylinder enclosing rotor and moving part of the pole |
| Interfaces | 1 | 2 (guide vanes → runner, runner → draft tube) | 1 |
| Typical fluid | Air | Water | Air |
| Rotation input | rpm or tip-speed ratio | rpm | rpm or tip-speed ratio |

Upstream and downstream distances for HAWT come from G0 notes, not from
memory; they are recorded with their source.

---

## 9. Physics: Incompressible Transient with a Rotating Mesh

### 9.1 Fluid

| Input | Unit | Preset |
|---|---|---|
| Density ρ | kg/m³ | Air 1.2, water 998 (approximate, about 20 °C) |
| Kinematic viscosity ν | m²/s | Air 1.5e-5, water 1.0e-6 (approximate, about 20 °C) |

Incompressible OpenFOAM solvers use kinematic pressure (p/ρ, in m²/s²). The UI
accepts pascals and converts using ρ, and shows both values.

### 9.2 Turbulence

- Default model: k-ω SST. Laminar as an option.
- Inlet turbulence from intensity I (%) and length scale L (m):
  k = 1.5 (U·I)², ω = √k / (C_μ^0.25 · L), with C_μ = 0.09.
- Wall treatment follows the layer settings; the UI shows an estimated
  first-cell y⁺ when absolute layer sizing is used. Heuristic, labelled as such.

### 9.3 Rotation

- Per rotating zone: axis, origin, speed.
- Speed entered as rpm, or as tip-speed ratio λ for wind turbines:
  ω = λ · U∞ / R. Both values are shown.
- Direction of rotation is explicit (right-hand rule about the axis); never assumed.

### 9.4 Boundary values

| Patch type | Inputs |
|---|---|
| Inlet | Velocity (m/s) for wind; volumetric flow rate (m³/s) or velocity for water |
| Outlet | Static pressure (Pa, converted) |
| Wall, rotating wall, slip | None |

### 9.5 Not in this phase

Temperature and other transported scalars or vectors. Incompressible isothermal
flow has no energy equation. Passive scalar transport is a candidate for a
later milestone, approved separately.

### 9.6 Time controls

- Time step: limited by **both** a number of degrees of rotation per step
  (converted to seconds from the rotation speed) **and** a maximum Courant
  number. Both are configurable; G6 sets their defaults with evidence
  (section 18, decision 5).
- End time in rotor revolutions; write interval in revolutions or degrees.
- Defaults are starting values and are labelled as such.

### 9.7 Files generated

`0/` fields (U, p, and the turbulence fields of the chosen model),
`constant/` physical and turbulence properties and the dynamic mesh
(rotation) dictionary, `system/controlDict`, `fvSchemes`, `fvSolution`.
The solver application and exact dictionary contents are fixed in G6 from a
real run on v2512.

---

## 10. Validation Before Meshing and Before Case Setup

| Check | Severity |
|---|---|
| Any STL units not chosen | BLOCKING |
| Imported domain not closed (the union of its surfaces) | BLOCKING |
| Binary STL given as named regions (detected by size, not by a leading "solid") | ERROR, with the fix |
| Duplicate or invalid patch names | BLOCKING |
| Imported region name invalid, reserved, or used twice (across all parts) | BLOCKING |
| Imported file unreadable | BLOCKING |
| An imported part's mesh point outside its surface | BLOCKING |
| A mesh point on a background-cell face or edge (V0 E4) | ERROR |
| A joint between two regions of the same part | BLOCKING |
| A patch named like a generated one (interfaces, `<body>_rotating`) | BLOCKING |
| Layer checks per body (first layer thicker than the finest cell; minimum thickness above the total) | ERROR |
| Outermost layer far thinner than the finest cell | WARNING |
| Bodies meshed together mixing RELATIVE and ABSOLUTE layer sizing | BLOCKING |
| No inlet, or no outlet | BLOCKING |
| A patch without a type | BLOCKING |
| Body entirely outside the domain | BLOCKING |
| Rotating body crossing the domain boundary | BLOCKING |
| Stationary body crossing an inlet or outlet face | ERROR |
| Stationary body crossing a wall or slip face (a pole or tower; the part outside is not meshed) | WARNING, naming the face |
| Rotating body not fully inside its zone | BLOCKING |
| A rotating-wall patch not inside a rotating zone | ERROR |
| Stationary body crossing an interface | BLOCKING |
| Rotating zones overlapping (zones on the same axis, exact) | BLOCKING |
| Rotating zones on different axes whose bounding boxes overlap (bounding box only) | WARNING |
| Zone clearance to domain or other bodies below a configurable fraction of the cell size | WARNING |
| Rotation speed zero or direction not set | BLOCKING (G6) |
| Time step gives more than a configurable rotation angle per step | WARNING (G6) |
| Estimated Courant number above a configurable limit | WARNING (G6) |

Thresholds are configuration, never constants.

---

## 11. Pipeline and Caching

- One operation per body mesh, per zone mesh, per domain mesh; assembly; check;
  case setup; export. Each cached separately, as in VAWT V3.
- Case setup is its own operation. Changing physics re-runs case setup only;
  the mesh is reused (invariant 14).
- Every joint between separately meshed parts is a cyclicAMI pair,
  stationary joints included. `stitchMesh` is not used (section 18,
  decision 3).
- Result checks from V3 extend to every zone (section 18, decisions 2 and 4).

| Result check | Severity |
|---|---|
| An expected patch (at least inlet and outlet) missing or without faces | ERROR |
| A patch meshed with the wrong OpenFOAM type | ERROR |
| A patch that was not generated (has faces) | WARNING |
| The kept region is not the intended one (e.g. a background-block patch still has faces) | ERROR |
| An interface patch without faces; a missing cell zone | ERROR |
| Region count ≠ separately meshed parts (each joined only by AMI) | INVALID (as V3 `REGION_COUNT_UNEXPECTED`) |
| AMI sum(weights) of any pair outside a configurable range (default 0.85–1.5) | WARNING |

What a change re-runs:

| Changed setting | Re-runs | Reused |
|---|---|---|
| Inlet velocity, rotation speed, time step, fluid, turbulence | Case setup | All meshes |
| One body's refinement or layers | That zone's mesh, assembly, check, case setup | Other zones, domain |
| Domain size or patch names | Domain mesh, assembly, check, case setup | Zone meshes |
| Patch type only | Case setup | All meshes |
| Body geometry or units | Everything downstream of that body | Other bodies |

Patch types (G4, decision H3): the domain and zone meshes are built with plain
patches; the assembly sets the final types (`foamDictionary` on the merged
boundary). A type-only change re-runs the assembly and checks, never a domain
or zone mesh. G6 case setup may take this step over.

---

## 12. VAWT Compatibility

- Every existing VAWT project migrates to the new schema automatically.
- For an unchanged VAWT project, the new pipeline generates byte-identical
  meshing dictionaries to V5, and the existing VAWT tests pass unmodified.
- The VAWT app remains available until the general UI covers everything it does.

---

## 13. User Interface

Same shell as V5 (section navigation, action bar, 3D pane, status strip).
Sections:

| Group | Section | Purpose |
|---|---|---|
| Setup | Project | Name, machine type, OpenFOAM status |
| Setup | Bodies | Upload one or more STLs, units, transform, rotating or stationary |
| Setup | Domain | Box, cylinder or imported; dimensions or uploads; region list |
| Setup | Patches | Table of every patch: name, type, source; inlet and outlet chosen here |
| Setup | Rotating zones | Shape, size, cell size, which bodies it contains |
| Setup | Refinement and layers | Per body and per zone |
| Physics | Fluid and turbulence | Presets, values, units shown |
| Physics | Boundary values | Inlet and outlet values per patch |
| Physics | Rotation and time | rpm or tip-speed ratio, direction, time step, end time |
| Run | Review, Run | As in V5 |
| Results | Mesh, Case, Export | Mesh report; generated case files; downloads |

The 3D pane (V6) shows bodies, domain, zones and patch colours, so the patch
table can be checked by eye.

---

## 14. Testing

Everything in `docs/testing_strategy.md` and `docs/vawt_mesh_generator.md`
section 17, plus:

| Area | Must cover |
|---|---|
| Imported domains | Both formats; binary STL as named regions; open surface; duplicate names; per-file names |
| Cylinder domain | Method from G0; dimensions; patch faces |
| Bodies | Several bodies; rotating and stationary; crossing an interface |
| Physics | Unit conversion Pa → kinematic; k and ω from I and L; ω from tip-speed ratio; time step from degrees per step |
| Case files | Every generated file; determinism; each patch type's conditions |
| Invariant 14 | Physics change re-runs case setup only; mesh files byte-identical |
| VAWT compatibility | Section 12 |
| Real OpenFOAM | Per machine: mesh valid, and the solver starts and completes a few time steps (smoke test, not a solution) |

---

## 15. Milestones

Stop after each. Do not start the next without an explicit instruction.

| | Milestone | Delivers |
|---|---|---|
| G0 | Method proof | On real OpenFOAM v2512, with captured dictionaries and logs: cylinder domain method; imported domain in both formats; HAWT axial rotating zone; two interfaces (Francis-like test passage); pole crossing; a few solver time steps per case. `docs/rotating_machinery_notes.md`. No application code. |
| G1 | Configuration | Model, migration from VAWT, validation without OpenFOAM, presets as data |
| G2 | Domains and patches | Box, cylinder, imported (both formats), patch typing |
| G3 | Bodies and zones | Several bodies, rotating and stationary, interface rules |
| G4 | HAWT | Preset, pipeline, real end-to-end mesh |
| G5 | Francis | Imported multi-part domain, two interfaces, real end-to-end mesh |
| G6 | Case setup | Physics, fields, dictionaries, solver smoke test |
| G7 | UI | Sections in section 13 |
| G8 | VAWT with pole, verification | Preset, real end-to-end mesh; performance against V5 budgets |

VAWT with pole is last because the existing VAWT preset already covers most of
it; G3 adds the pole.

---

## 16. Decisions for the Owner

1. **Pole:** does the pole rotate with the rotor, or is it a stationary tower
   that passes through the rotating zone?
2. **Solver run:** the case is prepared but not solved. Should a later
   milestone add running the solver from the app?
3. **Francis inputs:** will you provide the casing, guide vanes, runner and
   draft tube as separate fluid-domain STLs, or one assembly?
4. **Single passage:** should Francis support one blade passage with periodic
   boundaries (much cheaper), or the full wheel only?
5. **Fluent export:** still required alongside the OpenFOAM case?

## 17. Inputs Needed Before G0

G0 needs real geometry, one set per machine:

- An HAWT rotor STL (blades and hub)
- Francis turbine STLs: runner, and the fluid passage (casing, guide vanes,
  draft tube), in either domain format
- A VAWT rotor with pole, ideally the J-blade design

Each with its units, rotation axis and rotation direction.

---

## 18. Decisions After G0 (owner)

Evidence: `docs/rotating_machinery_notes.md` and `tests/fixtures/machines/g0/`.

1. **Cylinder domain:** a box background cut to the cylinder by
   snappyHexMesh (G0 R1 method b). It is the same path as the box and imported
   domains; patch names and types come from the cylinder surface's regions.
   The O-grid is not used.
2. **R2's silent failures never pass.** In G0 both exited 0 and passed
   checkMesh.
   - Before meshing: a binary STL declared as named regions is an ERROR; an
     open union of domain surfaces is BLOCKING (section 10).
   - After meshing: every expected patch (at least inlet and outlet) exists
     and has faces, and the kept region is the intended one; otherwise ERROR
     (section 11).
   - Both cases are tested in the milestone that builds them.
3. **Every joint between separately meshed parts is a cyclicAMI pair**,
   stationary joints included. `stitchMesh` is not used: in G0 it joined only
   conformal discs, and on non-conformal ones it exited 0 with an invalid
   mesh.
4. **Pole:** two methods are kept, chosen per machine when the real geometry
   is ready:
   - a split pole whose inner part rotates;
   - a stationary pole inside an annular zone.

   The split pole with its inner part held stationary is dropped. **AMI
   sum(weights) becomes a result check** (section 11): a WARNING when any
   pair's minimum or maximum is outside a configurable range.
   - **Default range 0.85–1.5,** from G0. Every G0 mesh with no body crossing
     an interface stayed within 0.90–1.35 over all steps (V0 E1: 0.99–1.37).
     The split pole, with different pole resolution on the two sides, started
     at 0.83 and fell to 0.72.
   - **These are mesh-consistency flags, not accuracy limits.**
   - **How to compute them:** `postProcess -dict <dict with an AMIWeights
     function object> -constant` gives the weights on the finished mesh with
     no solver and no fields. Verified on the G0 R3 mesh: exit 0, the same
     values as the solver's first AMI build.
   - This measures the starting rotor position only. Positions during
     rotation are not checked.
5. **Time step (G6):** limited by both degrees of rotation per step and a
   maximum Courant number, both configurable. G6 sets the defaults with
   evidence. G0 evidence so far: 1° per step gave a maximum Courant number of
   2.5–7.0 on the G0 meshes. The propeller tutorial uses `maxCo 2` with an
   adjustable time step.

## 19. Decisions After G1 (owner)

1. **Body crossing the domain boundary** (section 10): a rotating body →
   BLOCKING; a stationary body through an inlet or outlet face → ERROR;
   through a wall or slip face → WARNING naming the face. A split body
   counts as stationary here: only its stationary part can reach the domain.
   G0 R5's pole runs through the box's z faces (z ±2.3 against ±2.16).
2. **A rotating-wall patch lies inside a rotating zone**, otherwise ERROR: a
   region of an imported domain part (stationary) cannot be a rotating wall.
   A body's rotating wall is covered by the body checks.
3. **Configuration** (section 6): `flow_axis` is a field; `bodies` may be
   empty when the rotating walls are imported regions; zones on different
   axes are compared by bounding box only, so their overlap is a WARNING.

## 20. Decisions for G2 (owner)

E1–E6, approved before G2. Evidence: `tests/integration/test_machine_domains_real.py`.

1. **E1 Background box:** a cylinder domain or imported part is cut from a
   block around its bounding box with 2 cells of margin, plus half a cell so
   that the surface never lies on a background-cell plane (G0 R1 and R2 used
   the half-cell offset): 2.5 cells in all. Cell size: the domain's or part's.
   `background` is a reserved name; snappyHexMesh removes the patch once it is
   empty.
2. **E2 Cylinder facets:** `segments`, default 96 (G0 R1), 24–4096.
3. **E3 Region names** are unique across all imported surfaces: patches and
   joints refer to regions by name alone.
4. **E4 Closedness** is checked per part; the parts are not checked as a
   whole.
5. **E5 Region names are only suggested as types**, never applied.
6. **E6 G1 review findings fixed first:** a migrated VAWT project keeps the
   VAWT checks (AMI without domain, wake outside the domain, mesh point on a
   cell face, layer checks, inside-out bodies, zone cells); `to_vawt` refuses a
   renamed zone or body and reports model errors as `NotVawtConvertible`.

## 21. Decisions for G3 (owner)

F1–F7, approved before G3. Evidence: `tests/integration/test_machine_assembly_real.py`
(OpenFOAM v2512).

1. **F1 Cases:** `domain` (or `domain_<part>`), one `zone_<name>` per cylinder
   zone, and `merged` (mergeMeshes, createPatch, checkMesh, postProcess).
   `machines.assembly.meshing_steps` gives the order.
2. **F2 Interface names:** `<zone>_<outer|inner>_stat` and `_rot` are the
   cyclicAMI pairs. The meshes first carry them as `<name>_src`: createPatch
   moves faces into an existing patch without changing its type, so the pairs
   must be new patches.
3. **F3 Split body:** its inner part is `<body>_rotating` (wall). Both parts get
   about the same cell size: the domain side's levels are raised by
   round(log2(domain cell / zone cell)). The split pole still starts at AMI
   sum(weights) 0.52 (G0: 0.83), so the AMI WARNING fires for it. Where a body
   crosses an interface the weights stay low; this is G8 work.
4. **F4 Zone surfaces:** generated STL for every cylinder zone (`segments`,
   default 96), regions `outer` and `inner`. The hole's wall is refined to
   `hole_level` (default 2, as G0 R5) on the zone side and to the same cell
   size on the domain side. Without it the hole pair's weights fell to 0.24;
   with it they are 0.99997–1.004, and the face counts (2592, 4480) equal G0's.
5. **F5 AMI weights:** `postProcess -dict system/amiWeightsDict -constant` on
   the merged mesh; WARNING outside 0.85–1.5 (configurable). Starting position
   only.
6. **F6 Scope:** imported zones and joints are G5. Bodies and cylinder zones
   are checked against imported domain parts; a crossing names the nearest
   region and follows section 19, decision 1.
7. **F7 Layers:** per body, per-patch values in snappyHexMesh; one sizing per
   mesh. Verified: two layered bodies in one zone both got their layers.

## 22. Decisions for G4 (owner)

H1–H6, approved before G4. Evidence: `tests/integration/test_hawt_pipeline_real.py`
(OpenFOAM v2512) and `tests/unit/machines/test_machine_pipeline.py`.

1. **H1 HAWT preset values** (`machines/presets/hawt.py`): the G0 R3 test values,
   each stored with its source, "G0 R3 test value; unverified, not a
   recommendation": 2D upstream, 5D downstream, radius 2D, domain cells D/8;
   zone 1.2D, axial margin 0.04D, cells D/40. Replacing them with cited values
   changes only that table.
   The rotor is measured about its rotation axis (`hawt_rotor`): the axis passes
   through the area-weighted centroid of the surface (exact for a rotationally
   symmetric rotor; can be given instead), and D is the swept diameter. The
   bounding box used for VAWT puts a 3-blade rotor's centre 0.125 m off the
   axis. On the G0 rotor the preset reproduces G0 R3's domain and zone.
2. **H2 Pipeline** (`machines/pipeline.py`, `machines/operations.py`): built on
   the V3 pieces; VAWT keeps its own. One cached operation per domain case and
   per zone case, then assembly and checks. Verified on real OpenFOAM: a
   second run reuses every operation; a patch-type change re-runs only the
   assembly and checks; a body's refinement re-runs its zone mesh, the
   assembly and checks, and reuses the domain mesh.
3. **H3 Patch types:** see section 11.
4. **H4 Project store:** `.preprocessor/machines/` (project, history,
   last_meshed).
5. **H5 Service layer:** G7.
6. **H6 Real geometry:** G4 ran on the synthetic G0 HAWT rotor; no real HAWT
   STL yet. The same test runs on one when it is provided.