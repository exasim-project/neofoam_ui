# Field view + Catalyst 2.1 — what was built, what is missing

Status: **field view implemented** (phase A), **Catalyst: ParaView bridge implemented, real
Catalyst 2 API not available here** (phase B). Follows [`02-dag-stepper-proposal.md`](02-dag-stepper-proposal.md)
§5 "open field in a 3D view" and the parked in-situ part of [`01-ui-spec-dag-and-insitu.md`](01-ui-spec-dag-and-insitu.md) §5.

---

## 1. Field view (phase A)

A **Field** toggle in the toolbar opens a pane next to the activity diagram: the chosen
field on the mesh, redrawn at every pause. On phones the pane stacks under the diagram.

### 1.1 Protocol (worker ⇄ UI, JSON lines, see `worker.py`)

| Direction | Message | When |
|---|---|---|
| UI → worker | `{"cmd": "view", "on": true, "field": "U", "component": "mag\|0\|1\|2"}` | pane opened / field or component changed; `on: false` when closed |
| worker → UI | `{"type": "mesh", "kind", "n", "axes", "bounds", "note", …arrays}` | **once**, the first time the view is open while a solver context exists |
| worker → UI | `{"type": "field", "field", "component", "label", "values", "min", "max", "time", "fields"}` | at a pause, **only if the view is open and the values changed** (CRC of the array) |

- Arrays are base64 little-endian: `float32` for coordinates and values, `int32` for polygon
  offsets/connectivity. pitzDaily: mesh ≈ 460 kB once, a frame ≈ 65 kB.
- Closed view: no geometry is built and nothing is sent.
- The `field` frame is sent just before the `paused` message it belongs to.
- Only **cell-sized** fields are offered: `phi` (faces) and the toy's `flux` are left out.
- An unknown field falls back to `U`, `p`, `T` or the first field, and the worker reports what
  it picked.
- Vectors are sent as magnitude or as one component.

### 1.2 Solver protocol (`engine.Solver`)

Optional `mesh(ctx) -> dict | None`. A solver without it gets no view: the pane says
"provides no mesh geometry". The geometry kinds are in `geometry.py`:

- `line`: `x (n,)`, used by the toy `Heat1D`;
- `polygons`: `points (P,2)`, `offsets (n+1)`, `conn`. Cell *i* is a polygon, in cell order;
- `points`: cell centres `(n,2)`, projected on the two widest axes.

### 1.3 NeoFOAM adapter

pybFoam's `fvMesh` exposes only `C()`, `V()`, `Sf()`, `Cf()` and the counts. It is not a
`polyMesh` in the binding, so `points()`/`faces()`/`owner()` cannot be called on it, and
pybFoam cannot read a `polyMesh` from disk. The adapter therefore:

1. reads `constant/polyMesh/{points,faces,owner,neighbour}` itself (numpy only; ASCII,
   `.gz` and binary `faceCompactList`; `processor<rank>/constant/polyMesh` under MPI);
2. for a one-cell-thick (2D) mesh, takes the boundary faces on the lower plane of the thin
   axis as the cell polygons and reorders them by owner;
3. otherwise falls back to the cell centres `mesh.C()`.

Limits:
- static meshes only (the geometry is cached);
- **MPI: rank 0's subdomain only**, and the UI says so ("rank 0 subdomain only (1 of N)").
  Gathering all subdomains needs the followers to send their values (a `field` op on the
  sync socket) plus their geometry once.
- `k`, `epsilon` and `nut` live in models, not in `ctx.fields`, so they are not listed.

Upstream wish: a `polyMesh` view of `fvMesh` in pybFoam (`mesh.points()`, `mesh.faces()`,
`mesh.owner()`). That would make reading the files unnecessary and cover moving meshes.

### 1.4 Rendering choice: canvas 2D, not VTK

`fieldview.py` holds about 250 lines of JS on a `<canvas>`:
- polygons are batched into 256 colour bins, so one frame is 256 fills (a few ms for 12k
  cells);
- viridis colours, a colour bar, and a "fix range" option;
- wheel zoom, drag pan, double-click reset;
- hover shows the nearest cell's value;
- 1D data is drawn as a line plot, with the previous pause as a dashed ghost.

