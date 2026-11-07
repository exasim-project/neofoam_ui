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

import time
import traceback
from typing import Any, Callable, Iterator, Optional


from neofoam_ui.engine._graph import Event, InitStep, Op, assign_ids, topological_order
from neofoam_ui.engine._fieldview import _FieldView
from neofoam_ui.engine._protocols import Peers, RenderBackend, ResultObserver, SelfDrivingSolver, Solver
from neofoam_ui.engine._stepping import _free_run, _until, Mode
from neofoam_ui.engine._stats import _field_stats, _merge, _rows
from neofoam_ui.engine._threaded import ThreadedProgram
from neofoam_ui.engine._tracking import _Tracker
from neofoam_ui.engine._wire import _init_dict, _op_dict

__all__ = ()


#: Seconds between two ``running`` messages of a free run without delay (the DAG's token).
_RUNNING_EVERY = 0.1


class Session:
    """Drives one solver run event by event.

    ``emit(message)`` receives plain dicts (``graph``, ``log``, ``paused``,
    ``running``, ``error``) — the worker forwards them as JSON lines, tests
    collect them in a list.
    """

    def __init__(self, solver: Solver, emit: Callable[[dict[str, Any]], None],
                 renderer: Optional[RenderBackend] = None, follower: bool = False) -> None:
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
        #: Under MPI, rank 0's view of the other ranks (:meth:`set_peers`); ``None`` = alone.
        self.peers: Optional[Peers] = None
        self._go: Optional[Callable[[bool], None]] = None
        #: Another rank than 0: rank 0 drives it (``go``, ``free``); it reports nothing itself.
        self.follower = follower
        self._free: Optional[dict[str, Any]] = None
        self.pause_requested = False
        self.stop_requested = False
        self.ctx: Any = None
        self.ops: list[Op] = []
        self.results: dict[str, Any] = {}
        #: Timing, call counts and the fields each action wrote.
        self.tracker = _Tracker(solver.fields)
        self._observe = solver.observe if isinstance(solver, ResultObserver) else None
        #: The thread a self-driving solver runs on (set by its program), see :meth:`in_solver_thread`.
        self.program_thread: Optional[ThreadedProgram] = None
        #: Field view: also render at time steps while running (not only at pauses), at most
        #: every :attr:`frame_every` time steps and :attr:`frame_seconds` seconds apart.
        self.live_view = True
        self.frame_every = 1
        self.frame_seconds = 0.5
        #: Rendered frames kept on disk (older images of this session are deleted).
        self.keep_frames = 20
        self._view = _FieldView(self, renderer)
        if isinstance(solver, SelfDrivingSolver):  # the solver drives itself and fills self.init
            self.init: list[InitStep] = []
            self._gen = solver.program(self)
        else:
            self.init = topological_order(solver.init_steps())
            self._gen = self._program()
        self.event: Event = next(self._gen)
        self.finished = False
        self.failed = False

    def set_peers(self, peers: Peers) -> None:
        """Rank 0 of an MPI run: keep ``peers`` in step from the next resume on.

        Before every resume of the program they ``go`` too, a free run hands them its
        parameters, a frame makes them render, and the stats are merged over them."""
        self.peers = peers
        self._go = peers.go

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
        if self._observe is None:
            return
        try:
            self._observe(value, self.emit)
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
        if not (self.stepping or self.watch) or self.follower:  # a free run nobody watches
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
        action to see what it wrote; a free run (:func:`~neofoam_ui.engine._stepping._free_run`) leaves its writes
        :data:`UNTRACKED`.
        ``run_to`` is a single step while its target is near: it stops tracking at the
        second time step it starts.
        """
        if self.finished:
            return
        start = self.event
        until = _until(mode, start, target)
        self.pause_requested = False
        free = _free_run(mode, start)
        self._begin(not free or mode == "run_to" if track is None else track)
        self._arm(mode if free else None, target)
        steps = 0
        while True:
            try:
                if self._go is not None:  # the followers resume past the same event
                    self._go(self.stepping)
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
                    self._view.time_step()
                if self.watch and self.wait_ui and self.barrier is not None:
                    self.barrier()
                self._arm(mode if free else None, target)  # breakpoints / delay changed meanwhile
            if self.watch:
                self._running(ev)
        self._paused()

    def _arm(self, mode: Optional[Mode], target: Optional[str]) -> None:
        """A free run lets the solver thread pass the events it would not stop at (:meth:`set_free`).

        Not while animating (delay with the DAG pane open): then every event comes here.
        """
        if self.follower:  # rank 0 sets a follower's free run (``free``), not each ``go``
            return
        params = None
        if mode is not None and not (self.watch and self.delay > 0):
            params = {"mode": mode, "target": target, "bps": sorted(self.breakpoints)}
        if params != self._free:
            if self.peers is not None:
                self.peers.free(params)
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
        until = _until(params["mode"], self.event, params.get("target"))
        bps = frozenset(params["bps"])
        alone = self.peers is None and not self.follower  # no other ranks to keep in step

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
        others = self.peers.stats() if self.peers is not None and not self.failed else ()
        return {"scalars": self.solver.scalars(self.ctx), "fields": _rows(_merge(self.field_stats(), others)),
                "init_done": sorted(self.results)}

    # -- field view (in situ, Catalyst 2 by default) ------------------------------------------

    pipeline = property(lambda self: self._view.pipeline)
    view = property(lambda self: self._view.view)

    def set_view(self, field: Optional[str], component: str = "mag", on: bool = True) -> None:
        """Open (``on``) or close the field view; renders the current frame right away."""
        self._view.request(field, component, on)
        self._view.send()

    def request_view(self, field: Optional[str], component: str = "mag", on: bool = True) -> None:
        """Open or close the field view from any thread; the next frame (live or pause) shows it."""
        self._view.request(field, component, on)

    def send_view(self) -> None:
        """Render the viewed field in situ if the view is open and the values changed."""
        self._view.send()

    def render(self, params: dict[str, str], describe: bool = False) -> Optional[str]:
        """Hand this rank's cells and fields to the renderer (collective); the image file name."""
        return self._view.render(params, describe)

    def finalize_view(self) -> None:
        """Finalize the renderer (collective) on the solver thread, if a frame was rendered."""
        self._view.finalize()

    def _paused(self) -> None:
        if self.follower:  # nobody is listening
            return
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
