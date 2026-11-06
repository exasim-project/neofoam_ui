# neofoam_ui — UI specification: interactive solver DAG + in-situ (Catalyst 2.1)

Status: **DRAFT for design agreement** — nothing is implemented yet.
Scope of this document: *what the UI looks like and does* (sections 3–5). Architecture
(sections 2, 6, 7) is only as deep as needed to show the UI is feasible.

---

## 1. Goal and phases

A trame application, shipped as a separately installed **neofoam plugin** package
(`neofoam_ui`), that

1. **Phase 1 — DAG explorer.** Shows the solver's operation graph for a case and lets the
   user click from node to node (predecessors / successors / parent loop / children),
   inspecting what each operation is, where it comes from and what it reads.
2. **Phase 2 — In-situ.** Runs the solver with a Catalyst 2.1 adaptor and shows live fields
   in a 3D view next to the DAG, with the DAG showing live execution status (current op,
   timings).

Phase 1 is built and agreed first. Phase 2 reuses the same layout (an extra tab/pane).

---

## 2. Baseline: NeoFOAM `develop` + plugin branch

| Item | Finding |
|---|---|
| Base | NeoFOAM `develop` @ `c9fd292b8` |
| Plugin branch | **`feat/plugin-entry-points`** (1 commit `4b84cfefb` "feat(core): extend neofoam from separate packages through entry points"). Adds `neofoam/core/entry_points.py` with groups **`neofoam.plugins`** (module imported before every CLI command → registers models/nodes) and **`neofoam.solvers`** (`run(argv)` → `neofoam solver <name>`). Not merged; base is 28 commits behind develop, but **merges cleanly** onto develop. Template user: `neofoam_mdao`. |
| UI plugin hook already on develop | `neofoam.ui.steps` (`src/neofoam/ui/plugins/__init__.py:47`) — adds **wizard steps only** to the existing `neofoam ui` case wizard (`StepPlugin` with `register(ctx)`/`render(ctx)`, `StepContext` gives `server`, `v3`, `html`, …). No hook for a free page/layout. |
| Existing UI stack | trame 3.13, trame-vuetify 3 (`SinglePageWithDrawerLayout`), **trame-flow 2.2 (VueFlow)** already used for the sweep/Snakemake canvases; `tooling/workflow/dag.py` has a layered layout + Graphviz layout producing VueFlow node/edge dicts → directly reusable. No VTK view in the UI today. |
| Solver DAG | `Operation` tree (`framework/operations.py`), resolved by `DAGResolver().resolve(builder, model_ops)` (`framework/graph/resolver.py:353`). Loops (`IterativeOp`, e.g. `time_loop` ⊃ `inner_loop`/PIMPLE) are **containers**, not nodes. Explicit edges only from `depends_on`/`before`; most order comes from step order + `operation_number`. Field data dependencies are implicit (parameter names matched against `ctx.fields`/`ctx.models`, `Depends("fields.X")`). |
| Second DAG | Initialization DAG of `InitStep`s (`framework/initialization/`), buildable **without executing** via `StagedInitRunner.run_load/run_resolve/run_build`. |
| Getting the execution DAG | No "graph from case" API. `solver.execution_graph(ctx)` needs a live `ctx` → a full `solver.initialize()` (mesh + fields + models) is required today. |
| Runtime hooks | None besides telemetry spans per `Operation.run` (OpenTelemetry, `FileSpanExporter` → `case/telemetry/rank*.spans.jsonl`). Time loop: `SolutionLoopPredicate`; per-step hook = an optional model op placed in `time_loop` (like `post_process`). |
| VTK / Catalyst | Nothing in `src/` (only an unmerged `feat/vtkhdf-output`). |
| Packages (conda-forge, today) | trame 4.0, trame-vuetify 3.3, trame-vtklocal 1.8.1, paraview 6.2, conduit 0.9.9. **No standalone `catalyst` package** — Catalyst 2.1 runtime must come from the ParaView build (to verify, see Q6). |

---

## 3. UI specification — overall layout

