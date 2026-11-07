"""Step-by-step execution of a solver graph — the core of the DAG stepper.

The graph has the shape of a resolved NeoFOAM operation tree
(``neofoam.framework.operations``): *actions* (``SequentialOp``) and *loops*
(``IterativeOp``: a predicate plus child operations), preceded by the
initialization DAG of *init steps*. Nothing here imports neofoam or trame.

Execution is a generator that **pauses before** every event:

* ``init`` — before an init step runs,
* ``exec`` — before an action runs,
* ``test`` — before a loop predicate is evaluated,
* ``end``  — after the last loop exited.

:class:`Session` advances that generator according to the stepping commands
(into / over / out / next time step / run / run to), honouring breakpoints and
a pause request, and reports what happened through a callback.

Cost while running: the single steps (into / over / out) record which fields each
action changed (a checksum of every field after every action); the free runs (run,
run to, next time step) do not, and with the DAG pane closed (:attr:`Session.watch`)
they also send nothing per event — what remains is the pause check per event and
the open panes' own data (frames at their interval, post-processing rows).
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any, Callable, Iterator, Literal, Optional

import numpy as np

from neofoam_ui.engine._graph import Event, InitStep, Op, assign_ids, topological_order
from neofoam_ui.engine._protocols import Solver
from neofoam_ui.engine._stats import _field_stats, _merge, _rows
from neofoam_ui.engine._threaded import ThreadedProgram
from neofoam_ui.engine._tracking import _fingerprint, _Tracker
from neofoam_ui.engine._wire import _init_dict, _op_dict

__all__ = ()


#: Commands that advance the engine; ``None`` = until breakpoint / end / pause.
Mode = Literal["into", "over", "out", "time_step", "skip_init", "run", "run_to"]
#: Free runs: a self-driving solver passes the events they would not stop at on its own thread.
_FREE_MODES = ("run", "run_to", "time_step", "skip_init")
#: Seconds between two ``running`` messages of a free run without delay (the DAG's token).
_RUNNING_EVERY = 0.1
#: Seconds after which a live frame reads the ParaView pipeline back again (colour range etc.).
_DESCRIBE_EVERY = 5.0


class Session:
    """Drives one solver run event by event.

    ``emit(message)`` receives plain dicts (``graph``, ``log``, ``paused``,
    ``running``, ``error``) — the worker forwards them as JSON lines, tests
    collect them in a list.
    """

    def __init__(self, solver: Solver, emit: Callable[[dict[str, Any]], None]) -> None:
        self.solver = solver
        self.emit = emit
        self.breakpoints: set[str] = set()
        #: Seconds to wait between events while running (animation); 0 = no sleep.
        self.delay = 0.0
        #: The DAG pane is open (set by the UI): free runs report their events (``log``,
        #: ``running``) and animate; closed, they send nothing per event and never sleep.
        self.watch = True
        #: "Wait for UI": with :attr:`barrier` set, block at every time step (every event
        #: with a delay) and after every frame until the UI has shown what came before.
        self.wait_ui = False
        self.barrier: Optional[Callable[[], None]] = None
        #: The current advance is a single step (tracks writes, logs every event).
        self.stepping = True
        self._running_at = 0.0
        #: MPI rank 0: hands the free-run parameters (:meth:`set_free`) to the other ranks.
        self.on_resume: Optional[Callable[[Optional[dict[str, Any]]], None]] = None
        self._free: Optional[dict[str, Any]] = None
        self.pause_requested = False
        self.stop_requested = False
        self.ctx: Any = None
        self.ops: list[Op] = []
        self.results: dict[str, Any] = {}
        #: Timing, call counts and the fields each action wrote.
        self.tracker = _Tracker(solver.fields)
        #: The thread a self-driving solver runs on (set by its program), see :meth:`in_solver_thread`.
        self.program_thread: Optional[ThreadedProgram] = None
        #: Field stats of the other MPI ranks, merged into :meth:`summary` (set by the worker).
        self.gather: Optional[Callable[[], list[dict[str, dict[str, Any]]]]] = None
        #: Field view: ``(field, component)`` to render at every pause, ``None`` = view closed.
        #: Replaced as a whole (:meth:`request_view`), so another thread may set it while running.
        self.view: Optional[tuple[Optional[str], str]] = None
        #: Counts closings of the view: a reopened view renders again even if nothing changed.
        self._view_epoch = 0
        #: Rank 0 under MPI: makes the other ranks render the same frame (set by the worker).
        self.on_render: Optional[Callable[[dict[str, str]], None]] = None
        #: Also render at time steps while running (not only at pauses): at most every
        #: :attr:`frame_every` time steps and :attr:`frame_seconds` seconds apart.
        self.live_view = True
        self.frame_every = 1
        self.frame_seconds = 0.5
        self._steps_since_frame = 0
        self._frame_at = 0.0
        #: Rendered frames kept on disk (older PNGs of this session are deleted).
        self.keep_frames = 20
        self.insitu: Any = None  # neofoam_ui.catalyst.InSitu, started on the first frame
        self._insitu_error: Optional[str] = None
        self._geometry: Any = None  # None = not built yet, False = solver has no geometry
        self._view_sent: Optional[tuple[Any, ...]] = None
        #: The post-processing pipeline as Catalyst last ran it (:meth:`InSitu.describe`, rank 0).
        self.pipeline: Optional[dict[str, Any]] = None
        self._pipeline_sent: Optional[str] = None
        self._describe_failed = False  # reported once, then no more diagrams
        self._described: Optional[tuple[Any, float]] = None  # (params, when) of the last read-back
        program = getattr(solver, "program", None)
        if program is not None:  # the solver drives itself and fills self.init
            self.init: list[InitStep] = []
            self._gen = program(self)
        else:
            self.init = topological_order(solver.init_steps())
            self._gen = self._program()
        self.event: Event = next(self._gen)
        self.finished = False
        self.failed = False

    # -- the program ---------------------------------------------------------------

    def _program(self) -> Iterator[Event]:
        for step in self.init:
            yield Event("init", step.id)
            self.tracker.timed(step.id, lambda: self.results.__setitem__(step.name, step.func(self.results)))
            self.observe_result(self.results[step.name])
            self._log("init", step.id, step.name)
        self.set_context(self.solver.create_context(self.results))
        self.set_ops(self.solver.execution_graph(self.ctx))
        yield from self._walk(self.ops, 0, ())
        yield Event("end")

    # -- used by self-driving programs too ------------------------------------------

    # The tracker's books, read by the reports and the tests.
    calls = property(lambda self: self.tracker.calls)
    last_ms = property(lambda self: self.tracker.last_ms)
    total_ms = property(lambda self: self.tracker.total_ms)
    writes_seen = property(lambda self: self.tracker.writes_seen)

    def set_context(self, ctx: Any) -> None:
        self.ctx = ctx
        self.tracker.new_context(ctx, self.stepping)

    def set_ops(self, ops: list[Op]) -> None:
        self.ops = ops
        assign_ids(self.ops)
        self.emit({"type": "graph", "activity": "exec", "ops": [_op_dict(o) for o in self.ops]})

    def observe_result(self, value: Any) -> None:
        """Offer an init-step result to the solver's optional ``observe(value, emit)``."""
        hook = getattr(self.solver, "observe", None)
        if hook is None:
            return
        try:
            hook(value, self.emit)
        except Exception:  # noqa: BLE001 - an observer must not stop the run
            self.emit({"type": "stdout", "text": "observe: " + traceback.format_exc()})

    def in_solver_thread(self, fn: Callable[[], Any], timeout: Optional[float] = None) -> bool:
        """Run ``fn`` on the thread the solver runs on; False if that timed out."""
        if self.program_thread is None:  # tree mode: the solver runs on this thread
            fn()
            return True
        return self.program_thread.call(fn, timeout)

    def record(self, kind: str, node: str, name: str, ms: float, **extra: Any) -> None:
        """Book-keeping after an event ran: timing, call count, written fields, log line."""
        self.tracker.count(node, ms)
        if kind == "exec":
            self._track_writes(node, name)
        self._log(kind, node, name, **extra)

    def _walk(self, ops: list[Op], depth: int, stack: tuple[tuple[str, int], ...]) -> Iterator[Event]:
        for op in ops:
            if op.kind == "action":
                yield Event("exec", op.id, depth, stack=stack)
                self.tracker.timed(op.id, lambda: op.func(self.ctx))
                self._track_writes(op.id, op.name)
                self._log("exec", op.id, op.name, stack=stack)
                continue
            i = 0
            while True:
                i += 1
                yield Event("test", op.id, depth, iter=i, stack=stack)
                result = bool(self.tracker.timed(op.id, lambda: op.func(self.ctx)))
                self._log("test", op.id, op.name, stack=stack, iter=i, result=result)
                if not result:
                    break
                yield from self._walk(op.children, depth + 1, stack + ((op.id, i),))

    def _track_writes(self, node: str, op_name: str) -> None:
        if self.stepping:  # a free run: no checksums, see advance()
            self.tracker.track_writes(self.ctx, node, op_name)

    def _log(self, kind: str, node: str, name: str, **extra: Any) -> None:
        if not (self.stepping or self.watch):  # a free run nobody watches
            return
        stack = extra.pop("stack", ())
        self.emit(
            {
                "type": "log",
                "kind": kind,
                "node": node,
                "name": name,
                "ms": round(self.tracker.last_ms.get(node, 0.0), 3),
                "stack": [list(s) for s in stack],
                "time": self.solver.scalars(self.ctx).get("time") if self.ctx is not None else None,
                **extra,
            }
        )

    # -- stepping ------------------------------------------------------------------

    def start(self) -> None:
        """Announce the init graph and the first pause."""
        self.emit({"type": "graph", "activity": "init", "steps": [_init_dict(s) for s in self.init]})
        self._paused()

    def advance(self, mode: Mode, target: Optional[str] = None, track: Optional[bool] = None) -> None:
        """Execute the current event, then keep going until ``mode`` says stop.

        Only the single steps (into, over, out; or ``track``) checksum the fields after each
        action to see what it wrote; a free run (:meth:`_free_run`) leaves its writes
        :data:`UNTRACKED`.
        ``run_to`` is a single step while its target is near: it stops tracking at the
        second time step it starts.
        """
        if self.finished:
            return
        start = self.event
        until = self._until(mode, start, target)
        self.pause_requested = False
        free = self._free_run(mode, start)
        self._begin(not free or mode == "run_to" if track is None else track)
        self._arm(mode if free else None, target)
        steps = 0
        while True:
            try:
                self.event = next(self._gen)
            except Exception:  # the solver raised inside an op
                self.failed = self.finished = True
                self.emit({"type": "error", "node": start.node if start else None,
                           "traceback": traceback.format_exc()})
                self._paused()
                return
            ev = self.event
            if ev.kind == "end":
                self.finished = True
                break
            if until(ev) or ev.node in self.breakpoints or self.pause_requested or self.stop_requested:
                if ev.node in self.breakpoints and not until(ev):
                    self.emit({"type": "breakpoint", "node": ev.node})
                break
            if ev.kind == "test" and not ev.stack:  # new time step
                steps += 1
                if mode == "run_to" and steps == 2 and track is None:
                    self._begin(False)  # a far target: a free run from here on
                if self.live_view:
                    self._live_frame()
                if self.watch and self.wait_ui and self.barrier is not None:
                    self.barrier()
                self._arm(mode if free else None, target)  # breakpoints / delay changed meanwhile
            if self.watch:
                self._running(ev)
        self._paused()

    @staticmethod
    def _free_run(mode: Mode, start: Event) -> bool:
        """Run, run to, next time step, skip init — and stepping over the time loop (to the end)."""
        return mode in _FREE_MODES or (mode == "over" and start.kind == "test" and start.depth == 0)

    def _arm(self, mode: Optional[Mode], target: Optional[str]) -> None:
        """A free run lets the solver thread pass the events it would not stop at (:meth:`set_free`).

        Not while animating (delay with the DAG pane open): then every event comes here.
        """
        params = None
        if mode is not None and not (self.watch and self.delay > 0):
            params = {"mode": mode, "target": target, "bps": sorted(self.breakpoints)}
        if params != self._free:
            if self.on_resume is not None:
                self.on_resume(params)
            self.set_free(params)

    def set_free(self, params: Optional[dict[str, Any]]) -> None:
        """Pass the events of a free run on the solver thread, without a hand-over each.

        ``params`` (``mode``, ``target``, ``bps``; ``None`` = hand over every event) fix what
        is passed: every event but a time step's loop test, the run's target and the
        breakpoints given. Under MPI each rank gets the same ``params`` and so passes the
        same events; a Pause, a Stop and new breakpoints then take effect at the next time
        step. A serial run also stops right at the event after a Pause or Stop.
        """
        self._free = params
        thread = self.program_thread
        if thread is None:  # tree mode: the events come here cheaply anyway
            return
        if params is None:
            thread.skip = None
            return
        until = self._until(params["mode"], self.event, params.get("target"))
        bps = frozenset(params["bps"])
        alone = self.on_resume is None and self.gather is None  # no other ranks to keep in step

        def skip(ev: Event) -> bool:
            if (ev.kind == "test" and not ev.stack) or until(ev) or ev.node in bps:
                return False
            if alone and (self.pause_requested or self.stop_requested):
                return False
            if self.watch:
                self._running(ev)
            return True

        thread.skip = skip

    def _begin(self, stepping: bool) -> None:
        """Start tracking writes (fresh checksums after a free run), or stop for a free run."""
        self.stepping = stepping
        if stepping:
            self.tracker.start(self.ctx)
        else:
            self.tracker.stop()

    def _running(self, ev: Event) -> None:
        """The DAG's token while running: every event with a delay, else a few times a second."""
        if self.delay > 0:
            self.emit({"type": "running", "event": ev.to_dict()})
            if self.wait_ui and self.barrier is not None:
                self.barrier()
            time.sleep(self.delay)
            return
        now = time.monotonic()
        if now - self._running_at >= _RUNNING_EVERY:
            self._running_at = now
            self.emit({"type": "running", "event": ev.to_dict()})

    def _live_frame(self) -> None:
        """A frame at this time step if :attr:`frame_every` steps and :attr:`frame_seconds` passed."""
        self._steps_since_frame += 1
        if self.view is None or self._steps_since_frame < max(1, self.frame_every):
            return
        if time.monotonic() - self._frame_at < self.frame_seconds:
            return
        self._steps_since_frame = 0
        self.send_view()

    @staticmethod
    def _until(mode: Mode, start: Event, target: Optional[str]) -> Callable[[Event], bool]:
        d = start.depth
        if mode == "into":
            return lambda ev: True
        if mode == "over":
            if start.kind == "test":
                # Run the whole loop: stop at the first event after it.
                return lambda ev: ev.depth <= d and not (ev.kind == "test" and ev.node == start.node)
            return lambda ev: True
        if mode == "out":
            if start.kind == "init":  # out of the initialization activity
                return lambda ev: ev.kind != "init"
            if d == 0:  # out of the top-level activity = run to the end
                return lambda ev: False
            return lambda ev: ev.depth < d
        if mode == "time_step":
            return lambda ev: ev.kind == "test" and ev.depth == 0
        if mode == "skip_init":
            return lambda ev: ev.kind != "init"
        if mode == "run_to":
            return lambda ev: ev.node == target
        if mode == "run":
            return lambda ev: False
        raise ValueError(f"unknown step mode {mode!r}")

    # -- reporting -----------------------------------------------------------------

    def field_stats(self) -> dict[str, dict[str, Any]]:
        """Per field: min / max / sum / cell count and who last wrote it (mergeable across ranks)."""
        if self.ctx is None:
            return {}
        return _field_stats(self.solver.fields(self.ctx), self.tracker)

    def summary(self) -> dict[str, Any]:
        if self.ctx is None:
            return {"scalars": {}, "fields": [], "init_done": sorted(self.results)}
        # The other MPI ranks; not after a failure, they may be stuck in a collective.
        others = self.gather() if self.gather and not self.failed else ()
        return {"scalars": self.solver.scalars(self.ctx), "fields": _rows(_merge(self.field_stats(), others)),
                "init_done": sorted(self.results)}

    # -- field view (in situ, Catalyst 2) -------------------------------------------------

    def set_view(self, field: Optional[str], component: str = "mag", on: bool = True) -> None:
        """Open (``on``) or close the field view; renders the current frame right away."""
        self.request_view(field, component, on)
        self.send_view()

    def request_view(self, field: Optional[str], component: str = "mag", on: bool = True) -> None:
        """Open or close the field view from any thread; the next frame (live or pause) shows it."""
        if not on:
            self._view_epoch += 1  # before the swap: whoever sees the new view sees the new epoch
        self.view = (field, component) if on else None

    def _cell_fields(self) -> Optional[tuple[dict[str, Any], dict[str, np.ndarray]]]:
        """This rank's geometry (built once) and its cell-sized fields; ``None`` = no geometry."""
        if self._geometry is None:
            mesh = getattr(self.solver, "mesh", None)
            geom = None
            try:
                geom = mesh(self.ctx) if mesh is not None else None
            except Exception:  # noqa: BLE001 - no view rather than a dead session
                self.emit({"type": "stdout", "text": "field view: " + traceback.format_exc()})
            self._geometry = geom or False
        if not self._geometry:
            return None
        from neofoam_ui.geometry import cell_count  # noqa: PLC0415

        n = cell_count(self._geometry)
        source = getattr(self.solver, "view_fields", None) or self.solver.fields
        fields = {k: np.asarray(v) for k, v in source(self.ctx).items()}
        return self._geometry, {k: v for k, v in fields.items() if v.ndim in (1, 2) and len(v) == n}

    def _published(self, fields: dict[str, np.ndarray], params: dict[str, str]) -> dict[str, np.ndarray]:
        """What goes to Catalyst: the viewed field for the default pipeline, all for another script."""
        from neofoam_ui import catalyst  # noqa: PLC0415

        if catalyst.custom_script() or params.get("field") not in fields:
            return fields
        return {params["field"]: fields[params["field"]]}

    def _view_error(self, text: str) -> None:
        if self._view_sent != ("error", self._view_epoch, text):
            self._view_sent = ("error", self._view_epoch, text)
            self.emit({"type": "frame", "file": None, "fields": [], "error": text})

    def send_view(self) -> None:
        """Render the viewed field in situ if the view is open and the values changed."""
        view = self.view  # read once: the command reader may replace it meanwhile
        if view is None or self.ctx is None:
            return
        from neofoam_ui import catalyst  # noqa: PLC0415

        if self._insitu_error:
            return self._view_error(self._insitu_error)
        if not catalyst.configured():
            return self._view_error(f"in-situ view off: set {catalyst.ENV} to a Catalyst + ParaView install")
        cells = self._cell_fields()
        if cells is None:
            return self._view_error(f"{self.solver.name} provides no mesh geometry")
        fields = cells[1]
        name, comp = view
        if name not in fields:
            name = next((k for k in ("U", "p", "T") if k in fields), next(iter(fields), None))
        if name is None:
            return
        arr = fields[name]
        ncomp = int(arr.shape[1]) if arr.ndim == 2 else 1
        if ncomp == 1 or comp not in ["mag"] + [str(i) for i in range(ncomp)]:
            comp = "mag"
        key = (self._view_epoch, name, comp, _fingerprint(arr))
        if key == self._view_sent:
            return
        self._view_sent = key
        self._frame_at = time.monotonic()
        params = {"field": name, "component": comp}
        if self.on_render is not None:
            self.on_render(params)
        file = self.render(params, describe=True)
        if file is None:
            return self._view_error(self._insitu_error or "in-situ view failed")
        frame = {
            "type": "frame", "file": file, "field": name, "component": comp,
            "view": list(view),  # the request this frame answers (field/component may be a fallback)
            "time": self.solver.scalars(self.ctx).get("time"),
            "fields": [{"name": k, "ncomp": int(v.shape[1]) if v.ndim == 2 else 1}
                       for k, v in fields.items()],
        }
        if self.pipeline is not None:
            # The graph and its settings only when they changed; the timings with every frame.
            graph = {k: v for k, v in self.pipeline.items() if k != "ms"}
            key = json.dumps(graph, sort_keys=True)
            if key != self._pipeline_sent:
                self._pipeline_sent = key
                frame["pipeline"] = graph
            frame["pipeline_ms"] = self.pipeline.get("ms", {})
        self.emit(frame)
        prune = getattr(self.insitu, "prune", None)
        if prune is not None:
            prune(self.keep_frames)
        if self.wait_ui and self.barrier is not None:
            self.barrier()

    def render(self, params: dict[str, str], describe: bool = False) -> Optional[str]:
        """Hand this rank's cells and fields to Catalyst (collective); the image file name.

        ``describe``: afterwards also read back the pipeline (:attr:`pipeline`); one rank does.
        """
        out: list[Optional[str]] = [None]

        def run() -> None:
            cells = self._cell_fields() if self.ctx is not None else None
            if cells is None:
                return
            from neofoam_ui.catalyst import InSitu  # noqa: PLC0415

            if self.insitu is None:
                self.insitu = InSitu()
            time_value = self.solver.scalars(self.ctx).get("time") or 0.0
            out[0] = self.insitu.execute(cells[0], self._published(cells[1], params), time_value, params)
            if describe and not self._describe_failed and hasattr(self.insitu, "describe"):
                try:
                    self._describe(params)
                except Exception:  # noqa: BLE001 - a frame without its diagram is still a frame
                    self.pipeline, self._describe_failed = None, True
                    self.emit({"type": "stdout", "text": "pipeline diagram: " + traceback.format_exc()})

        def guarded() -> None:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - the stepper outlives a broken view
                self._insitu_error = f"in-situ view failed: {exc!r}"
                self.emit({"type": "stdout", "text": "Catalyst: " + traceback.format_exc()})

        if self._insitu_error is None:
            self.in_solver_thread(guarded)
        return out[0]

    def _describe(self, params: dict[str, str]) -> None:
        """Read the pipeline back when it may have changed: the first frame, another field or
        component, or :data:`_DESCRIBE_EVERY` seconds on (colour range); else only the timings."""
        now = time.monotonic()
        last = self._described
        if self.pipeline is None or last is None or last[0] != params or now - last[1] >= _DESCRIBE_EVERY:
            self.pipeline = self.insitu.describe()
            self._described = (dict(params), now)
            return
        ms = getattr(self.insitu, "ms", None)
        if isinstance(ms, dict):
            self.pipeline = {**self.pipeline, "ms": {k: round(v, 3) for k, v in ms.items()}}

    def finalize_view(self) -> None:
        """``catalyst_finalize`` (collective) on the solver thread, if a frame was rendered."""
        if self.insitu is not None:
            insitu, self.insitu = self.insitu, None
            self.in_solver_thread(insitu.finalize)

    def _paused(self) -> None:
        self.send_view()
        t = self.tracker
        self.emit(
            {
                "type": "paused",
                "event": self.event.to_dict(),
                "finished": self.finished,
                "failed": self.failed,
                "summary": self.summary(),
                "calls": t.calls,
                "last_ms": {k: round(v, 3) for k, v in t.last_ms.items()},
                "total_ms": {k: round(v, 3) for k, v in t.total_ms.items()},
                "writes_seen": {k: sorted(v) for k, v in t.writes_seen.items()},
            }
        )
