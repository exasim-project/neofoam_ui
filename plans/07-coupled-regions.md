# 07 — Coupled regions (multiphysics) in the stepper: state and next steps

Paused on 2026-11-07. This note records where the coupled-region work stands, so it can be picked up later.

## Branches

| Repo | Branch | What it holds |
|---|---|---|
| neofoam_ui | `feat/dag-stepper` | The UI for coupled regions, commits `a7c3d26` … `379d132`. |
| NeoFOAM | `feat/multiphysics-minimal` (worktree `.claude/worktrees/feat+multiphysics-minimal`) | The `feat/region-coupling` chain on top of `feat/operation-domains`, plus the coupling-node refactor (`df2c75f6f`, `14bf00719`) and the probe/limitations cases in `test/solver/multiRun/limitations/`. |

To open a case, run `scripts/probe.sh <case>`, where `<case>` is pair, chain, …. For the nested chain, set `PROBE_COUPLINGS=hand`.

## Framework design as it stands (NeoFOAM)

- **One coupling node per coupled op.** `couple_<field>` is inserted directly before the op it feeds and does the whole exchange for that side. There are no separate send or receive ops.
- **Exchanged data is declared as a pydantic model** per side (`CoupledSide(data=Model, send=..., receive=..., coordinates=...)`). `CouplingOp.attach` raises when the two sides' fields do not match.
- **One shared `CouplingOp` loop** wraps all sides of a coupling. It is the loop predicate and handles convergence, acceleration, max iterations, outcomes and the observer hook. The design note is `doc/explanation/region-coupling.rst`.

## UI as it stands

- **Two views.** The toolbar switches between Init and Execution. The only frames left are the region boxes.
- **Each region box is a full solver,** as that solver would run alone: time loop → driver ops → coupling loop(s) → coupling node → solve. Shared loops and driver ops appear in every box as mirrored copies; a copy's id is `<op>@@<region>` and its `data.ref` points to the shared op. Loops are drawn as compact one-line nodes.
- **Boxes are joined only by coupling lines,** one per exchange, drawn between the two coupling nodes. A line shows the sent values and lights up while its exchange runs. Boxes are ordered along the coupling chain and share rows, so each line is horizontal.
- **Chain (nested, built by hand):** each box shows every loop its region actually runs in, so a's box also shows the b↔c loop.
- **Readability.** The minimap is off by default; "follow" fits the box that holds the token; a box collapses to its coupling nodes.
- **Fields per region.** The variables table, the field list and the per-pass plots (`fields · <region>`) are grouped by region and drawn in region colours.

## Decisions taken

- Exchanged data is matched strictly against shared models. The framework provides standard models.
- N couplings: one `CouplingOp` per connected group, with one coupling node per op that does all of that op's exchanges.
- Line values: accept the current UI-only values for now (see open item 2).
- 3D field view of several regions: deferred.

## Open, in rough order

1. **Framework: one loop per connected group,** plus `interfaces:` as a list in `regionCoupling.yaml`. This removes the nested chain loops (122 solves of a) and lets the box layout settle.
2. **Framework hooks for the UI:**
   - multiRun announces its regions before initializing them, so the Init view can show every box from the first run;
   - `coupling_exchanged(node, data)`, so the line shows exactly the data handed over, after relaxation and mapping.
3. **Limitations still open:**
   - a typo in a convergence key passes silently and the loop counts as converged; it should raise;
   - `no_coordinates` fails with an IndexError; it should fall back to identity mapping;
   - `forgotten_graph` only fails at runtime;
   - solver discovery is hard-wired in `registry.py`.
4. **Option (c), a full solver per region running in its own process or ranks** (for finer resource assignment): a parked design, `07b-per-region-solvers-draft.md`. It is not implemented. Today the UI only projects the shared loop into each box.
5. **3D field view for several regions:** one Catalyst channel per region.