One page, three panes, vuetify3 `SinglePageWithDrawerLayout`:

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ neofoam ui ▸ [case: ~/run/pitzDaily   📂] [solver: incompressibleFluid ▾]              │
│              [graph: Execution ▾ | Initialization]   [⟳ Build graph]  ⓘ built 12:03 4.1s │
│              (phase 2:)  [▶ Run] [⏸] [■]   t = 0.0125  step 25   ● running             │
├──────────────┬────────────────────────────────────────────────────┬───────────────────┤
│ OUTLINE      │  breadcrumb: time_loop ▸ inner_loop (PIMPLE)       │ INSPECTOR         │
│ 🔍 filter…   │  [◀][▶] history   [fit] [focus: 1-hop ▾] [edges ▾]  │                   │
│              │                                                    │ momentum          │
│ ▾ time_loop  │   ┌─time_loop──────────────────────────────────┐   │ #1.2.1 · fluid    │
│   set_dt     │   │ set_dt → increment_time →                  │   │ SequentialOp      │
│   incr_time  │   │   ┌─inner_loop (PIMPLE) ───────────────┐   │   │ scope: time_loop ▸│
│  ▾ inner_loop│   │   │ mesh_update? → [momentum] →        │   │   │        inner_loop │
│    momentum ◀│   │   │   continuity → viscosity → turb    │   │   │───────────────────│
│    continuity│   │   └────────────────────────────────────┘   │   │ ⬅ Predecessors    │
│    viscosity │   │ → write_output → post_process              │   │  (mesh_update?)   │
│    turbulence│   └────────────────────────────────────────────┘   │ ➡ Successors      │
│   write_out  │                                                    │  (continuity)     │
│   post_proc  │                               ┌minimap┐            │ ⬆ Parent loop     │
│              │                               └───────┘            │  (inner_loop)     │
│ legend:      │                                                    │───────────────────│
│ ■ fluid      │                                                    │ Reads  U p phi nu │
│ ■ turbulence │                                                    │ Writes U (Field…) │
│ ■ viscosity  │                                                    │ Source  pimple…:212│
│ ◇ conditional│                                                    │ Docstring …       │
│ ▢ loop       │                                                    │ Config  {…}       │
│              │                                                    │ Timing (phase 2)  │
└──────────────┴────────────────────────────────────────────────────┴───────────────────┘
       drawer                          canvas (trame-flow / VueFlow)        right pane
