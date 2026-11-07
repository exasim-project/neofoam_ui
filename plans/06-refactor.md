# Refactor: packages by concern, small seams, a visible public API

Status: **done** on `feat/dag-stepper` (after 013ca69). The behaviour is unchanged: the
contract tests (`tests/test_contracts.py`) were recorded before the refactor and still pass.
They cover the trame state and template the browser binds to, the message trace of a
session, the entry points, and a trame-free worker. Largest module before: `app.py`, 1308
lines. After: `engine/_session.py`, 391.

## 1. Rules

- Every module, class, function and constant that is not API starts with `_`. That includes
  private modules (`_session.py`) and the private UI package `neofoam_ui._ui`.
- Every public module has an explicit `__all__`. Every private module has `__all__ = ()`.
  A package's `__init__` is its public face.
- Public modules: `neofoam_ui`, `.engine`, `.protocol`, `.app`, `.extensions`, `.worker`,
  `.parallel`, `.catalyst`, `.geometry`, `.pptables`, `.adapters.neofoam`, `.toy.heat1d`.
  `tests/test_contracts.py::test_public_api` pins them.
- The worker side imports neither trame nor `_ui`: `engine`, `worker`, `parallel`,
  `catalyst`, `pptables`, `geometry`, `adapters` and `toy` run in a solver's interpreter.
- Paths come from the package root (`Path(neofoam_ui.__file__)`), never from a module's own
  directory. This applies to the worker's PYTHONPATH, the Catalyst script and the inspector's
  short paths.
