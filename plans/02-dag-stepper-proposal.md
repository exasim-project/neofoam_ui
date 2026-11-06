# Proposal: DAG stepper — execute the solver graph step by step (Cameo-style)

Status: **PROPOSAL — decisions of 2026-11-06 applied (§0)** · supersedes the phase-1 scope of
[`01-ui-spec-dag-and-insitu.md`](01-ui-spec-dag-and-insitu.md) (in-situ/Catalyst is parked).

Clickable mockup: [`mockups/dag-stepper.html`](mockups/dag-stepper.html) — open in a browser
(no server needed). `…/dag-stepper.html#steps=13` opens it after 13 steps.
The mockup uses fake data. The node names, nesting and order are taken from
`incompressibleFluid.execution_graph` (`src/neofoam/solver/incompressibleFluid/incompressibleFluid.py:138-206`).

---

## 0. Decisions (2026-11-06)

| # | Question | Decision |
|---|---|---|
| Q1 | Stepping granularity | **Operations only**: pause before each op and each loop test. No solver refactoring. |
| Q2 | Diagram renderer | **VueFlow (trame-flow)**: loops drawn as group nodes styled like activity-diagram frames. |
| Q3 | Initialization | **Step through the init DAG too**: an *Initialization* activity (InitSteps) runs before the time loop. |
| Q4 | Parallel | **MPI runs supported**: all ranks pause together at every event. |
| Q5 | Editing state | **Read-only first**: inspect only, edits come later. |
| Q6 | Ops injected by models | **Marked visually** with an "injected by model X" badge. |

The HTML mockup is a hand-drawn SVG sketch made before Q2 was decided. It shows the
interaction, not the final rendering.

---

## 1. Idea

Show the solver's operation graph as a **UML activity diagram** and let the user
**execute it one operation at a time**, the way Cameo/MagicDraw *Simulation* animates an
activity: a token sits on the next action, the user steps, the action runs for real on the
live solver, the diagram/variables update, the token moves on. Breakpoints, step
into/over/out and "run to here" work like a debugger.

What the user gets:
- understand *what runs when* (time loop ⊃ PIMPLE loop ⊃ momentum, continuity, …);
- watch the effect of each operation on `ctx` (fields, residuals, time);
- stop exactly before an operation to inspect state (and later: attach Python debugger,
  dump a field, open in ParaView).

---

## 2. Notation (UML activity → NeoFOAM)

| UML / Cameo | NeoFOAM | Mockup |
|---|---|---|
| Initial node ● | session start: solver instantiated, case read | black dot |
| Activity *Initialization* | the init DAG: `InitStep`s (`mesh`, `fields.U`, …) in dependency order, each one an action | (not in mockup) its own activity in front of the time loop; fork/join where steps are independent |
| Action (rounded rect) | `Operation` with `SequentialOp` | yellow card, domain colour stripe, `operation_number` |
| Structured **loop node** (frame with test + body) | `Operation` with `IterativeOp` (`time_loop`, `inner_loop`) | blue frame «loop», test text in header |
| Decision ◇ `[true]/[false]` | the loop predicate call (`SolutionLoopPredicate`, `pimple.loop()`) | diamond at frame top, dashed back edge |
| Guard on action | `_under_turb_corr` gate (`turbCorr()`) | `[turbCorr()]` on the card |
| Stereotype «injected» | op placed by the resolver from an optional model (`model_ops`) | (not in mockup) badge `⊕ injected by <model>` and dashed border |
| Control flow edge | execution order in a `StepBuilder` scope | solid arrows |
| Activity final ◉ | time loop predicate returned false (end time reached) | bullseye |
| Token / active element | the operation about to execute | red outline + red token dot |
| Breakpoint | breakpoint on an op or on a loop test | red dot in the left gutter |

`ConditionalOp` (a predicate in the op list) would be drawn as a decision ◇ whose result is
shown in the console. `incompressibleFluid` doesn't contain one.

---

## 3. Screen layout