Why not trame-vtk / trame-vtklocal:
- **No new dependency.** Neither env has VTK. trame-vtklocal needs a VTK wheel whose version
  matches its WASM bundle in the UI env.
- The data is 2D (pitzDaily) or 1D (toy). A WebGL 3D view adds camera handling and weight but
  shows nothing more here.
- The phone layout keeps working: it is a plain element.

When to switch to VTK: real 3D cases or large meshes (≳ 200k cells, where canvas fills get
slow). The natural route is ParaView via Catalyst (§2), or trame-vtklocal fed from the same
`mesh`/`field` messages: `insitu.vtk_grid()` already builds the `vtkUnstructuredGrid`.

### 1.5 Verification

- `tests/test_field_view.py`, 13 tests, no NeoFOAM:
  - the view protocol: closed view sends nothing, mesh once, frames only on change, field
    switching, fallback, vector components, a solver without geometry, the worker `view`
    command;
  - polyMesh reading (ASCII, gz, binary) and front polygons in cell order.
- Screenshots (playwright, Chrome), in desktop 1500×950 and phone 390×844:
  - toy: a line plot;
  - pitzDaily serial: |U| and p polygons, zoom and hover;
  - pitzDaily `mpirun -np 2`: rank 0 subdomain with the note.

---

## 2. Catalyst 2.1 (phase B)

### 2.1 Findings (checked 2026-11-06 on this machine)

| Route | Catalyst 2 runtime (`libcatalyst`, `import catalyst`, `libcatalyst-paraview`) | Notes |
|---|---|---|
| conda-forge `catalyst` / `libcatalyst` | **does not exist** | only unrelated `catalystcoop.*` |
| conda-forge `paraview` 6.2.0 (py312, build `_4`) | **not included** | The recipe doesn't set `PARAVIEW_ENABLE_CATALYST=ON` and has no MPI. It ships `paraview.catalyst` (`bridge.py`, v2 script support), `libvtkPVCatalyst`, `libvtkPVPythonCatalyst` and `paraview.live`. |
| conda-forge `conduit` 0.9.9 | — | `conduit.blueprint.verify` works for explicit coords + `quad`/`polygonal` + element fields. Nothing in conda-forge can consume the node. |
| PyPI | **no** | `catalyst` is the PyTorch library and `conduit` an unrelated package. There are no ParaView or libcatalyst wheels, only `vtk`. |
| Kitware ParaView binaries | **yes** | Local `~/OpenFOAM/ParaView-5.13.0` has Catalyst API 2.0 (`catalyst.about`: version 2.0, abi 2, implementation paraview, MPI). With `catalyst_load/implementation=paraview` a conduit Blueprint mesh → `catalyst.execute` → a ParaView script with a VTPD extractor works, but only under its own pvpython 3.10, which can't be imported into 3.12. The ParaView 6.2 MPI tarball (946 MB) should also have it; not downloaded. |
| Catalyst **2.1** | **nowhere prebuilt here** | It would need building from source (catalyst v2.1.0 is CMake-only), plus ParaView with `PARAVIEW_ENABLE_CATALYST=ON`. |

Interop:
- The NeoFOAM venv is built on miniforge's conda-forge Python 3.12, so it can import conda
  `conduit` and conda ParaView's `paraview.catalyst.bridge` (tested, without OpenFOAM loaded).
- Loading them next to OpenFOAM/pybFoam in the solver process is untested. libstdc++, MPI and
  OpenFOAM's own VTK could clash.

### 2.2 What is implemented: ParaView bridge in the UI process

```
worker (rank 0) ── mesh / field JSON ──► trame UI ──► insitu.CatalystPublisher
                                                    └► paraview.catalyst.bridge.coprocess(grid)
                                                        └► scripts/catalyst_pipeline.py
                                                           (paraview.simple, VTU extractor, Catalyst Live)
```

- `src/neofoam_ui/insitu.py`:
  - `vtk_grid(mesh_msg)` builds a `vtkUnstructuredGrid`: `VTK_POLYGON` cells, or one
    `VTK_VERTEX` per cell centre;
  - `CatalystPublisher` adds the viewed field as cell data and calls `bridge.coprocess` on
    channel `input`.
