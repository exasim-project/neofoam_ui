# 07 — Coupled regions (multiphysics) in the stepper: state and next steps

Updated on 2026-11-08 for NeoFOAM's one graph and one thread per region. This note records where the coupled-region work stands, so it can be picked up later.

## Branches

| Repo | Branch | What it holds |
|---|---|---|
| neofoam_ui | `feat/dag-stepper` | The UI for coupled regions, commits `a7c3d26` … `379d132`. |
| NeoFOAM | `feat/multiphysics-minimal` (worktree `.claude/worktrees/feat+multiphysics-minimal`) | The `feat/region-coupling` chain on top of `feat/operation-domains`, plus the coupling-node refactor (`df2c75f6f`, `14bf00719`) and the probe/limitations cases in `test/solver/multiRun/limitations/`. |
| NeoFOAM | `feat/coupling-decorators` (same worktree, on top of `feat/multiphysics-minimal`) | `@coupled` sides, `exchanges:` in `regionCoupling.yaml`, a mapping node per side, one loop per connected group, the public read API. |
| NeoFOAM | `feat/coupling-per-region` (same worktree, tested at `4eeac1022`) | One graph and one thread per region; regions talk only through the transport; `Coupling.agree` instead of `sync_dt`. The UI follows it from `68c6f2f` on. |

To open a case, run `scripts/probe.sh <case>`, where `<case>` is pair, chain, …; for a worktree set `NEOFOAM_DIR`, `NEOFOAM_SRC` and `NEOFOAM_SITE` (a `sitecustomize.py` that points the venv's editable install at the worktree).

## Framework design as it stands (NeoFOAM, `feat/coupling-per-region`): one graph and one thread per region

- **multiRun is a launcher.** `run()` instantiates each region's solver (`region=`, `coupling=`) and runs it on its own thread (`framework/coupling/region.py: run_regions`); it returns `{region: Context}`. The threads inherit the caller's execution observer (contextvars). A region that fails closes the transport for all; `run_regions` raises the first error once every thread ended.
- **One graph per region:** `time_loop { set_time_step, agree_dt, increment_time, <r>.couple { solve_* } }` (probe). Op names carry no region, except `<r>.couple`. The observer's `operations(ops, ctx)` is called once per region, from its thread (`ctx.region`).
- **Regions talk only through the transport** (`RegionCoupling.transport`), with blocking receives: in the coupling op's test (connect, the leader's decision, fresh data) and in `Coupling.agree` (time step). A region paused in the UI stalls its partners at their next receive.
- **Read API the adapter uses:** `CouplingOp.region`, `.label`, `.sides`, `.exchange_of(side)` (`name`, `partner` = `(region, patch)`, `mapping.kind`), `.partner(side)` (label `region.patch`), `.is_leader`, `.controller` (leader only, after its first test: `.label`, `.parallel`, `.residuals`); the scheme from `Coupling.of(ctx).coupling.parallel`; `ExchangeSide.region`, `patch`, `receives`, `mapped`, `points`. Hooks: `coupling_exchanged(op, side, data)` (any region thread, in the test), `coupling_finished(controller, outcome)` (leader thread).
- **Gone:** `CouplingGroup`, `.leader`, `ExchangeSide.coupling_op`, `op.metadata.region`, the driver context with `ctx.models["regions"]`, `sync_dt` (now `Coupling.agree`).

## Stepping several regions (engine `_lanes.py`, adapter `_observed.py`)

- **Per-region state.** The adapter keeps everything it follows per region (`_RegionRun`: loop stack, open ops, taken nodes, the loop test, handed-over data, coupling passes) and books each hook under one lock, never held while paused. Each region's events carry their region (`Event.region`) and pause in that region's own gate.
- **One step = every region, together.** A command (Into, Over, Out, Next time step, Run, Run to) applies to every region, from the event it is held at; each region evaluates the command's stop condition on its own events (Into: its next event). A region *waiting* in a receive keeps its place and takes the command from the event it was released from. A breakpoint, a run-to target or Pause in one region stops all others at their next event (all-stop). **The step ends when no region runs**: each is paused, waiting for a partner, or done. A waiting region never holds the step up; it moves as soon as its data arrives (normally within the same step, else it is reported when it stops). Nothing waits on a held region, so stepping cannot deadlock; in a true solver deadlock every region simply shows waiting.
- **Waiting.** The adapter wraps the transport's `receive` / `send` (the object, once): a receive that blocks > 30 ms marks its region `waiting for <source>`, a matching send marks it running again before the sender can pause.
- **Errors.** A region thread that ends before its last top-level op ran failed: the other regions are unwound, `run()` raises the region's error, and the session ends with it.
- **Wire.** `paused.event.regions`: `[{region, state (paused | waiting | running | done | failed), event, waiting_for}]`; `event` itself is the region in focus (the one at a breakpoint, else the first paused). `log` and `running` messages carry `region`. Graph ids are `<region>/<path>` (`a/time_loop/a.couple/solve_x`); `Session.set_region_ops` merges the regions' graphs in the coupling group's order.