- These strings are contracts and did not change:
  - trame state and trigger names (`fv_on`, `nf_select`, …);
  - message, command and MPI op names (`neofoam_ui.protocol`);
  - the Catalyst channel `grid`;
  - `pipelines/view.py` (loaded by path; its hook names are ParaView's).

## 2. Tree

```
neofoam_ui/
  __init__.py        P  the core re-exported (Op, InitStep, Event, Session, protocols, …)
  protocol.py        P  the process contract: message / command / rank-op names, LOSSY,
                        WORKER_URGENT / UI_URGENT, every NEOFOAM_UI_* environment name
  app.py             P  neofoam-ui: parse_args, main; StepperApp
  extensions.py      P  Pane, PaneToggle, PaneContext, BasePane, ExtensionRegistry
  geometry.py        P  polyMesh → cells (unchanged)
  engine/            P  the stepper core (numpy only)
    _graph.py           Op, InitStep, Event, assign_ids, topological_order
    _wire.py            the graph as `graph` messages
    _protocols.py       FieldSource, GraphSolver, SelfDrivingSolver, ProgramContext,
                        MeshProvider, ViewFieldProvider, ResultObserver, Region, RegionProvider,
                        Renderer, RenderBackend, Peers
    _session.py         Session: the program, stepping, free runs, reports
    _stepping.py        Mode, _until, _free_run: one stop policy for every path and rank
    _tracking.py        _Tracker: timing, call counts, written fields (checksums)
    _stats.py           field stats per rank, merged over ranks, as table rows
    _fieldview.py       _FieldView: view requests, live-frame cadence, frames, pipeline
                        read-back; _CatalystBackend (the default RenderBackend)
    _regions.py         DEFAULT_REGION, a solver as its one region
    _threaded.py        ThreadedProgram
  worker/            P  `python -m neofoam_ui.worker module:Factory`
    __main__.py, _main.py (wiring, main loop), _commands.py (registry), _out.py (writer,
    thinning), _acks.py ("wait for UI"), _stdio.py (fd 1 capture)
  parallel/          P  rank_info, SYNC_ENV
    _ranks.py, _mpi.py (MPI_Finalize), _hub.py (rank 0, implements engine.Peers),
    _follower.py (the other ranks)
  catalyst/          P  InSitu, configured, custom_script, ENV / OUT_ENV / SCRIPT_ENV / LIVE_ENV
    _config.py, _native.py (imports, FP traps), _blueprint.py, _insitu.py, _describe.py
  pipelines/view.py     the Catalyst script (unchanged, loaded by path)
  pptables/          P  observe, describe_table, rows_message, NodeDescriber, register_describer
    _tee.py, _rows.py, _describe.py
  adapters/neofoam/  P  NeoFoamSolver, ObservedNeoFoamSolver, has_observer,
                        incompressible_fluid, neofoam_solver
    _fields.py, _mesh.py (region-aware polyMesh dir), _postprocess.py, _base.py,
    _observed.py (+ _Stepper), _tree.py, _ops.py (op conversion, _GATES), _factory.py
  toy/heat1d.py      P  Heat1D, Heat1DContext, run_plain
  _ui/                  the trame UI (private)
    _app.py             StepperApp: composition root, run status, user actions
    _worker_link.py     spawn / read / send / terminate; per-run image directories
    _pusher.py          capped state pushes, console buffer
    _dispatch.py        message type → handlers (app first, then panes in order)
    _model.py           graph index, names, timings (no trame)
    _shell.py           page, toolbar (pane toggles generated), drawer, grid, bottom
    _styles.py, _format.py
    _layout/            _dag.py, _pipeline.py, _common.py, _palette.py (no trame)
    panes/              _dag.py (+ _dag_view.py, _inspector.py), _field.py, _pipeline.py,
                        _plots.py (+ _table_history.py)
```

## 3. Seams (SOLID) and how to extend

### A solver

Implement the protocols in `neofoam_ui.engine`. All solvers are a `FieldSource` (`name`,
`scalars(ctx)`, `fields(ctx)`). Then either:

- a `GraphSolver` (`init_steps()`, `create_context(results)`, `execution_graph(ctx)`): the
  stepper runs it; or
- a `SelfDrivingSolver` (`program(session)`): it runs itself on a `ThreadedProgram` and
  reports through the `ProgramContext` (`init`, `results`, `set_context`, `set_ops`, `record`, …).

Optional extras:

- `MeshProvider` and `ViewFieldProvider`: the field view.
- `ResultObserver`: sees every init-step result (the post-processing hook uses it).

Run it with `--solver mypkg:Factory`. Capabilities are checked once with `isinstance`.

For a NeoFOAM solver, one factory is enough:
`def my_solver(): return neofoam_solver("neofoam.solver.x.x", "x")`. A solver-specific gate
around an op's function (like `turbCorr()`) is one more entry in `adapters/neofoam/_ops._GATES`.

### A mesh region (CHT, FSI)

A solver that implements `RegionProvider.regions(ctx) -> [Region]` is viewed by its first
region. A `Region` has `name`, `mesh(ctx)` and `fields(ctx)`. Any other solver is one region,
`DEFAULT_REGION = "region0"`.

The geometry cache and field identities are already keyed by region. Unchanged so far:
the wire (`frame`), the Catalyst channel `grid` and the script. Choosing the region in the UI,
and one channel per region, come later as a coordinated change of those three.

The NeoFOAM adapter's polyMesh directory is already region-aware
(`constant/<region>/polyMesh`).

### A renderer

`Session(..., renderer=backend)` takes a `RenderBackend`:

- `unavailable()` (why there is no view, or `None`);
- `publishes_all()`;
- `create() -> Renderer`.

A `Renderer` has `execute(geom, fields, time, params) -> file name` and `finalize()`;
`describe`, `prune` and `ms` are optional. The default is Catalyst (`_CatalystBackend`,
which looks up `neofoam_ui.catalyst` on each call).

### A pane

Subclass `neofoam_ui.extensions.BasePane`:

- `name`, `toggle = PaneToggle(key, label, icon, title)`, `placement = "side"`, `css`;
- `reset()` sets its state defaults; `build()` builds its widgets;
- `handlers()` returns `{message type: fn}`;
- `spawned()` sends its settings to a new worker;
- `layout_changed()` runs when a pane opens or closes;
- `wants_frames()` asks the worker to render field-view frames.

Register it with `registry.add_pane(MyPane)` and `StepperApp(..., registry=registry)`, or
from an installed package:

```toml
[project.entry-points."neofoam_ui.extensions"]
mine = "mypkg:register"   # def register(registry): registry.add_pane(MyPane)
```

Entry points are loaded once, when the app starts, never per event. The toggle button, the
grid's "a side pane is open" class, the "all panes closed" message and the DAG minimap's
visibility are generated from the registered panes. `tests/test_extensions.py` shows a
complete outside pane.

### A post-processing node describer

`neofoam_ui.pptables.register_describer(fn)`, called once in the worker process (e.g. in the
solver factory's module). `fn(obj)` returns `None` ("not mine") or any of `name`, `type`,
`kind` and `settings`. The generic pydantic reading stays the fallback and fills in the rest.

### MPI ranks

`Session.set_peers(peers)` takes an `engine.Peers` (`go`, `free`, `render`, `stats`,
`stop`); rank 0's `_Hub` implements it. A follower is `Session(..., follower=True)`: it
reports nothing and never arms a free run.

Before the refactor, the worker wrapped the session's private generator and the follower
subclass overrode private methods. Both are gone.

### A worker command

Add an `@_command("name")` handler in `worker/_commands.py`, plus the name in
`protocol.COMMANDS`.

## 4. Invariants checked

- **MPI lockstep.** Rank 0 sends `go` before each `next()` of its program, never at session
  start (the followers create their session on the first `go`). A free run gives the
  followers the same parameters, and followers never arm one. The followers render before
  rank 0's own collective render.
- **Thread affinity.** Catalyst and MPI finalization run on the solver thread
  (`in_solver_thread`).
- **Worker JSON protocol.** Unchanged, including the `_FRESH` late serialisation, the
  thinning sets, `sync`/`ack`, and fd 1 output before `paused`/`error`.
- **No per-event cost added.** The `go` hook is a single `None` check when serial; the
  capability checks happen once.
- **Lazy native imports.** Catalyst, ParaView, neofoam and pybFoam are imported only inside
  functions.

## 5. Not done (later)

- Multi-region rendering (region choice in the UI, a channel per region): see §3.
- `pipelines/view.py`'s globals are not underscored. They are a ParaView script's names, and
  `options` and `catalyst_execute` are required by Catalyst.
