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
                          image mode: CaptureWindow on every rank, IceT composites ─► PNG bytes (rank 0)
                          data mode:  Catalyst Live ─► the UI's ParaView (pvpython) ─► trame remote view
  {"type": "frame", "mode": "image", "image": "<base64 PNG>", ...} ───► <img src="data:image/png;base64,...">
  {"type": "frame", "mode": "data", "image": null, ...}           ───► recolour the Live data, redraw
```

- **Two modes, a toggle `image | data` in the pane's bar** (`{"cmd": "view", ..., "mode": "image|data"}`;
  the choice applies to the next frame, switching at a pause renders again).
- **Image mode: no field data passes through the UI.** Rank 0 encodes the composited image in
  memory (`InSitu.execute` → `CaptureWindow` → `vtkPNGWriter.WriteToMemory`); the bytes travel
  in the `frame` message (base64) and the pane shows them as a data URL. No files, no served
  directory. A solver's own `insitu` entry outside the stepper still writes `view_*.png`
  (`$NEOFOAM_UI_CATALYST_OUT`, else `postProcessing/<name>`): with an output directory the
  pipeline adds its PNG extractor and nothing is captured.
- **Data mode: Catalyst Live, trame renders.** Only when the UI runs under the `pvpython` of a
  ParaView 6.0 *with web modules* (`paraview.modules.vtkPVClientWeb`, for trame-vtk's
  `paraview.VtkRemoteView`); otherwise the button is greyed out with the reason. The UI picks a
  free port and passes it to the worker as `NEOFOAM_UI_CATALYST_DATA=localhost:<port>`; it
  listens (`live.ConnectToCatalyst`) when data mode is first chosen, polls the socket every
  20 ms, and listens again after the worker restarts. The pipeline publishes to Live only for
  frames in data mode (it sets `options.EnableCatalystLive` per execute), updates `surface`
  itself (Live sends the extracts as they are) and renders no picture. The UI extracts
  `surface` (default) or `grid` (*volume*, picked in the bar; the pick redraws the current
  frame, `"redraw": true`); only the extracted source travels. Live's pause is not used: the
  stepper steps. `_ui/_live.py` colours the data by the viewed field (Viridis, data range).
  The Catalyst-only build of §2 has no web modules: run the UI with a ParaView 6.0.1 release
  (`~/software/ParaView-6.0.1/bin/pvpython --venv .pixi/envs/default -m neofoam_ui.app …`); the
  worker keeps the Catalyst build. Live's handshake carries the version (6.0), not the build.
- **Whole domain under MPI.** Every rank publishes its own subdomain; ParaView composites.
  Image size, camera and colour range come from an MPI all-reduce in the pipeline, so the
  ranks agree (different image sizes crash IceT's gather).
- **Collective.** Rank 0 decides whether to render (view open, values changed) and tells the
  followers with `{"op": "render", "params": …}` on the sync socket; each rank calls
  `catalyst_execute` on its solver thread (where MPI was initialised).
- **Live frames:** while running, a frame is rendered at every time step (outer loop test) unless the pane's *live* toggle is off (`{"cmd": "live", "on": false}`); then only at pauses.
- **Display options** (`{"cmd": "view", ..., "options": {...}}`, passed to the pipeline as
  parameters; names in `pipelines/view.py`'s docstring). Image mode: the stage's size in device
  pixels (≤ 2 per CSS pixel, reported after 300 ms at rest; the pane's picture has no empty
  bands), camera presets ±x/±y/±z/reset at zoom 1 and zoom (buttons over the picture, the mouse
  wheel), colour range auto/fixed, colour map, log scale (refused with a ⚠ note in the message
  when the range reaches ≤ 0; the pipeline reports it with the domain bounds and the default cut
  axis, `InSitu.report`). Settings rest 250 ms before the ranks render (each frame at a pause is
  collective). A new size refits only the reset camera at zoom 1. Data mode sends only the cut;
  camera and colours are applied by `_ui/_live.py`. All settings survive a restart; a fixed
  range goes back to auto with another field or component.
- **Slice / clip:** *show* = surface | slice | clip (| volume in data mode); axis x/y/z (default:
  the longest in-plane axis of a 2D case, else z), position 0–100 % of the domain (shown as the
  coordinate), clip keeps the lower side unless *flip*. In data mode the cut is the extracted
  source (only it travels).
- **Catalyst Live to a desktop ParaView:** `NEOFOAM_UI_CATALYST_LIVE=host:port` enables Live in
  the pipeline at every frame and renders at every pause even with the pane closed. Connect a
  ParaView 6.0 GUI (*Catalyst ▸ Connect*). Data mode is unavailable then (one Live per run).
- **Fields offered:** the solver's optional `view_fields(ctx)` (default `fields`). The NeoFOAM
  adapter adds every `volScalarField`/`volVectorField` on the mesh registry (`k`, `epsilon`,
  `nut`, …; not `*_0` / `*PrevIter`); the statistics table keeps `ctx.fields`.
- **Switching while running:** the worker sets `Session.view` from its command reader
  (`request_view`), so the next live frame shows it; each frame names the request it answers
  (`view`), and the UI ignores frames for an earlier choice.
- Another pipeline: `NEOFOAM_UI_CATALYST_SCRIPT=<file>` (under the stepper its own extracts go to
  `postProcessing/catalyst` of the case; the image is captured from its first render view) (a Catalyst-v2 script, channel `grid`,
  parameters `field=…` and `component=mag|0|1|2` via `info.catalyst_params`).

- **Post-processing diagram** (toolbar *Pipeline*, `_ui/panes/_pipeline.py`): after each execute rank 0
  reads the live ParaView pipeline back (`InSitu.describe` → `catalyst.describe_pipeline`:
  `GetSources()` and their input properties, `GetViews()` and visible representations,
  `GetExtractors()` and their producers). Nodes carry kind, name, type and a few settings
  (non-default properties; for displays the coloured array, component and colour range; for
  extractors file pattern and trigger). The frame carries `pipeline` when it changed and
  `pipeline_ms` always: the whole execute plus per node from the second execute on (Start/End
  observers on rank 0's algorithms and render windows; displays and extractors are not timed
  separately). `_ui/_layout/_pipeline.py` draws it in a second VueFlow editor, layered by
  data flow. Opening the pane also turns rendering on; any `NEOFOAM_UI_CATALYST_SCRIPT` pipeline
  shows as itself.

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
| ParaView 6's `info.catalyst_params` is the whole execute node, not the parameter list → the `field` parameter was ignored, every frame showed `U` | read `catalyst/state/parameters` from it; hide the previous field's colour bar |

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
passes it (and, when it offers data mode, its Live address `NEOFOAM_UI_CATALYST_DATA`) to the worker.
Unset → the pane says how to enable it; nothing else changes.

Long-term: a Spack/conda recipe for this pair (conda-forge would need a `libcatalyst` package
and `PARAVIEW_ENABLE_CATALYST=ON` in its `paraview` feedstock).

---

## 3. Verification

- `tests/test_field_view.py` (34 tests, incl. the pipeline diagram: sent only when changed,
  only rank 0 describes, a failing description keeps the frames, layout; real ParaView: the
  default and another script described with settings and times): the view protocol with Catalyst faked (frames only on
  change, field fallback, components, no geometry, unconfigured, failing Catalyst, other ranks
  told), polyMesh → polyhedra; with `NEOFOAM_UI_CATALYST` set also a Blueprint `verify` and a
  real ParaView render (polyhedral and line) in a fresh interpreter.
  Run those with the venv: `PYTHONPATH=src $NEOFOAM_PYTHON -m pytest tests/test_field_view.py`.
- pitzDaily in the UI (playwright): serial and `np 2`, several time steps, the image updates;
  under `np 2` both subdomains appear in one image with a global range; Stop leaves no ranks
  and no MPI/EGL errors.

## 4. Open points

- Dynamic meshes: rebuild the geometry when `mesh.changing()`.
- The scalar bar title shows the array name, not `|U|`.
- Pipeline diagram: no per-node time for displays and extractors (only the whole execute);
  times are rank 0's.
