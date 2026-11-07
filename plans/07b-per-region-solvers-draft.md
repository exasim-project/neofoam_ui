# Per-region solvers coupled only through coupling nodes (option c)

Worktree: `feat+multiphysics-minimal` @ 14bf00719. Paths are relative to it. This is analysis only. No code was changed.

## 1. Why there is one shared loop today (short)

- **One process, one call stack.** multiRun puts every region's time-step body into **its own** time loop (`src/neofoam/solver/multiRun/multiRun.py:110-115`), and `region_operations` renames those ops to `<region>.<op>` (`src/neofoam/framework/solver/regions.py:46-65`). Execution is plain recursion: `Operation._execute` runs `while self._loop_test(...)` over its sub-ops (`src/neofoam/framework/operations.py:143-150`). A loop runs to completion before its next sibling starts. So two region loops in one thread can never interleave pass by pass. Only one loop around both sides can.
- **One decision.** The `CouplingOp` predicate reads every side's `send()`, computes the residuals, the convergence check, `max_iterations` and acceleration, and records the outcome (`src/neofoam/framework/coupling/loop.py:309-354`). Both sides repeat or advance together because only one predicate exists.
- **Gauss-Seidel for free.** `_exchange` reads the partner's data *now* if the partner was stepped earlier in this pass (`loop.py:377-388`). It needs no messages because both sides live in one address space.
- **The same time for free.** Every region shares the driver's `Time` (`multiRun.py:60`), and only the driver runs `set_time_step`/`increment_time` (`multiRun.py:111-112`). Side effect: a region's own deltaT constraints (Courant, for example) are not consulted under multiRun.
- **Grouping.** Wraps that share one `with_` join one loop (`src/neofoam/framework/graph/edits.py:126-135`, `:159-162`). The design note asks for one loop per coupled group (`doc/explanation/region-coupling.rst:153-158`). Per-interface loops nest instead: with the hand-built chain, `a` runs 122 solves where the pair needs 20 (I re-ran `limitations/run.py` on pair, chain and two_interfaces to get these numbers).
- **The older branch.** `feat/buoyant-cht`'s `CouplingLoop` is also one driver loop over the split time steps of both regions (`git show feat/buoyant-cht:src/neofoam/framework/coupling/loop.py`, docstring at lines 4-27).

**Finding that carries over to (c).** The coupled iteration is mathematically *one* fixed-point loop on the joint interface vector. In (c) each region has its own loop node, but those nodes must reach the *same* verdict in the *same* pass. Per-region loops are synchronised replicas of one collective decision, not independent loops.

**The per-region graphs already exist.** I resolved each probe region's `time_step` separately, with the shared `CouplingOp` (scratchpad `perregion.py`). Each region gets its own `loop_solving_x [IterativeOp] -> couple_x, solve_x`, and both loop nodes point at one `CouplingOp` that holds both sides. So (c) is mostly about how those per-region loop nodes synchronise. The graph edits stay.

## 2. Options (short)

| | per-region loop nodes in the graph | regions run concurrently | change to the framework |
|---|---|---|---|
| (a) projection only | UI only | no | none |
| (b) per-region IterativeOps, one driver | yes, but the driver must split them into test and body and run them in lockstep, i.e. (a) in disguise | no | medium |
| **(c) full solver per region** | yes, each region runs its own `execution_graph` | yes | large; the user chose this |

## 3. Proposal for (c)

### 3.1 Execution model

- **Target: one OS process group per region (MPMD).** OpenFOAM v2406 supports this natively. `argList` has a `-world` option (`$WM_PROJECT_DIR/src/OpenFOAM/global/argList/argList.C:184`), and `UPstream` provides `myWorld`/`allWorlds` (`src/OpenFOAM/db/IOstreams/Pstreams/UPstream.H`). One launch is `mpirun -np 4 python -m neofoam.solver.region -world a : -np 2 ... -world b`. Each world is a normal decomposed OpenFOAM run on its own sub-communicator. Coupling traffic goes over the world-spanning communicator. pybFoam still has to be checked for exposing `-world` and the inter-world communicator.
- **First: an in-process mode with one thread per region and a baton.** Coroutines would require rewriting `Operation.run` as generators, because the call stack *is* the program counter (`operations.py:111-150`). Threads keep the executor unchanged. A **baton** (exactly one region thread runs at a time; the baton is handed over only at a blocking exchange or verdict) makes the run deterministic and serial. It also sidesteps OpenFOAM's lack of thread safety and gives the UI one global token (see 3.6).
- **One API, two transports.** The solver and graph code only see a per-region `CouplingEndpoint`. Its transport is either `InProcess` (queues + baton) or `Mpi` (p2p over the inter-world communicator). multiRun picks the transport. Tests and the UI stepper use `InProcess`; production runs use `Mpi`.

