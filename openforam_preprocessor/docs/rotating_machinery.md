# Rotating Machinery Workflow — Build Specification

| | |
|---|---|
| Status | G0 complete on synthetic geometry (`docs/rotating_machinery_notes.md`); real-geometry items wait for section 17. G1 not started. |
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

- Every body lies inside the domain.
- A rotating body lies entirely inside its rotating zone.
- A stationary body must not cross an interface. A pole that passes through
  the rotating zone has to be split: the part inside rotates (or is a separate
  stationary body inside a zone hole); the decision is per machine (section 17).
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

G0 records which one is used, with evidence.

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

Validation: the union of all domain regions is closed; region names are
valid OpenFOAM patch names; no two regions share a name; every body lies
inside.

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
├── bodies[]                   name, STL source, units, transform, rotating: bool, zone
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

- Time step from rotation: degrees of rotation per step (default chosen in G6
  from evidence), converted to seconds from the rotation speed; or from a
  maximum Courant number.
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
| Imported domain not closed | BLOCKING |
| Binary STL given as named regions | ERROR, with the fix |
| Duplicate or invalid patch names | BLOCKING |
| No inlet, or no outlet | BLOCKING |
| A patch without a type | BLOCKING |
| Body outside the domain | BLOCKING |
| Rotating body not fully inside its zone | BLOCKING |
| Stationary body crossing an interface | BLOCKING |
| Rotating zones overlapping | BLOCKING |
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
- Result checks from V3 extend to every zone: wrong region, empty interface
  patches, missing cell zones, region count equal to the number of
  disconnected zones (evidence from G0).

| Changed setting | Re-runs | Reused |
|---|---|---|
| Inlet velocity, rotation speed, time step, fluid, turbulence | Case setup | All meshes |
| One body's refinement or layers | That zone's mesh, assembly, check, case setup | Other zones, domain |
| Domain size or patch names | Domain mesh, assembly, check, case setup | Zone meshes |
| Patch type only | Case setup | All meshes |
| Body geometry or units | Everything downstream of that body | Other bodies |

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
