# Field view in situ through Catalyst 2.1

Status: **implemented and verified on pitzDaily, serial and `mpirun -np 2`.** Replaces the first
version, which shipped every frame to the browser as base64 float32 arrays and drew it on a
canvas. That does not scale to CFD meshes and showed rank 0's subdomain only. Follows
[`02-dag-stepper-proposal.md`](02-dag-stepper-proposal.md) §5 and
[`01-ui-spec-dag-and-insitu.md`](01-ui-spec-dag-and-insitu.md) §5.

---

## 1. How it works

```
solver rank r (worker)                                 UI (trame)
  pause ─► Session.send_view (rank 0 decides)
           ├─ hub.render(params) ──► followers: Session.render(params)
           └─ Session.render(params)            │ (same frame, all ranks, solver thread)
                └─ catalyst.InSitu.execute ◄────┘
                     conduit Blueprint node: this rank's polyhedra + every cell field,
                     arrays passed by pointer (no copy)
                     └─ libcatalyst-paraview ─► pipelines/view.py (paraview.simple)
                          IceT compositing over all ranks ─► view_<step>.png (rank 0)
  {"type": "frame", "file": "view_000003.png", ...} ───────────► <img src="insitu/run1/...">
```

- **No field data passes through the UI.** The only thing sent per pause is the PNG file name.
  The UI serves the directory (`insitu/`, a temp dir per UI, one `run<N>` per worker start).
- **Whole domain under MPI.** Every rank publishes its own subdomain; ParaView composites.
  Image size, camera and colour range come from an MPI all-reduce in the pipeline, so the
  ranks agree (different image sizes crash IceT's gather).
- **Collective.** Rank 0 decides whether to render (view open, values changed) and tells the
  followers with `{"op": "render", "params": …}` on the sync socket; each rank calls
  `catalyst_execute` on its solver thread (where MPI was initialised).
- **Catalyst Live:** `NEOFOAM_UI_CATALYST_LIVE=host:port` enables Live in the pipeline and
  renders at every pause even with the pane closed. Connect a ParaView 6.0 GUI (*Catalyst ▸
  Connect*).
- Another pipeline: `NEOFOAM_UI_CATALYST_SCRIPT=<file>` (a Catalyst-v2 script, channel `grid`,
  parameters `field=…` and `component=mag|0|1|2` via `info.catalyst_params`).

### 1.1 Geometry (`geometry.py`)

pybFoam's `fvMesh` exposes no points/faces, so the adapter reads `constant/polyMesh`
(`processor<rank>/constant/polyMesh` under MPI; ASCII, `.gz`, binary) and turns it into
Blueprint `polyhedral` cells with `polygonal` subelements: owner and neighbour lists give each
cell its faces. 3D as well as 2D; the pipeline looks along the thinnest axis. Without polyMesh
files the cell centres are published as points; the toy's 1D mesh becomes a rectilinear line.

### 1.2 Pitfalls found on the way

| Problem | Fix |
|---|---|
| Kitware's ParaView binaries compile their Python modules into `pvpython` (`_paraview_modules_static`), so they cannot run inside the solver's Python; conda-forge ParaView ships without Catalyst | build ParaView from source for the NeoFOAM venv (§2) |
| OpenFOAM traps FP exceptions (`FOAM_SIGFPE`); VTK overflows on purpose → SIGFPE | `_no_fp_traps()` around every Catalyst call |
| Per-rank bounds/range → IceT gathers images of different sizes → segfault | all-reduce in the pipeline |
| Vector components as separate arrays → VTK SOA arrays, copied on `GetVoidPointer` | strided conduit schema into the one `(n, 3)` array (AOS, no copy) |
| ParaView's static destructors call `MPI_Comm_free` after `MPI_Finalize` → mpirun abort | ranks leave with `os._exit` after a clean stop |

---

## 2. Runtime (built 2026-11-07)

Install prefix `~/software/paraview-catalyst/install` (482 MB), all for the NeoFOAM venv
(miniforge Python 3.12.9, NumPy 2.5) and OpenFOAM's system OpenMPI 4.1.6:

1. **Catalyst 2.1.0** (`gitlab.kitware.com/paraview/catalyst`, tag `v2.1.0`):
   `-DCATALYST_WRAP_PYTHON=ON -DCATALYST_USE_MPI=ON`.
2. **ParaView 6.0.1**, edition `CATALYST_RENDERING`, headless EGL, no Qt:
   `-DPARAVIEW_ENABLE_CATALYST=ON -Dcatalyst_DIR=<prefix>/lib/cmake/catalyst-2.1
   -DPARAVIEW_USE_PYTHON=ON -DPARAVIEW_USE_MPI=ON -DPython3_EXECUTABLE=<venv python>
   -DVTK_USE_X=OFF -DVTK_OPENGL_HAS_EGL=ON -DPARAVIEW_USE_QT=OFF`. About 18.8k targets with
   `-j8`; source and build trees are in `~/software/paraview-catalyst/{src,build}`.

Use: `NEOFOAM_UI_CATALYST=<prefix>` in the UI's environment (or `--catalyst <prefix>`). The UI
passes it and the image directory (`NEOFOAM_UI_CATALYST_OUT`) to the worker. Unset → the pane
says how to enable it; nothing else changes.

Long-term: a Spack/conda recipe for this pair (conda-forge would need a `libcatalyst` package
and `PARAVIEW_ENABLE_CATALYST=ON` in its `paraview` feedstock).

---

## 3. Verification

- `tests/test_field_view.py` (19 tests): the view protocol with Catalyst faked (frames only on
  change, field fallback, components, no geometry, unconfigured, failing Catalyst, other ranks
  told), polyMesh → polyhedra; with `NEOFOAM_UI_CATALYST` set also a Blueprint `verify` and a
  real ParaView render (polyhedral and line) in a fresh interpreter.
  Run those with the venv: `PYTHONPATH=src $NEOFOAM_PYTHON -m pytest tests/test_field_view.py`.
- pitzDaily in the UI (playwright): serial and `np 2`, several time steps, the image updates;
  under `np 2` both subdomains appear in one image with a global range; Stop leaves no ranks
  and no MPI/EGL errors.

## 4. Open points

- Interactive view in the browser (rotate/zoom): ParaView Live to a desktop works today; in the
  page it would need remote rendering (trame + ParaView) attached to the in-situ pipeline.
- Dynamic meshes: rebuild the geometry when `mesh.changing()`.
- Model fields (`k`, `epsilon`, `nut`) via `ctx.models`.
- The scalar bar title shows the array name, not `|U|`.