```
┌ header: case │ solver │ graph: Execution ▾ │ ● initialized (mesh 12 225 cells, 4.1 s) ───────────┐
├ toolbar: ▶Run ⏸Pause ■Stop ⟲Restart │ ⤓Step into ↷Step over ⤒Step out ⏭Next time step │ speed ┤
│                                                     status: paused before ◇ inner_loop test · 13/49│
├───────────────┬──────────────────────────────────────────────┬─────────────────────────────┤
│ CONTAINMENT   │          ●  solver.initialize() ✓            │ INSPECTOR                   │
│ ↺ time_loop   │  ┌«loop» time_loop · iteration 1/3────────┐  │ name, number, type, domain, │
│   set_time_…  │  │test: SolutionLoopPredicate()     ◇◄┐   │  │ source file:line, docstring │
│   increment…  │  │      [set_time_step]               ┆   │  │ Navigate ⬅ ➡ ⬆ ⬇ (chips)    │
│  ↺ inner_loop │  │      [increment_time]              ┆   │  │ Reads / Writes (fields)     │
│    momentum   │  │  ┌«loop» inner_loop (PIMPLE)───┐   ┆   │  │ Session: calls, last time,  │
│  ● continuity │  │  │          ◇ ◄┐               │   ┆   │  │  [Add breakpoint][Run to]   │
│    viscosity… │  │  │   [momentum]┆               │   ┆   │  ├─────────────────────────────┤
│    turbulen…  │  │  │ ●[continuity]               │   ┆   │  │ CALL STACK                  │
│   write_out…  │  │  │  …          ┘               │   ┆   │  │ ◇ inner_loop test #3        │
│   post_proc…  │  │  └─────────────────────────────┘   ┆   │  │ ↺ time_loop [1/3]           │
│ LEGEND        │  │      [write_output] [post_process] ┘   │  ├─────────────────────────────┤
│               │  └────────────────────────────────────────┘  │ BREAKPOINTS                 │
│               │          ◉ end of run                        │ ● continuity   enabled      │
│               ├────────────────────────┬─────────────────────┤                             │
│               │ SIMULATION CONSOLE     │ VARIABLES (ctx)     │                             │
│               │ [t=…] scope ▶ op (ms)  │ time dt index outer │                             │
│               │ [t=…] ◇ loop → true    │ residuals           │                             │
│               │ ● breakpoint hit: …    │ field min max mean  │                             │
│               │                        │ last-written-by     │                             │
└───────────────┴────────────────────────┴─────────────────────┴─────────────────────────────┘
```

On narrow screens the panes stack. Diagram, containment tree and inspector stay in sync:
clicking a node in any of them selects it in all three.

### 3.1 States
`no case` → `starting` (spawn ranks, read case) → **`paused`** ⇄ `running` → `finished` | `error`.
The session starts paused at the **first init step**. The canvas shows two activities stacked:
*Initialization* (InitSteps), then *Time loop*. A "Skip initialization" action runs every init step
and pauses at `time_loop` test #1.
On `error` the token turns into ✖ on the failing op and the traceback shows in the console. Restart is
then the only action.

### 3.2 Colours / marks (Cameo conventions)
- **red outline + token**: next element to execute;
- **green**: executed in the current time step (reset at each `time_loop` test);
- **red frame**: loops on the current call stack, with `iteration i/N` in the header;
- **red gutter dot**: breakpoint (click the gutter to toggle);
- **yellow row** in Variables: fields written by the last executed op.

---

## 4. Controls and stepping semantics

The engine pauses at **events**. There are three kinds:
- **init**: just before an `InitStep` runs;
- **exec**: just before an action runs;
- **test**: just before a loop predicate is evaluated.

| Control | Key | Semantics |
|---|---|---|
| Step into | F11 | Run the current event and pause at the next one. At a loop test that is true, the next pause is inside the body. |
| Step over | F10 | At an action: same as step into. At a loop test: run the **whole loop** and pause at the element after it. |
| Step out | ⇧F11 | Finish the current loop iteration and pause at the enclosing loop's next test. |
| Next time step | — | Run until the next `time_loop` test. |
| Run | F5 | Run until a breakpoint, the end, or Pause. With an animation delay > 0 the token visibly moves. With 0 it runs at full speed and only redraws at pauses. |
| Pause | — | Stop after the current operation finishes. An op is never interrupted mid-way. |
| Run to here | inspector | Like a temporary breakpoint on the selected node. |
| Stop | — | Terminate the session (the solver process ends). |
| Restart | — | New solver process, `initialize()`, paused at the first event. |

Breakpoints: on an action or a loop test. Phase 2 options:
- conditional breakpoints (`ctx.time.index >= 10`, `outer == 2`);
- hit counts;
- a "break on field written" watchpoint (`p`).

**No stepping backwards.** The solver mutates fields in place, so the engine has no undo.
Restart is the only way back. Possible later: snapshot `ctx` at a breakpoint and restore it.

---

## 5. Panes in detail