### 3.2 The coupling node's contract

There are two framework nodes per side. Neither is written by the solver author.

```
loop_solving_x   IterativeOp(predicate = endpoint.verdict)    # per region
    couple_x     endpoint.receive()   blocks until the partner's data for this pass arrived
    solve_x
```

- **Where the send happens.** "Send, then block until received" inside `couple_x` works for *parallel* (Jacobi), but not for *serial*. In serial, the partner needs this side's data **after** `solve_x`, and the coupling node runs *before* the solve (`edits.py:217-232`). So the send point is the end of the pass, which is the loop predicate:
  - `verdict()` = `send(own data)`, then wait for the collective decision.
  - `receive()` = block until the partner's data for this pass arrives.
- **Serial (a before b), per pass k:**
  - `a.couple` gets b's data from pass k-1 (relaxed).
  - `a.solve`.
  - `a.verdict` sends a's fresh data.
  - `b.couple` blocks until it has that data.
  - `b.solve`.
  - `b.verdict` sends b's data, and the decision is computed.
  - Both get the verdict. This is preCICE's serial-implicit. While b solves, a's ranks sit idle, which works against the resource goal, so parallel should be the default for MPMD.
- **Parallel:** both `couple` nodes take the carried (relaxed) data of pass k-1 and never wait inside the pass. The only synchronisation is the verdict, one barrier per pass.
- **Who decides.** A **coordinator** holds today's `CouplingOp` state for each coupled group: carried data, `converged`, `acceleration`, `max_iterations`, `outcomes`. That reuses `_next_pass`/`_start_pass`/`_finish` (`loop.py:309-354`) almost verbatim, with `_data(i)` filled from messages instead of `send()` calls.
  - The coordinator is an object in the multiRun thread (InProcess), or rank 0 of one designated region or a tiny multiRun rank (MPI).
  - Every region's predicate receives the same `(repeat | advance)` bit, plus the relaxed data it needs for its next `couple`.
  - A chain `a <-> b <-> c` is one group with one coordinator. b's single loop holds two `couple` nodes, one per interface. That fixes the nesting seen in `limitations/chain`.