## Framework design before `feat/coupling-decorators` (superseded)

- **One coupling node per coupled op.** `couple_<field>` is inserted directly before the op it feeds and does the whole exchange for that side. There are no separate send or receive ops.
- **Exchanged data is declared as a pydantic model** per side (`CoupledSide(data=Model, send=..., receive=..., coordinates=...)`). `CouplingOp.attach` raises when the two sides' fields do not match.
- **One shared `CouplingOp` loop** wraps all sides of a coupling. It is the loop predicate and handles convergence, acceleration, max iterations, outcomes and the observer hook. The design note is `doc/explanation/region-coupling.rst`.

## UI as it stands

- **Two views.** The toolbar switches between Init and Execution. The only frames are the region boxes.
- **One box per region, its own graph** (no mirrored shared loops, no `<op>@@<region>` copies). The boxes share rows so the k-th coupling op of every box sits on one row and each coupling line is horizontal. Loops are compact one-line nodes; the leader's coupling op keeps its `leader` tag.
- **A token per box.** `cur_nodes` / `cur_tests` hold every region's next action / loop test; a waiting region's node is amber and dashed (`wait_nodes`) and its box header says `⏳ waiting for b` (`waiting`); a finished region lights its end node (`done_regions`). The call stack and the status line list every region (`a: solve_x · b: waiting for a`). A single-region solver keeps its one token.
- **Coupling lines** join the `<r>.couple` ops of partner regions, one per exchange (paired by region and exchange name), through the mapping chip (`≈ <kind>`; hover / tap: kind, points per side, per direction the fields mapped / as is, live values). Box gap 200 px. Lines are hot for every paused region's coupling op. The Init view draws the same chip between the boxes once the execution graph is known.
- **Fields per region.** Variables (`a.time`, `a.x`, …), field list and the per-pass plots (`fields · <region>`, one row per coupling test of that region) are grouped by region in region colours; `coupling · <leader>.couple` holds each time step's outcome.
- **Seen in the probe cases** (screenshots in `.probe_shots/option_c/`, untracked): pair, serial scheme: after the first coupling test a is at `solve_x` while b (leader, receives a's fresh data) waits for a; chain: a waits for c's decision, b solves, c waits for b.

## Decisions taken

- Exchanged data is matched strictly against shared models. The framework provides standard models.
- N couplings: one coupling op per region (`<r>.couple`), one exchange per coupled side; the group's last region leads.
- Stepping: every command moves all regions together (decided by the user); all-stop on a breakpoint or Pause; a waiting region never blocks a step.
- No backwards compatibility with the merged-graph design.
- 3D field view of several regions: deferred.

## Open, in rough order

1. **Framework hooks that would simplify the adapter:**
   - `waiting(region, source, tag)` / `received(...)` observer hooks around a transport receive (today the adapter wraps the transport object's `receive` / `send`);
   - `region_finished(region, ctx)` / `region_failed(region, error)` hooks (today: "its last top-level op ran", else the thread ended early);
   - multiRun announcing its regions (and their stepping order) before they initialize, so the gate need not wait 0.25 s for the last region to show up and the Init view shows every box from the start;
   - a public way to the partner side's points (today each end tells only its own).
2. **Limitations still open:** a typo in a convergence key; `no_coordinates` should fall back to identity mapping; `forgotten_graph` only fails at runtime; solver discovery is hard-wired in `registry.py`.
3. **Live field view while a multi-region run runs:** frames render at pauses only; one Catalyst channel per region is still to do.
4. **Option (c) over processes / ranks** (`07b-per-region-solvers-draft.md`): the per-region gate is the same idea; a transport between processes needs the waiting hook from 1.
