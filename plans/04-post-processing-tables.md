# NeoFOAM post-processing tables in the stepper

Status: **implemented and verified on pitzDaily, serial and `mpirun -np 2`** (with and without
the Catalyst field view). Builds on [`03-field-view-and-catalyst.md`](03-field-view-and-catalyst.md),
whose Pipeline pane now shows NeoFOAM's tables next to the ParaView pipeline.

## 1. Data flow

```
solver rank r                                                  UI (trame)
  init step post_processor ─► Session.observe_result(value)
     └─ adapter.observe: isinstance PostProcessor ─► pptables.observe(processor, emit)
          emit {"type": "pptables", "tables": [describe_table(t) …]} ─► Pipeline pane frames
          processor._open wrapped: each table's private (write_control, writer) copy
          gets proxies (_Due times the compute, _Tee forwards to the real writer)
  op post_process ─► PostProcessor.write(ctx) ─► table due ─► compute ─► _Tee.write
     ├─ CsvWriter.write (unchanged: master rank writes postProcessing/<table>.csv)
     └─ emit {"type": "pptable", "name", "time", "ms", "columns", "group_columns",
              "values": [[…] per row], "groups": [[labels] per row]} ──► TableHistory ─► Plots pane
```

- **Nothing in NeoFOAM changes.** The hook relies on `PostProcessor`'s private `_tables`,
  `_open` and the `_TableRuntime` named tuple (`_replace`); a NeoFOAM without them is reported
  once (`stdout`) and runs as before.
- **MPI:** every rank computes (NeoFOAM's aggregators reduce over ranks); followers' `emit`
  drops everything, so only rank 0's already-reduced rows reach the UI.
- **Generic:** the rows are `AggregatedDataSet.headers` / `grouped_values` split into value
  columns and group labels; the diagrams are read off the node objects (plugin discriminator =
  type, other pydantic fields = settings, a field holding a plugin — `not.region`,
  `binary.left/right` — becomes its own node with a labelled edge). Node kind = the
  `neofoam.postprocess.nodes.<module>` it is filed in (`selectors` → selector, …), a case
  script's own node is `custom`. Nothing is keyed on a table, node type or case.
- **NeoN workaround:** the pybFoam `incompressibleFluid` never initializes Kokkos, but the
  post-processing kernels allocate NeoN vectors — `min`/`max`/`mean` abort the process (also in a
  plain `run()`). The adapter calls `neofoam.solver.neon_runtime.ensure_neon_initialized` when a
  case declares tables. Should move into NeoFOAM.

## 2. UI

- **Plots** (toolbar, next to Field/Pipeline; `_ui/panes/_plots.py`, trame-plotly — plotly.js is
  bundled, no network): one chart per declared table, in declaration order. One line per value
  column (a vector: `_0/_1/_2`); several rows per step (bins, residual labels) → one line per row
  and column, capped at 12, with a note. NeoFOAM's `GREAT` (±1e15, empty bin) is a gap. History:
  2000 steps per table, cleared by Restart. Charts refresh at most every 0.5 s while running and at
  every pause; nothing is sent while the pane is closed.
- **Pipeline:** `build_pipeline_flow(catalyst_desc, tables)` — the ParaView frame
  ("ParaView (Catalyst) · view.py") and one frame per table ("NeoFOAM post-processing · <table>",
  last compute time in the title), wrapping at 1700 px. Without Catalyst only the tables.

## 3. Example (pitzDaily)

`examples/pitzDaily/system/postProcess.{yaml,py}`, copied by `scripts/pitzdaily.sh` into a case
that has no `system/postProcess.*`: `p_min/p_mean/p_max`, `magU_mean/magU_max` (mag),
`lowerWall_p/upperWall_p` (patch source + surfIntegrate), `recirculation_Ux` (box selector +
component), `magU_along_x` (directional binning → 6 rows), and from the script a custom
`pressureForce` node (`p S_f`) summed per wall → `pressure_force_<wall>` (3 columns).

## 4. Open

- Per-node timing inside a table (only the whole compute is timed).
- A table that changes its row set every step (residuals) gets a line per label ever seen,
  capped; no smarter selection.
- Many tables make the Pipeline diagram small at fit; zoom in.