```

On narrow screens the inspector collapses to a bottom sheet; the outline stays in the
(toggleable) drawer.

### 3.1 Toolbar
- **Case picker**: text field + directory dialog (reuse the existing dir-picker pattern from
  `neofoam/ui/app.py`). Validates that the dir contains `system/controlDict`.
- **Solver selector**: built-in solvers + plugin solvers (`neofoam.solvers` group).
- **Graph selector**: `Execution` (time-step operations) | `Initialization` (`InitStep`s).
- **Build graph**: runs the graph extraction in a worker (see §6); shows spinner,
  duration, and errors in a snackbar + expandable log. The graph is cached per
  (case, solver, mtime of `system/`/`constant/` dicts).
- Phase 2 run controls appear only when the Catalyst pieces are available.

### 3.2 Outline (left drawer)
- Tree of the operation hierarchy: loops are expandable folders, ops are leaves, ordered
  by execution order / `operation_number`.
- Filter box (substring on name, domain, field names read).
- Selecting in the tree ≡ selecting on the canvas (two-way sync).
- Legend: colours per `domain_name`, shapes per op type (sequential ▭, conditional ◇,
  loop ▢ group, fallback dashed border).

### 3.3 Canvas (centre)
- Renderer: **trame-flow (VueFlow)**, already a neofoam UI dependency.
- **Loops are group nodes** (VueFlow parent/child) that can be **collapsed** to a single
  node (double-click) or **drilled into** (breadcrumb shows the path; only that scope is
  rendered). Default: top level expanded one level deep.
- Node card: op name, `operation_number`, domain colour stripe, small badges
  (◇ conditional, ↺ loop, ⚑ fallback, `?` optional model op).
- **Edge kinds** (toggle in "edges ▾" menu, each a distinct style):
  1. *sequence* — execution order inside a scope (solid grey) — on by default;
  2. *explicit* — `depends_on` / `before` constraints (solid, coloured, arrow) — on;
  3. *data* — inferred field flow "op A writes `U` → op B reads `U`" (dashed, labelled
     with field name) — off by default (can be dense).
- Layout: layered top→down (reuse `tooling/workflow/dag.py` Graphviz `dot` with the
  pure-Python `layered_layout` fallback), computed per scope.
- Minimap + zoom/fit controls (as in the sweep canvas).

### 3.4 Inspector (right pane)
For the selected node:
- **Header**: name, `operation_number`, domain, op type, scope breadcrumb (each crumb
  clickable).
- **Navigation** (the core "click from node to node" requirement):
  - ⬅ Predecessors / ➡ Successors as clickable chips, grouped by edge kind
    (sequence / explicit / data);
  - ⬆ Parent loop; ⬇ Children (for loops);
  - clicking a chip selects that node, pans/zooms the canvas to it and pushes onto the
    navigation history.
- **Reads / Writes**: fields and models the op uses (from the unwrapped function signature
  + `Depends(...)` markers; writes from `FieldUpdates` annotation where determinable). Each
  field name is clickable → highlights all producers/consumers of that field on the canvas.
- **Source**: module, qualname, `file:line` (copy button), docstring, and a collapsible
  code snippet (read-only, syntax highlighted).
- **Config**: values injected by the config wrapper for this op (read-only in phase 1).
- **Timing** (phase 2): last / mean / total time, call count from telemetry spans.

For a loop node additionally: predicate type (e.g. `SolutionLoopPredicate`, PIMPLE
`nOuterCorrectors`), number of children.

---

## 4. Interactions (phase 1)

| Action | Result |
|---|---|
| Click node | select; inspector updates; neighbours highlighted, rest dimmed (per "focus" setting: off / 1-hop / 2-hop / upstream-only / downstream-only) |
| Double-click loop | collapse ⇄ expand |
| Ctrl/⌘-click loop or "drill in" button | render only that scope; breadcrumb updates |
| Click chip in inspector | navigate to that node (select + pan + history push) |
| `←` / `→` | previous / next node in execution order |
| `Alt+←` / `Alt+→` | history back / forward |
| `↑` | go to parent loop |
| `/` | focus filter box |
| Click field name | highlight producers (one colour) and consumers (another) of the field |
| Hover node | tooltip: name, number, domain |
| URL state | `?case=…&solver=…&node=…` so a view can be shared/reloaded |

Out of scope for phase 1: editing the graph, reordering, changing config.

---

## 5. Phase 2 UI — in-situ

Adds a **view switcher** in the canvas header: `Graph | 3D | Split` (default Split when
running: graph left, 3D right; inspector stays).

### 5.1 3D pane
- Renderer: **trame-vtklocal** (WASM, client-side) with fallback to trame-vtk remote
  rendering (Q4).
- Controls: field selector (`p`, `U`, `k`, …), component/magnitude, colour map + range
  (auto / fixed), representation (surface / surface with edges / slice / outline),
  patch visibility list, reset camera.
- Header: simulation time, time-step index, wall-time per step, update rate
  ("every N steps", from the Catalyst script parameters).
- Optional: probe/plot strip — residuals or a scalar over time (from solver log/telemetry).

### 5.2 Live DAG status
- The currently executing op pulses; ops completed in this time step get a tick.
- Heat-map mode: node fill by share of wall time (from telemetry spans).
- Inspector "Timing" section is filled.

### 5.3 Run controls
- ▶ Run starts the solver (in-situ enabled), ⏸ pauses after the current time step
  (Catalyst steering / cooperative flag), ■ stops cleanly (writes current time).
- Log drawer (bottom, collapsible) streams solver stdout (reuse `run_panel.py` pattern).

---

## 6. Architecture sketch (only what the UI depends on)

```
neofoam_ui/                       (this repo, pixi + pip-installable)
  src/neofoam_ui/
    graph/model.py      GraphModel: JSON-serialisable nodes/edges/scopes (pydantic)
    graph/extract.py    case + solver → GraphModel (exec + init DAG)
    graph/layout.py     per-scope layered layout → VueFlow dicts (reuses neofoam dag.py)
    app/                trame app: layout, outline, canvas, inspector, state
    insitu/adaptor.py   phase 2: Catalyst 2 adaptor op (conduit Mesh Blueprint)
    insitu/scripts/     phase 2: Catalyst Python pipeline script(s)
  pyproject.toml
    [project.scripts]                     neofoam-ui = "neofoam_ui.app:main"
    [project.entry-points."neofoam.plugins"]  ui = "neofoam_ui.insitu.model"   (phase 2: registers catalyst model)
    [project.entry-points."neofoam.ui.steps"] dag = "neofoam_ui.app.step:DagStep" (optional: DAG as wizard step)