- `neofoam-ui --catalyst scripts/catalyst_pipeline.py`:
  - publishes at every pause where the values changed, with the pane open or not (the view
    request is forced on);
  - errors disable it and show up in the console instead of stopping the stepper.
- `scripts/catalyst_pipeline.py` is a standard Catalyst-v2-style script:
  - `NEOFOAM_UI_CATALYST_LIVE=host:port`, default `localhost:22222`, `off` to disable;
  - `NEOFOAM_UI_CATALYST_VTU=<dir>` writes one `.pvtu` per pause.
- pixi: optional environment `catalyst` (feature with `paraview = "6.2.*"`, about 2.4 GB). The
  default env is unchanged, but `pixi.lock` now also holds the catalyst env.
  - Run: `pixi run -e catalyst neofoam-ui --catalyst scripts/catalyst_pipeline.py`.
  - Connect with `pixi run -e catalyst paraview` → *Catalyst ▸ Connect* (Live needs the same
    ParaView version on both ends).
- Verified:
  - `pixi run -e catalyst pytest`: `tests/test_insitu.py` writes VTU per pause and reads back
    40 cells with the right `T`;
  - the UI with `--catalyst` on the toy wrote `input_00000{0,1,2}.pvtu` with the pane closed;
  - the pitzDaily geometry gives 12225 `VTK_POLYGON` cells through the bridge;
  - Catalyst Live, 6.2 headless client ↔ bridge script: verified in the research spike, not
    through the UI.

This is **not** the Catalyst 2 API: no `catalyst.execute`, no conduit node. It is ParaView's
bridge running the same kind of script. For a desktop ParaView 6.2 user the result is the
same: a Live connection or extracts.

### 2.3 Smallest path to real Catalyst 2.1 (`catalyst.execute` + conduit Blueprint)

1. **Runtime:** build `catalyst` v2.1.0 (CMake, small) and ParaView 6.x with
   `PARAVIEW_ENABLE_CATALYST=ON` against it. Alternatives:
   - ask conda-forge to add a `libcatalyst` package and turn on that flag in the `paraview`
     feedstock (the cleanest long-term fix);
   - use Kitware's ParaView 6.2 MPI tarball and its pvpython.
2. **Publisher process:** a small `neofoam_ui.catalyst2` script, run under that runtime's
   Python, that reads the same `mesh`/`field` JSON. The UI could tee them to a socket, or the
   worker could gain a second sink. It builds the Blueprint node:
   - `coordsets/coords` explicit x, y;
   - `topologies/mesh` unstructured, `shape: polygonal`, `elements/{sizes,offsets,connectivity}`;
   - `fields/<name>` with `association: element`.
   It then calls `catalyst.initialize` (`catalyst/scripts/script`, `catalyst_load/implementation=paraview`),
   `catalyst.execute` per pause and `catalyst.finalize`. The node layout was verified with
   conduit 0.9.9 (`blueprint.verify`) and ParaView 5.13's Catalyst 2.0.
3. **In the solver instead (MPI-correct):** call `catalyst.execute` from the observer hook on
   every rank with that rank's subdomain. This needs (1) inside the NeoFOAM venv. Risk:
   library clashes with OpenFOAM. Benefit: the whole domain, not rank 0 only.

The scratch environments and scripts from the research spike are not part of the repo. They
are a ParaView 6.2 pixi env, a conduit env, `test_catalyst2.py` (the Catalyst 2.0 conduit
round trip under pvpython 5.13) and `live_client.py`.

---

## 3. Open points

- All ranks in the view: gather follower values (per-rank geometry once, values per pause).
  The client can draw several polygon sets.
- Dynamic meshes: rebuild the geometry when `mesh.changing()`.
- More fields: model fields (`k`, `epsilon`, `nut`) via `ctx.models`.
- `--catalyst` publishes only the viewed field. Publishing every cell field would need one
  `field` message per field, or a `fields` list in the view request.
- The `coprocess` call runs synchronously in the UI event loop. That is fine for small meshes;
  move it to a thread if Live/extracts get slow.