**Inspector:** carried over from plan 01, plus a *Session* section:
- call count and last/mean wall time;
- buttons to add or remove a breakpoint, and Run to here;
- for loops: the test text, the current iteration and the number of children.

**Call stack:** the innermost first:
- the current event (`▶ momentum`, or `◇ inner_loop test #3`);
- then each enclosing loop with its iteration (`↺ time_loop [1/3]`).

Clicking a frame selects that loop.

**Simulation console:** one line per event: `[t=0.0005] time_loop#1 › inner_loop#2 ▶ momentum (16.5 ms)`.
- Loop tests are shown with their result.
- Breakpoint hits are highlighted.
- Solver stdout (residual lines from the linear solvers) is interleaved, collapsible per op.

**Variables (ctx)**, refreshed whenever the engine pauses:
- scalars: `time`, `deltaT`, `timeIndex`, PIMPLE `outer` corrector index, the latest residuals;
- one row per field in `ctx.fields`: min, max, mean, and "last written by". The engine records which op wrote each field.

Phase 2 additions: per-field norms of the change since the previous pause; expanding a row shows
per-patch values; a button to "open field in a 3D view" (this links back to the parked in-situ plan).

---

## 6. How it works (feasibility)

```
 trame UI process                                  solver worker process (pybFoam / NeoN)
 ─────────────────                                 ────────────────────────────────────────
 GraphModel (JSON)  ◄──── graph + node metadata ──  instantiate → initialize → execution_graph
 controls  ───── cmd: step_into|step_over|… ───►   DAGResolver().resolve(...) → ops tree
 console/vars ◄─ event: {kind, node, iter, stack,  run ops under a StepController
                         dt_ms, ctx_summary}  ──   (pauses on a threading/queue gate)
```

- **Separate process(es).** `initialize()` loads pybFoam/the mesh. A crash or a segfault must
  not take the UI down, and Stop/Restart must be able to kill the run. The worker is started as
  `mpirun -np N python -m neofoam_ui.worker <case> <solver>`; N = 1 for serial.
- **MPI.** Only **rank 0** talks to the UI (JSON lines over a socket). At each event, rank 0 waits
  for a UI command and broadcasts it with `comm.bcast`, so every rank passes the gate in lockstep.
  Every op stays collective on every rank, as in a normal run. At a pause, the ctx summary is
  reduced with `allreduce` (min, max, sum/count) and rank 0 sends it. Stop broadcasts a
  terminate command and all ranks exit cleanly. Per-rank values (e.g. cells per rank) can go into
  a "rank" column in the Variables pane later.
- **One choke point.** Every op runs through `Operation.run` (`src/neofoam/framework/operations.py:104`).
  Loops call `self.func(ctx)` (the predicate) and then `op.run` on each child
  (`operations.py:114-125`). A `StepController` therefore needs two hooks:
  1. `before_op(op, stack)` and `after_op(op, dt)` around `Operation.run`, to produce the exec
     events, the timing and the "last written by" record;
  2. `before_test(loop_op, iter)` and `after_test(result)` around the predicate call.

  The prototype can wrap these by monkeypatching from the plugin. Upstream they belong in a
  small **execution-observer API** next to the existing telemetry span in `Operation.run`
  (the preferred route; see §8).
- **Init stepping.** The init DAG is ordered by `_topological_sort` and run step by step via
  `execute_step(step, context)`
  (`src/neofoam/framework/initialization/execution/executor.py:16`). Gating `execute_step`
  produces the *init* events. The worker builds the steps with
  `StagedInitRunner.run_load/run_resolve/run_build`, so it can send the init graph **before**
  anything executes. The time-loop graph is only known after initialization (it needs `ctx`), so
  that activity appears once the last init step has run, and is shown as a placeholder until then.
- **Injected ops.** The worker tags every op that came from `model_ops`, i.e. optional models,
  with its model name before `DAGResolver().resolve(...)` merges it. The tag travels in the
  GraphModel as `injected_by`.
- **Graph without a separate extraction step.** The worker already holds the resolved ops tree
  after `initialize()`, so it sends the GraphModel itself. The plan-01 "extract" subprocess is
  folded into the worker.
- **ctx summary.** Cheap reductions only, computed at a pause and not after every op while
  running: `np.asarray(field.internalField())` → min/max/mean (the same access the postprocess
  nodes use, `postprocess/node.py:165`).