- **Time-step agreement: a collective at the time-loop level, reusing existing hooks.** `set_time_step` already folds the injected `timeStepConstraint` (min) and `loopCondition` (`src/neofoam/algorithms/solution_loop/solution_loop.py:373-380`). Coupling adds a final reduction: `deltaT = min over regions`, `keep_running = all over regions`.
  - Without the `all`, one region ends (endTime reached or steady converged, `control.py:513-515`) while the other blocks forever at `couple`. That is a deadlock.
  - Startup handshake: compare endTime/startTime, the exchange models (today's `_check_match`, `loop.py:399-419`) and the group layout.
  - No subcycling or waveforms in v1: one coupling window equals one common time step.
- **Checkpoint/rollback.** A repeated pass must restart from the state at the beginning of the time step. This problem **exists today too**. The current solution is a solver convention: an op outside the loop stores the old time (`store_old_time`, `test/solver/multiRun/slab_solver.py:214-223`), and the Selector keeps it outside the loop because it neither solves `T` nor runs downstream of the solve (`edits.py:49-67`). (c) keeps that convention. Option 3 in section 4 makes it explicit.

### 3.3 Solver author API: a solver still runs alone, unchanged

- A region runs **its own `execution_graph`**: time loop, `set_time_step`, its writers and its own outer loops, like `slab_solver.py:199-211`. Under (c), **`@spec.time_step` is no longer needed**; today it exists only so a driver can lift the body out (`src/neofoam/framework/solver/spec.py:211-220`). The production solvers on this branch don't register it anyway; only the test solvers (slab, affine, probe) do.
- The coupled BC stays exactly as it is: `ctx.graph.wrap(ops=solving("x"), with_=CouplingOp.shared(ctx, key), side=CoupledSide(...))`. `runtime.resolve_operations` applies the edits as it does for a standalone run (`src/neofoam/framework/solver/runtime.py:118`).
- **Yes, the coupling loop is an `IterativeOp` in each region's graph, and its predicate is a collective decision.** Without multiRun (no endpoint injected), a coupled BC has no partner. That is an error at build time, as today (`forgotten_graph`), unless a `Standalone` endpoint is configured for tests.

```
time_loop                      IterativeOp(SolutionLoopPredicate)   # own, per region
    set_time_step              + coupling constraint: min/all over the group
    increment_time
    store_old_time
    loop_solving_x             IterativeOp(endpoint.verdict)        # collective
        couple_x               endpoint.receive(); side.receive(model)
        solve_x
    write_output
```

### 3.4 Config and launch

```yaml
# system/regionCoupling.yaml
regions:                      # new: resources per region
  a: {solver: probe, ranks: 4}
  b: {solver: probe, ranks: 2}
mode: processes               # processes | inProcess   (inProcess ignores ranks)
interfaces:                   # as in region-coupling.rst:127-140
  - patches: {a: to_b, b: to_a}
    fields: [x]
scheme: parallel
acceleration: {type: aitken, relaxation: 0.5}
convergence: [{type: relative, data: x, limit: 1.0e-6}]
maxIterations: 100
```

- Today `regionSolvers` lives in `controlDict.yaml` (`src/neofoam/solver/multiRun/configs.py:15-30`). It moves next to `ranks`, or stays there with `ranks` added (open question 5).
- `python -m neofoam.solver.multiRun`:
  - `inProcess`: starts one thread per region, plus the coordinator in the main thread.
  - `processes`: either execs `mpirun -np 4 … -world a : -np 2 … -world b`, or (better for clusters) prints that command / is itself started in MPMD form and detects its world.
  - Each region process reads only its own `constant/<region>`, `0/<region>` and `system/<region>`, as today.

### 3.5 What is reused, replaced or removed

| piece | fate |
|---|---|
| `CoupledSide` + pydantic exchange models, `Array`, `_kinds`, `_check_match` | **reused**, unchanged. The model also defines the wire format (named float arrays plus plain values) |
| `MappedTransfer`, `identity_transfer` | **reused**. In v1 mapping runs at the receiver; the target points are exchanged once at startup |
| `CouplingOp` | **split**. The coordinator keeps the predicate state, `outcomes`, `residuals` and `acceleration`. A per-region `CouplingEndpoint` (the `IterativeOp` in the region graph) keeps `verdict()`/`receive()`. `CouplingOp.shared` becomes the call that returns the region's endpoint |
| `CouplingNode` (`loop.py:193-205`) | **reused**, calling `endpoint.receive` instead of `coupling._exchange` |
| `CouplingOutcome`, `CouplingNotConvergedWarning` | **reused**. Raised by the coordinator and mirrored to every region's endpoint (per-region `outcomes` stay readable in tests) |
| graph edits (`wrap`, Selector, `_wrapped`, `_with_sides`) | **reused**, now applied per region only |
| `include_region` (`edits.py:126-135`) | **removed** from the (c) path; regions no longer join one graph |
| `region_operations`, `region_step`, `_on_region` (`regions.py`) | **removed** from the (c) path; keep them only while (a)-style tests still need them |
| `Operation.context` binding | no longer needed for regions; each thread or process owns its `ctx` |
| multiRun `execution_graph` (`multiRun.py:100-116`) | **replaced** by a launcher plus coordinator; no driver time loop |
| execution observer | **reused** per region. The observer is a per-thread ContextVar that threads do **not** inherit (`src/neofoam/framework/execution_observer.py:14-17`), so the launcher installs one per region thread. New events: `exchange_waiting/received`, `verdict` |

### 3.6 UI / debugging

- **One observer stream per region**: the region's own graph, tagged with its region. A coordinator stream emits `coupling_finished` once per group per step. In MPI mode, rank 0 of each world forwards events (e.g. over a socket); the UI subscribes to N streams.
- **Stepping with N tokens.** Every region can be paused independently. Pausing a stalls the others at their next `couple`/`verdict`, which is correct and will appear in the UI as "waiting for a". The UI needs a third node state, **blocked on exchange**, next to running and paused.
- **Deterministic stepping (InProcess + baton).** There is one global token: "step" runs the region holding the baton until its next op, and the baton moves at blocking points, so the order matches today's serial order. This should be the stepper's default. Per-region "run to exchange" is a second mode.

### 3.7 Plan (each step tested with the probe solver)

1. **Coordinator extracted, still one graph.** Refactor `CouplingOp` into coordinator + endpoint with an in-memory transport, under today's shared-loop multiRun. Pair, chain and two_interfaces results must stay the same (`test_limitations.py`).
2. **Pair, InProcess, two threads with a baton.** Each region runs its own `execution_graph`. The probe gets one (slab-style). Expect (2, 1) in about 20 passes for serial and parallel, and an outcome sequence equal to step 1.
3. **Time agreement.** Add the collective `min`/`all` in `set_time_step`. Tests: different `deltaT` per region gives the min; different `endTime` is rejected at the handshake; one region steady-converged keeps running without a deadlock.
4. **Chain and two_interfaces as one group.** One coordinator per connected component, several `couple` nodes per region. Expect chain = (3, 2, 2) with solve counts far below 122 (this flips the `xfail` tests).
5. **Observer per region + UI.** Two streams, a blocked state and baton stepping (`neofoam_ui scripts/probe.sh pair`).
6. **Two processes, no OpenFOAM.** Use the `Mpi` transport via mpi4py, `mpirun -np 1 … : -np 1 …`, with the probe and the same assertions as step 2. Then use different rank counts per region with a probe that holds a distributed array.
7. **OpenFOAM worlds.** Check that pybFoam honours `-world`, then run the slab (or a CHT case) MPMD.
8. **Checkpoint API** (if open question 3 picks the framework option), then serial-vs-parallel performance on real ranks.

### 3.8 Risks

- Deadlocks from mismatched collectives: differing loop counts, an exception in one region, a region that ends early. Mitigations: the handshake, `all` on `keep_running`, and abort-on-error broadcast from the coordinator.
- OpenFOAM in threads: two `Time`s in one process are fine, but not concurrent execution, which is why the baton is required.
- pybFoam `-world` support has not been verified.
- Serial schemes waste ranks.
- Debugging across processes.

## 4. Open API questions

1. **Where does the decision live?**
   - (A) A coordinator (multiRun thread or rank) runs today's `CouplingOp` logic. This is the recommended option.
   - (B) Leader side as in preCICE: the second side of each group decides and sends the bit.
   - (C) Symmetric: both sides hold both vectors and compute the same verdict, which relies on bit-identical floating-point results.
   ```python
   endpoint.verdict()   # A: send -> coordinator ; B: send -> leader ; C: allgather + local check
   ```
2. **What does the solver author write for the coupling loop?**
   - (A) Nothing: the coupled BC's `wrap` creates the endpoint loop, as today. Recommended.
   - (B) An explicit marker in the graph.
   ```python
   # B
   with time_builder.loop(ctx.coupling.loop("x")) as c:   # a no-op loop when standalone
       c.step(self.operations["solve_x"])
   ```
3. **Checkpoint/rollback.**
   - (A) Convention, as today's `store_old_time`.
   - (B) Hooks, as in preCICE.
   - (C) The framework snapshots declared fields.
   ```python
   @slab.checkpoint            # B
   def save(self, ctx): return {"T": ctx.fields["T"].copy()}
   @slab.restore
   def load(self, ctx, state): ctx.fields["T"][:] = state["T"]

   ctx.graph.wrap(..., side=CoupledSide(..., checkpoint=["T"]))   # C
   ```
4. **Time-step agreement.**
   - (A) Common fixed `deltaT`, checked at the handshake.
   - (B) Collective min each step via `set_time_step`. Recommended.
   - (C) The coordinator sets the window and regions subcycle (later).
   ```yaml
   timeWindow: {deltaT: min}      # B   | {deltaT: 1.0e-3}  # A | {deltaT: 1.0e-2, subcycle: true}  # C
   ```
5. **Where do regions and resources live?**
   - (A) `regionCoupling.yaml: regions: {a: {solver, ranks}}`, which retires `regionSolvers` in controlDict.
   - (B) `controlDict.yaml: regionSolvers: {a: {solver: probe, ranks: 4}}`.
   - (C) Only the `mpirun` command line, with yaml naming solvers only.
6. **One API for both modes?**
   - (A) The transport is chosen by `mode:` in yaml.
   - (B) The transport is chosen by how multiRun is launched (`mpirun` with `-world` means processes, otherwise inProcess).
   ```yaml
   mode: inProcess   # A
   ```
   ```sh
   mpirun -np 4 python -m neofoam.solver.multiRun -world a : -np 2 ... -world b   # B
   ```
7. **Coupled BC without multiRun.**
   - (A) Error, as today.
   - (B) A `Standalone` endpoint with fixed partner data from a file, so one region can be tested alone.

**Recommendation.** Do 1A, 2A, 3A (with 3B later), 4B, 6A, and the plan steps 1 to 4 in-process before any MPI. Step 1 changes no behaviour and is the smallest testable cut.
