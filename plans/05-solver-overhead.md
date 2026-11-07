# What the UI costs the solver

Status: **implemented and measured on pitzDaily, serial and `mpirun -np 2`** (observed NeoFOAM,
Catalyst field view). The stepper is a UI: a run should cost what the solver costs. Follows the
review of b676131, which found the costs listed in §1.

## 1. Where the time went (b676131)

| cost | when | now |
|---|---|---|
| CRC32 of every field after every action, every rank | every event, also in Run | single steps only (into / over / out, run to within a time step); free runs show *not tracked while running* in the stats table |
| one hand-over solver thread → session per event (observed mode) | every event | a free run passes the events it would not stop at on the solver thread; only time-step loop tests, the target and breakpoints are handed over |
| one `go` per event to every MPI follower | every event | rank 0 sends the free-run parameters once per resume (`free`); every rank passes the same events by itself |
| 150 ms sleep + `running` message per event (default delay) | every event in Run | `--delay MS` / `NEOFOAM_UI_DELAY_MS`, default 0; with 0 a `running` message at most every 0.1 s |
| `log` message per event, blocking flushed write per message | every event | background writer; DAG pane closed: no per-event messages at all |
| one trame state push per message, console list rebuilt per line | every message | ≤ 15 pushes/s (at once for paused/frame/errors/sync), console and visited applied per push |
| live frame at every time step, every cell field published | every time step | every *n* steps and ≥ *s* apart (default 1 / 0.5 s); the viewed field only (all for a custom `NEOFOAM_UI_CATALYST_SCRIPT`) |
| ParaView pipeline read back after every frame | every frame | first frame, field/component change, every 5 s; else only the timings |
| PNGs never deleted | — | newest 20 per run; the run directory on restart, the temp dir on exit |
| `pptable` rows converted on every rank | every table row | rank 0 only (followers still compute: NeoFOAM's aggregators are collective) |

## 2. Modes the user picks

- **DAG pane** (toolbar toggle, like Field / Pipeline / Plots). Closed — or no visible browser
  tab — the worker is told nobody watches (`watch`): a run sends nothing per event, never
  sleeps, tracks nothing. What remains: the pause check per handed-over event, solver stdout,
  the open panes' own data (frames at their interval, table rows). Opening it again brings the
  token back from the next event (running) or time step.
- **Delay** (slider 0–1000 ms, labelled; `--delay`). Only with the DAG pane open. A delay makes
  every event come to the session again (that is the animation).
- **Wait for UI** (checkbox; `--wait-ui`, `NEOFOAM_UI_WAIT=1`). Off (default): the solver never
  waits; while the UI lags, the writer keeps per batch only the newest 40 `log`, 1 `running`,
  200 `stdout` and says how many it left out (console: *… not shown while running*). `frame`,
  `pptable`, `paused`, errors, `stopped` always go out. On: nothing is thinned, and the session
  blocks on a `sync` message at every time step (with the DAG open), after every frame, and
  with a delay at every event, until the UI has handled and pushed everything before it and
  answered `ack`. Every event and frame is shown; the solver runs at the UI's pace.
- **Live frames** (Field pane: *every n steps*, *≥ s apart*). At pauses a frame is always
  rendered (if the values changed).

Pause: serial, the next event after the click; under MPI the next time step (every rank must
pass the same events — they decide alone in a free run, so an asynchronous request is only
safe at a point where they all hand over). New breakpoints during a run take effect at the
next time step too.

## 3. Benchmark

`scripts/bench_overhead.sh [--np N] [--end-time 0.02] [--repeat 3] [--src DIR]` — prepares a
fresh pitzDaily (12 225 cells, the example tables of `examples/pitzDaily`), END_TIME 0.02
(≈ 106 adjustable time steps, ≈ 1 190 events), and times, best of 3:

- **plain**: NeoFOAM's `run()` (plus `ensure_neon_initialized`, which the tables need);
- the UI modes: the real `StepperApp` in-process (its worker, reader and state pushes; the trame
  server is not started, so no browser renders), Run from the first pause, delay 0:
  **dag-off** (every pane closed, DAG too), **ui** (DAG open, a viewer), **field** (+ Field pane
  live, U magnitude), **plots** (+ Plots pane), and each with *wait for UI* (**-wait**).

`run` = from `run()` / leaving the first pause to the end of the last time step: what the UI
costs the solver. `total` adds process start-up. 16-core workstation, at most 2 ranks.

### Serial

| mode | before run s | vs plain | after run s | vs plain |
|---|---:|---:|---:|---:|
| plain | 4.22 | — | 4.24 | — |
| dag-off | — | — | 4.31 | +1.7 % |
| ui (DAG open) | 4.49 | +6.4 % | 4.32 | +1.9 % |
| field | 8.59 | +104 % | 5.74 | +35 % |
| plots | 4.51 | +6.9 % | 4.32 | +1.9 % |
| ui-wait | — | — | 4.36 | +2.8 % |
| field-wait | — | — | 5.79 | +37 % |
| plots-wait | — | — | 4.34 | +2.4 % |

### `mpirun -np 2`

| mode | before run s | vs plain | after run s | vs plain |
|---|---:|---:|---:|---:|
| plain | 2.77 | — | 2.80 | — |
| dag-off | — | — | 2.82 | +0.7 % |
| ui (DAG open) | 2.97 | +7.2 % | 2.84 | +1.4 % |
| field | 7.65 | +176 % | 3.84 | +37 % |
| plots | 2.98 | +7.6 % | 2.85 | +1.8 % |
| ui-wait | — | — | 2.87 | +2.5 % |
| field-wait | — | — | 3.88 | +39 % |
| plots-wait | — | — | 2.89 | +3.2 % |

"Before" is b676131 with the slider at 0; its default (150 ms per event) would add ≈ 1 190 ×
0.15 s ≈ 180 s to this run. Run-to-run spread of the best-of-3 is about ±0.02 s.

Reading the numbers:

- **dag-off / ui / plots ≈ +1–2 %** (≈ 0.03–0.08 s). The session itself costs nothing
  measurable: `Session.advance("run")` in-process takes the same 4.14 s as `run()`. What is left
  is the worker process: forwarding ≈ 1 200 solver output lines and ≈ 1 150 table rows
  (≈ 0.03–0.05 s, through the writer and the fd 1 pump, which now wake at most 50×/s), and the
  pause → command → pause round trip of the measurement.
- **field ≈ +1.5 s**, of which ≈ 1.15 s is ParaView/Catalyst start-up at the first frame (the
  same in every run with a view); each further frame costs ≈ 28 ms, 11 frames here at the
  0.5 s default (before: 106 frames, every field published, pipeline read back each time).
- **wait for UI** costs +0.5–1.5 % here because this UI answers at once; with a browser that
  lags it runs at the browser's pace — that is the point of it.

## 4. Tests

`tests/test_overhead.py` (no checksums in a free run, far run-to, hand-overs per time step,
breakpoints/targets/pause in free runs, DAG closed = no events, delay animates every event,
frame interval, PNG retention, rank-0-only rows, thinning keeps critical messages, a slow
consumer does not block, wait-for-UI blocks until `ack` and drops nothing, two ranks
self-driven in step through free runs and a pause), `tests/test_ui_overhead.py` (options,
watch, capped pushes and acks), and opt-in with NeoFOAM: free runs give the same fields as
`run()` (tree and observed), and `mpirun -np 2` observed through free runs, a breakpoint,
next time step and single steps.

## 5. Open

- A hidden DAG pane (`v-show`) makes vue-flow log NaN sizes for its background/minimap in the
  browser console; harmless.
- "Who watches" counts visible tabs (`visibilitychange` / `pagehide`); a tab that crashes
  still counts, and the run then reports events as with the DAG open (thinned, not blocking).
- Under MPI, Pause / Stop / new breakpoints in a free run wait for the next time step; a very
  long time step delays them accordingly.