- **trame side.**
  - The diagram uses **VueFlow via trame-flow** (Q2), with custom node templates as in
    `sweep_view.py`:
    - `action` (rounded card);
    - `decision` (diamond);
    - `loop` (group node with a header and the test text);
    - `initial` and `final`.
  - Child positions are relative to their parent loop's group node. The layout is computed in
    Python (vertical stacking per scope), and the token/active state is a node `class` updated from
    state.
  - Back edges use a smoothstep edge with a custom path offset.
  - Pan, zoom and minimap come with VueFlow.
  - The worker's events go into `state` through an asyncio reader task, the same pattern as
    `run_panel.py`.

---

## 7. Packaging (unchanged from plan 01)

- `neofoam_ui` is a separate package with the console script `neofoam-ui`, built on NeoFOAM
  `develop` plus `feat/plugin-entry-points`.
- The stepper itself needs **no plugin entry point**: it only drives a solver.
- `neofoam.solvers` is used to list plugin solvers in the solver selector.
- Later, an optional `neofoam.ui.steps` step links from the existing case wizard.

---

## 8. Upstream changes in NeoFOAM

1. `feat/plugin-entry-points` merged into develop (it merges without conflicts).
2. **Execution observer** (it also covers init steps: `on_init_step(step)` in `execute_step`):
   - `neofoam.framework.operations.set_observer(obs)`, with `on_enter(op)`, `on_exit(op, dt)`,
     `on_test(loop, iter, result)`, `on_init_step(step)`;
   - a no-op by default, so it costs nothing when unused;
   - called in `Operation.run` and `Operation._execute`;
   - it is also what a future live in-situ status would use.
3. A public helper to build the resolved graph for a solver: today it is a four-line recipe
   inside each solver's `run()` (`incompressibleFluid.py:209-244`).

**Status (2026-11-06).** Submitted as stacked draft PRs (stack #464):
[#461](https://github.com/exasim-project/NeoFOAM/pull/461) plugin entry points ←
[#462](https://github.com/exasim-project/NeoFOAM/pull/462) empty-argv segfault fix ←
[#463](https://github.com/exasim-project/NeoFOAM/pull/463) `execution_observer` +
`SolverRuntime.resolve_operations`. The hooks are `init_steps`, `before/after_init_step`,
`operations`, `before/after_operation` and `before/after_loop_test`.

---

## 9. Milestones

1. **M1 UI on a recorded trace.** trame app: diagram, tree, inspector, toolbar, console,
   variables. It replays a JSON trace recorded from a real run, so it works without a solver.
   → verify: pytest for the stepping semantics on the trace (into/over/out/run-to/breakpoints);
   screenshot compared against this mockup.
2. **M2 worker + StepController (monkeypatch), serial.** Live stepping through init + time loop
   on `pitzDaily` / `incompressibleFluid`. → verify: an integration test where 1 time step
   executed by stepping gives the same fields as an uninterrupted run.
2b. **M2b MPI.** The rank-0 gate with `bcast` and the `allreduce` summaries. → verify: the same
   comparison with `mpirun -np 2` on a decomposed `pitzDaily`, and Stop leaves no hanging ranks.
3. **M3 ctx summary + "last written by" + timings.**
4. **M4 upstream observer API**, replacing the monkeypatch.

**Status (2026-11-06).**
- M1–M3 and M4 are done. The real solver's `run()` executes in a thread (`engine.ThreadedProgram`)
  and pauses in the observer hooks. Tree interpretation is still available for NeoFOAM without
  the observer (`NEOFOAM_UI_MODE=tree`).
- M2b is done, without mpi4py (`neofoam_ui.parallel`):
  - rank 0 releases the follower ranks over a local socket, one `go` per event;
  - field min/max/mean are merged over all ranks;
  - on Stop every rank calls `MPI_Finalize`.
- Verified on pitzDaily, serial and `mpirun -np 2`: stepping every event gives fields byte-identical
  to a plain run, Stop exits cleanly, and Restart leaves no ranks behind.

---

## 10. Open points

- **Back edges in VueFlow.** A loop's back edge has to route around its group node. Check that
  trame-flow's smoothstep or a custom edge looks acceptable, and otherwise draw the back edge as a
  decoration of the loop node.
- **Init graph layout.** Independent init steps make a fork/join. Use a layered layout, reusing
  `tooling/workflow/dag.py` (`layered_layout` or Graphviz).
- **MPI launcher.** Plain `mpirun` from the worker spawner (`--np`, `--mpirun`). The sync socket is
  bound to 127.0.0.1, so ranks must run on one node; multi-node needs rank 0's host published to
  the others.
