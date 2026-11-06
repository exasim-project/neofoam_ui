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


def assign_ids(ops: list[Op], prefix: str = "") -> None:
    """Give every op a path id (``time_loop/inner_loop/solve``); repeated names get ``#2``, ``#3``."""
    seen: dict[str, int] = {}
    for op in ops:
        seen[op.name] = seen.get(op.name, 0) + 1
        suffix = f"#{seen[op.name]}" if seen[op.name] > 1 else ""
        op.id = f"{prefix}{op.name}{suffix}"
        assign_ids(op.children, op.id + "/")


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
        self.last_written_by: dict[str, str] = {}
        self.last_writes: list[str] = []
        self._prints: dict[str, int] = {}
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

    def record(self, kind: str, node: str, name: str, ms: float, **extra: Any) -> None:
        """Book-keeping after an event ran: timing, call count, written fields, log line."""
        self.last_ms[node] = ms
        self.calls[node] = self.calls.get(node, 0) + 1
        if kind == "exec":
            self._track_writes(name)
        self._log(kind, node, name, **extra)

    def _walk(self, ops: list[Op], depth: int, stack: tuple[tuple[str, int], ...]) -> Iterator[Event]:
        for op in ops:
            if op.kind == "action":
                yield Event("exec", op.id, depth, stack=stack)
                self._timed(op.id, lambda: op.func(self.ctx))
                self._track_writes(op.name)
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
        self.last_ms[node] = (time.perf_counter() - t0) * 1e3
        self.calls[node] = self.calls.get(node, 0) + 1
        return out

    def _field_prints(self) -> dict[str, int]:
        return {k: _fingerprint(v) for k, v in self.solver.fields(self.ctx).items()}

    def _track_writes(self, op_name: str) -> None:
        now = self._field_prints()
        self.last_writes = [k for k, v in now.items() if self._prints.get(k) != v]
        for k in self.last_writes:
            self.last_written_by[k] = op_name
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

    def summary(self) -> dict[str, Any]:
        if self.ctx is None:
            return {"scalars": {}, "fields": [], "init_done": sorted(self.results)}
        rows = []
        for name, arr in self.solver.fields(self.ctx).items():
            a = np.asarray(arr, dtype=float)
            label = name
            if a.ndim == 2:  # vector/tensor field: summarise the magnitude
                a = np.linalg.norm(a, axis=1)
                label = f"|{name}|"
            rows.append(
                {
                    "name": label,
                    "min": float(a.min()),
                    "max": float(a.max()),
                    "mean": float(a.mean()),
                    "written_by": self.last_written_by.get(name, "init"),
                    "changed": name in self.last_writes,
                }
            )
        return {"scalars": self.solver.scalars(self.ctx), "fields": rows,
                "init_done": sorted(self.results)}

    def _paused(self) -> None:
        self.emit(
            {
                "type": "paused",
                "event": self.event.to_dict(),
                "finished": self.finished,
                "failed": self.failed,
                "summary": self.summary(),
                "calls": self.calls,
                "last_ms": {k: round(v, 3) for k, v in self.last_ms.items()},
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
    """

    def __init__(self, target: Callable[[], Any]) -> None:
        import queue  # noqa: PLC0415
        import threading  # noqa: PLC0415

        self._target = target
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        self._resume: queue.Queue[bool] = queue.Queue(maxsize=1)
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
            return
        self._events.put(("done", None))

    def pause(self, event: Event) -> None:
        """Called from the solver thread: stop here until the session moves on."""
        self._events.put(("event", event))
        if not self._resume.get():
            raise _Stopped

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