```

**GraphModel** (the contract between extraction and UI):
```
Node   { id, name, domain, op_type: seq|cond|loop, operation_number, scope: [loop ids],
         depth, fallback, optional, reads: [str], writes: [str],
         source: {module, qualname, file, line, doc}, config: {…} }
Edge   { source, target, kind: sequence|explicit|data, label? }
Graph  { kind: execution|initialization, solver, case, built_at, nodes, edges }
```

**Graph extraction runs in a subprocess** (`python -m neofoam_ui.graph.extract <case>
<solver> --json`), because building the execution DAG requires `solver.initialize()`
(pybFoam, mesh load) — keeps the trame server responsive and isolates crashes. The UI only
consumes the JSON → the UI can be developed/tested with fixture JSON before extraction works.

**Phase 2 data path (proposal, see Q3):** solver runs as a separate (optionally MPI)
process with a `catalyst` optional model whose op sits at the end of `time_loop` and calls
`catalyst.execute(node)` with a conduit Mesh Blueprint (polyhedral unstructured topology
from fvMesh points/faces/cells + cell fields). The ParaView-Catalyst implementation runs a
Catalyst Python script that forwards the (reduced) dataset to the trame UI process; live
op status comes from telemetry spans (tail `spans.jsonl`, later a socket SpanProcessor).

---

## 7. Required changes in NeoFOAM (upstream)

1. Merge/rebase `feat/plugin-entry-points` onto develop (clean merge verified) — needed for
   `neofoam.plugins` (phase 2 catalyst model) and plugin solvers in the solver selector.
2. Small public API: `solver.build_execution_graph(case) -> resolved StepBuilder` (or
   document the 4-line recipe) so extraction does not copy solver internals.
3. Optional: populate `OperationMetadata.description` (today always empty).
4. Phase 2: a pluggable telemetry `SpanProcessor` hook for live status (otherwise tail jsonl).

---

## 8. Milestones (phase 1)

1. **M0 env**: pixi env (python, trame, trame-vuetify, trame-flow, neofoam from source +
   plugin branch). → verify: `python -c "import neofoam, trame_flow"`.
2. **M1 UI on fixtures**: GraphModel + hand-written fixture JSON of incompressibleFluid;
   layout, outline, canvas, inspector, navigation, history. → verify: pytest on
   GraphModel/layout; manual click-through; screenshot.
3. **M2 extraction**: real exec + init DAG from a tutorial case (pitzDaily). → verify:
   pytest comparing node set/order with `DAGResolver` output.
4. **M3 data edges + source/config inspector**. → verify: field-highlight test on fixture.
5. **M4 packaging**: `neofoam-ui` script, optional `neofoam.ui.steps` integration.

Phase 2 milestones are planned after the open questions Q3–Q6 are settled.

---

## 9. Open questions — need agreement

- **Q1 Standalone vs. wizard step.** Proposal: standalone `neofoam-ui` app (own layout; the
  step hook can't host a 3-pane page) **plus** an optional thin `neofoam.ui.steps` step
  that embeds the canvas in the existing wizard. Or propose an upstream `neofoam.ui.views`
  group / `neofoam ui dag` sub-command?
- **Q2 Edge semantics.** Is "sequence + explicit, data edges optional" the right default, or
  should the primary view be the *data-flow* graph (fields as edges or even as nodes)?
- **Q3 In-situ process model.** (a) Solver in a thread inside the trame process — simplest,
  serial only, GIL contention; (b) solver as separate (MPI) process + ParaView-Catalyst
  script forwarding to the trame app — recommended; (c) Catalyst Live to a desktop ParaView
  with trame only for the DAG.
- **Q4 3D renderer.** trame-vtklocal (client WASM, needs data shipped to browser) vs
  trame-vtk remote rendering (server renders, better for big meshes/HPC). Proposal:
  vtklocal default, remote as fallback.
- **Q5 Backend scope.** Phase 2 first for the pybFoam backend (fvMesh) only, NeoN later?
- **Q6 Catalyst 2.1 source.** conda-forge has no standalone `catalyst`; need to confirm the
  conda-forge `paraview` 6.2 build ships `libcatalyst` + the `catalyst`/`catalyst_conduit`
  Python modules at API 2.1, or build Catalyst 2.1 from source in the pixi env.
- **Q7** Should the initialization DAG be in phase 1 (cheap: needs no execution) or later?
