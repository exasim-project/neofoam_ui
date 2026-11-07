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
"""

from __future__ import annotations

import time
import traceback
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal, Optional, Protocol

import numpy as np

__all__ = [
    "Event",
    "InitStep",
    "Op",
    "Session",
    "Solver",
    "ThreadedProgram",
    "assign_ids",
    "topological_order",
]


@dataclass
class Op:
    """One node of the execution graph: an action, or a loop with children."""

    name: str
    kind: Literal["action", "loop"]
    #: Action body ``f(ctx)``, or the loop predicate ``f(ctx) -> bool``.
    func: Callable[[Any], Any]
    children: list[Op] = field(default_factory=list)
    domain: Optional[str] = None
    number: Optional[str] = None
    #: Loop predicate as shown in the diagram, e.g. ``"time < endTime"``.
    test: Optional[str] = None
    #: Guard shown on the action, e.g. ``"finalIter()"``.
    guard: Optional[str] = None
    #: Name of the optional model that injected this op through the resolver.
    injected_by: Optional[str] = None
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    doc: str = ""
    #: ``file:line`` shown in the inspector; derived from ``func`` when empty.
    source: Optional[str] = None
    id: str = ""
    #: Position in the tree, ``"1.4.2"`` (set by :func:`assign_ids`).
    pos: str = ""


@dataclass
class InitStep:
    """One node of the initialization DAG (mirrors neofoam's ``InitStep``)."""

    name: str
    func: Callable[[dict[str, Any]], Any]
    depends_on: list[str] = field(default_factory=list)
    category: Optional[str] = None
    doc: str = ""
    source: Optional[str] = None

    @property
    def id(self) -> str:
        return f"init:{self.name}"


class Solver(Protocol):
    """What the stepper needs from a solver.

    A solver may instead provide ``program(session) -> Iterator[Event]`` and drive
    itself (see :class:`ThreadedProgram`); then only ``scalars`` and ``fields`` are
    used, and ``init_steps`` / ``create_context`` / ``execution_graph`` are not.
    """

    name: str

    def init_steps(self) -> list[InitStep]: ...

    def create_context(self, results: dict[str, Any]) -> Any:
        """Build the run context from the init-step results (keyed by step name)."""

    def execution_graph(self, ctx: Any) -> list[Op]: ...

    def scalars(self, ctx: Any) -> dict[str, Any]: ...

    def fields(self, ctx: Any) -> dict[str, np.ndarray]: ...

    # Optional: ``mesh(ctx) -> geometry dict`` (see :mod:`neofoam_ui.geometry`) enables
    # the field view; it is called once, the first time the view is opened.


@dataclass
class Event:
    """The point the engine is paused before."""

    kind: Literal["init", "exec", "test", "end"]
    node: Optional[str] = None
    depth: int = 0
    iter: Optional[int] = None
    #: Enclosing loops, outermost first, as ``(loop id, iteration)``.
    stack: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "node": self.node,
            "depth": self.depth,
            "iter": self.iter,
            "stack": [list(s) for s in self.stack],
        }


def assign_ids(ops: list[Op], prefix: str = "", pos: str = "") -> None:
    """Give every op a path id (``time_loop/inner_loop/solve``) and a position (``1.4.2``).

    Repeated names get ``#2``, ``#3``.
    """
    seen: dict[str, int] = {}
    for i, op in enumerate(ops, 1):
        seen[op.name] = seen.get(op.name, 0) + 1
        suffix = f"#{seen[op.name]}" if seen[op.name] > 1 else ""
        op.id = f"{prefix}{op.name}{suffix}"
        op.pos = f"{pos}{i}"
        assign_ids(op.children, op.id + "/", op.pos + ".")


def topological_order(steps: list[InitStep]) -> list[InitStep]:
    """Kahn's algorithm, stable in declaration order; an already valid order is kept as is."""
    by_name = {s.name: s for s in steps}
    for s in steps:
        for dep in s.depends_on:
            if dep not in by_name:
                raise ValueError(f"init step {s.name!r} depends on unknown step {dep!r}")
    position = {s.name: i for i, s in enumerate(steps)}
    if all(position[d] < position[s.name] for s in steps for d in s.depends_on):
        return list(steps)
    done: set[str] = set()
    order: list[InitStep] = []
    pending = list(steps)
    while pending:
        ready = [s for s in pending if all(d in done for d in s.depends_on)]
        if not ready:
            raise ValueError("cycle in init steps: " + ", ".join(s.name for s in pending))
        for s in ready:
            order.append(s)
            done.add(s.name)
            pending.remove(s)
    return order


def _fingerprint(arr: np.ndarray) -> int:
    return zlib.crc32(np.ascontiguousarray(arr).tobytes())


#: Commands that advance the engine; ``None`` = until breakpoint / end / pause.
Mode = Literal["into", "over", "out", "time_step", "skip_init", "run", "run_to"]


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
        #: Seconds to wait between events while running (animation); 0 = no events in between.
        self.delay = 0.0
        self.pause_requested = False
        self.stop_requested = False
        self.ctx: Any = None
        self.ops: list[Op] = []
        self.results: dict[str, Any] = {}
        self.calls: dict[str, int] = {}
        self.last_ms: dict[str, float] = {}
        #: Summed time per node over the whole run (timing heat map).
        self.total_ms: dict[str, float] = {}
        #: Fields each action was seen to change (a field fingerprint differed afterwards).
        self.writes_seen: dict[str, set[str]] = {}
        self.last_written_by: dict[str, str] = {}
        self.last_writes: list[str] = []
        self._prints: dict[str, int] = {}
        #: The thread a self-driving solver runs on (set by its program), see :meth:`in_solver_thread`.
        self.program_thread: Optional[ThreadedProgram] = None
        #: Field stats of the other MPI ranks, merged into :meth:`summary` (set by the worker).
        self.gather: Optional[Callable[[], list[dict[str, dict[str, Any]]]]] = None
        #: Field view: ``(field, component)`` to render at every pause, ``None`` = view closed.
        self.view: Optional[tuple[Optional[str], str]] = None
        #: Rank 0 under MPI: makes the other ranks render the same frame (set by the worker).
        self.on_render: Optional[Callable[[dict[str, str]], None]] = None
        #: Also render at every time step while running (not only at pauses).
        self.live_view = True
        self.insitu: Any = None  # neofoam_ui.catalyst.InSitu, started on the first frame
        self._insitu_error: Optional[str] = None
        self._geometry: Any = None  # None = not built yet, False = solver has no geometry
        self._view_sent: Optional[tuple[Any, ...]] = None
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
            self._timed(step.id, lambda: self.results.__setitem__(step.name, step.func(self.results)))
            self._log("init", step.id, step.name)
        self.set_context(self.solver.create_context(self.results))
        self.set_ops(self.solver.execution_graph(self.ctx))
        yield from self._walk(self.ops, 0, ())
        yield Event("end")

    # -- used by self-driving programs too ------------------------------------------

    def set_context(self, ctx: Any) -> None:
        self.ctx = ctx
        self._prints = self._field_prints()

    def set_ops(self, ops: list[Op]) -> None:
        self.ops = ops
        assign_ids(self.ops)
        self.emit({"type": "graph", "activity": "exec", "ops": [op_dict(o) for o in self.ops]})

    def in_solver_thread(self, fn: Callable[[], Any], timeout: Optional[float] = None) -> bool:
        """Run ``fn`` on the thread the solver runs on; False if that timed out."""
        if self.program_thread is None:  # tree mode: the solver runs on this thread
            fn()
            return True
        return self.program_thread.call(fn, timeout)

    def record(self, kind: str, node: str, name: str, ms: float, **extra: Any) -> None:
        """Book-keeping after an event ran: timing, call count, written fields, log line."""
        self._count(node, ms)
        if kind == "exec":
            self._track_writes(node, name)
        self._log(kind, node, name, **extra)

    def _walk(self, ops: list[Op], depth: int, stack: tuple[tuple[str, int], ...]) -> Iterator[Event]:
        for op in ops:
            if op.kind == "action":
                yield Event("exec", op.id, depth, stack=stack)
                self._timed(op.id, lambda: op.func(self.ctx))
                self._track_writes(op.id, op.name)
                self._log("exec", op.id, op.name, stack=stack)
                continue
            i = 0
            while True:
                i += 1
                yield Event("test", op.id, depth, iter=i, stack=stack)
                result = bool(self._timed(op.id, lambda: op.func(self.ctx)))
                self._log("test", op.id, op.name, stack=stack, iter=i, result=result)
                if not result:
                    break
                yield from self._walk(op.children, depth + 1, stack + ((op.id, i),))

    def _timed(self, node: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        out = fn()
        self._count(node, (time.perf_counter() - t0) * 1e3)
        return out

    def _count(self, node: str, ms: float) -> None:
        self.last_ms[node] = ms
        self.total_ms[node] = self.total_ms.get(node, 0.0) + ms
        self.calls[node] = self.calls.get(node, 0) + 1

    def _field_prints(self) -> dict[str, int]:
        return {k: _fingerprint(v) for k, v in self.solver.fields(self.ctx).items()}

    def _track_writes(self, node: str, op_name: str) -> None:
        now = self._field_prints()
        self.last_writes = [k for k, v in now.items() if self._prints.get(k) != v]
        for k in self.last_writes:
            self.last_written_by[k] = op_name
        if self.last_writes:
            self.writes_seen.setdefault(node, set()).update(self.last_writes)
        self._prints = now

    def _log(self, kind: str, node: str, name: str, **extra: Any) -> None:
        stack = extra.pop("stack", ())
        self.emit(
            {
                "type": "log",
                "kind": kind,
                "node": node,
                "name": name,
                "ms": round(self.last_ms.get(node, 0.0), 3),
                "stack": [list(s) for s in stack],
                "time": self.solver.scalars(self.ctx).get("time") if self.ctx is not None else None,
                **extra,
            }
        )

    # -- stepping ------------------------------------------------------------------

    def start(self) -> None:
        """Announce the init graph and the first pause."""
        self.emit({"type": "graph", "activity": "init", "steps": [init_dict(s) for s in self.init]})
        self._paused()

    def advance(self, mode: Mode, target: Optional[str] = None) -> None:
        """Execute the current event, then keep going until ``mode`` says stop."""
        if self.finished:
            return
        start = self.event
        until = self._until(mode, start, target)
        self.pause_requested = False
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
            if self.live_view and ev.kind == "test" and not ev.stack:  # new time step: live frame
                self.send_view()
            if self.delay > 0:
                self.emit({"type": "running", "event": ev.to_dict()})
                time.sleep(self.delay)
        self._paused()

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
        stats: dict[str, dict[str, Any]] = {}
        if self.ctx is None:
            return stats
        for name, arr in self.solver.fields(self.ctx).items():
            a = np.asarray(arr, dtype=float)
            label = name
            if a.ndim == 2:  # vector/tensor field: summarise the magnitude
                a = np.linalg.norm(a, axis=1)
                label = f"|{name}|"
            stats[name] = {
                "label": label,
                "min": float(a.min()) if a.size else None,
                "max": float(a.max()) if a.size else None,
                "sum": float(a.sum()),
                "n": int(a.size),
                "written_by": self.last_written_by.get(name, "init"),
                "changed": name in self.last_writes,
            }
        return stats

    def summary(self) -> dict[str, Any]:
        if self.ctx is None:
            return {"scalars": {}, "fields": [], "init_done": sorted(self.results)}
        stats = self.field_stats()
        # The other MPI ranks; not after a failure, they may be stuck in a collective.
        for other in self.gather() if self.gather and not self.failed else ():
            for name, theirs in other.items():
                if name not in stats:
                    stats[name] = dict(theirs)
                    continue
                mine = stats[name]
                values = [v for v in (mine["min"], theirs["min"]) if v is not None]
                mine["min"] = min(values) if values else None
                values = [v for v in (mine["max"], theirs["max"]) if v is not None]
                mine["max"] = max(values) if values else None
                mine["sum"] += theirs["sum"]
                mine["n"] += theirs["n"]
                mine["changed"] = mine["changed"] or theirs["changed"]
        rows = [
            {
                "field": name,
                "name": st["label"],
                "min": st["min"] if st["min"] is not None else float("nan"),
                "max": st["max"] if st["max"] is not None else float("nan"),
                "mean": st["sum"] / st["n"] if st["n"] else float("nan"),
                "written_by": st["written_by"],
                "changed": st["changed"],
            }
            for name, st in stats.items()
        ]
        return {"scalars": self.solver.scalars(self.ctx), "fields": rows,
                "init_done": sorted(self.results)}

    # -- field view (in situ, Catalyst 2) -------------------------------------------------

    def set_view(self, field: Optional[str], component: str = "mag", on: bool = True) -> None:
        """Open (``on``) or close the field view; renders the current frame right away."""
        self.view = (field, component) if on else None
        self._view_sent = None
        self.send_view()

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
        fields = {k: np.asarray(v) for k, v in self.solver.fields(self.ctx).items()}
        return self._geometry, {k: v for k, v in fields.items() if v.ndim in (1, 2) and len(v) == n}

    def _view_error(self, text: str) -> None:
        if self._view_sent != ("error", text):
            self._view_sent = ("error", text)
            self.emit({"type": "frame", "file": None, "fields": [], "error": text})

    def send_view(self) -> None:
        """Render the viewed field in situ if the view is open and the values changed."""
        if self.view is None or self.ctx is None:
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
        name, comp = self.view
        if name not in fields:
            name = next((k for k in ("U", "p", "T") if k in fields), next(iter(fields), None))
        if name is None:
            return
        arr = fields[name]
        ncomp = int(arr.shape[1]) if arr.ndim == 2 else 1
        if ncomp == 1 or comp not in ["mag"] + [str(i) for i in range(ncomp)]:
            comp = "mag"
        key = (name, comp, _fingerprint(arr))
        if key == self._view_sent:
            return
        self._view_sent = key
        params = {"field": name, "component": comp}
        if self.on_render is not None:
            self.on_render(params)
        file = self.render(params)
        if file is None:
            return self._view_error(self._insitu_error or "in-situ view failed")
        self.emit({
            "type": "frame", "file": file, "field": name, "component": comp,
            "time": self.solver.scalars(self.ctx).get("time"),
            "fields": [{"name": k, "ncomp": int(v.shape[1]) if v.ndim == 2 else 1}
                       for k, v in fields.items()],
        })

    def render(self, params: dict[str, str]) -> Optional[str]:
        """Hand this rank's cells and fields to Catalyst (collective); the image file name."""
        out: list[Optional[str]] = [None]

        def run() -> None:
            cells = self._cell_fields() if self.ctx is not None else None
            if cells is None:
                return
            from neofoam_ui.catalyst import InSitu  # noqa: PLC0415

            if self.insitu is None:
                self.insitu = InSitu()
            time_value = self.solver.scalars(self.ctx).get("time") or 0.0
            out[0] = self.insitu.execute(cells[0], cells[1], time_value, params)

        def guarded() -> None:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - the stepper outlives a broken view
                self._insitu_error = f"in-situ view failed: {exc!r}"
                self.emit({"type": "stdout", "text": "Catalyst: " + traceback.format_exc()})

        if self._insitu_error is None:
            self.in_solver_thread(guarded)
        return out[0]

    def finalize_view(self) -> None:
        """``catalyst_finalize`` (collective) on the solver thread, if a frame was rendered."""
        if self.insitu is not None:
            insitu, self.insitu = self.insitu, None
            self.in_solver_thread(insitu.finalize)

    def _paused(self) -> None:
        self.send_view()
        self.emit(
            {
                "type": "paused",
                "event": self.event.to_dict(),
                "finished": self.finished,
                "failed": self.failed,
                "summary": self.summary(),
                "calls": self.calls,
                "last_ms": {k: round(v, 3) for k, v in self.last_ms.items()},
                "total_ms": {k: round(v, 3) for k, v in self.total_ms.items()},
                "writes_seen": {k: sorted(v) for k, v in self.writes_seen.items()},
            }
        )


class _Stopped(BaseException):
    """Unwinds a solver thread whose program was closed."""


class ThreadedProgram:
    """Turns a solver that calls back into the stepper into an event generator.

    ``target()`` runs in a thread; whenever it calls :meth:`pause` with an
    :class:`Event`, that event is yielded and the thread blocks until the
    session asks for the next one. An exception in ``target`` is re-raised
    from the generator; ``target`` returning ends it with an ``end`` event.
    Closing the generator unwinds the blocked thread.

    The thread outlives ``target`` so that :meth:`call` can still run code on
    it — MPI must be finalized on the thread that initialised it.
    """

    def __init__(self, target: Callable[[], Any]) -> None:
        import queue  # noqa: PLC0415
        import threading  # noqa: PLC0415

        self._target = target
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        #: True = resume, False = unwind / end, a callable = run it here and keep waiting.
        self._resume: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._main, name="solver", daemon=True)
        #: What ``target`` returned.
        self.result: Any = None

    def _main(self) -> None:
        try:
            self.result = self._target()
        except _Stopped:
            return
        except BaseException as exc:  # noqa: BLE001 - handed to the session
            self._events.put(("error", exc))
        else:
            self._events.put(("done", None))
        while self._wait() is True:  # serve call() until closed
            pass

    def _wait(self) -> bool:
        """Block for the session's word, running any calls handed over meanwhile."""
        while True:
            cmd = self._resume.get()
            if isinstance(cmd, bool):
                return cmd
            cmd()

    def pause(self, event: Event) -> None:
        """Called from the solver thread: stop here until the session moves on."""
        self._events.put(("event", event))
        if not self._wait():
            raise _Stopped

    def call(self, fn: Callable[[], Any], timeout: Optional[float] = None) -> bool:
        """Run ``fn`` on the solver thread while it is paused or finished.

        Returns False if it did not complete within ``timeout`` (e.g. the solver
        thread is stuck inside an operation).
        """
        import threading  # noqa: PLC0415

        if not self._thread.is_alive():
            return False
        done = threading.Event()

        def run() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001 - must not kill the solver thread
                traceback.print_exc()
            finally:
                done.set()

        self._resume.put(run)
        return done.wait(timeout)

    def __iter__(self) -> Iterator[Event]:
        self._thread.start()
        while True:
            kind, value = self._events.get()
            if kind == "error":
                raise value
            if kind == "done":
                yield Event("end")
                return
            try:
                yield value
            except GeneratorExit:
                self._resume.put(False)
                raise
            self._resume.put(True)


def op_dict(op: Op) -> dict[str, Any]:
    return {
        "id": op.id,
        "name": op.name,
        "kind": op.kind,
        "domain": op.domain,
        "number": op.number,
        "pos": op.pos,
        "test": op.test,
        "guard": op.guard,
        "injected_by": op.injected_by,
        "reads": op.reads,
        "writes": op.writes,
        "doc": op.doc,
        "source": op.source or _source(op.func),
        "children": [op_dict(c) for c in op.children],
    }


def init_dict(step: InitStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "name": step.name,
        "depends_on": [f"init:{d}" for d in step.depends_on],
        "category": step.category,
        "doc": step.doc,
        "source": step.source or _source(step.func),
    }


def _source(func: Callable[..., Any]) -> Optional[str]:
    code = getattr(func, "__code__", None)
    if code is None:
        return None
    return f"{code.co_filename}:{code.co_firstlineno}"
