"""Observed mode: NeoFOAM's own ``run()`` in a thread, paused by an ``ExecutionObserver``.

A multi-region case (multiRun) runs every region's solver on a thread of its own; the
threads report to the same observer, concurrently. Everything the stepper follows of a run
(the loop stack, the ops open, the nodes taken in a pass, the context) is therefore kept per
region (``ctx.region``, :class:`_RegionRun`) and booked under one lock; the region's events
pause in the engine's per-region gate (``Event.region``). The regions exchange data only
through their comm channel, whose blocking receives the stepper watches to show a region
"waiting for" its partner.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterator, Optional

import numpy as np

from neofoam_ui.adapters.neofoam._base import _NeoFoamBase
from neofoam_ui.adapters.neofoam._fields import _own_fields, _RegionContexts
from neofoam_ui.adapters.neofoam._ops import (
    _convert,
    _doc,
    _exchange_of,
    _qualified,
    _region_coupling,
    _source,
)
from neofoam_ui.engine import Event, InitStep, Op, ProgramContext, ThreadedProgram
from neofoam_ui.engine._wire import _init_dict
from neofoam_ui.pptables._rows import table_row

__all__ = ()


class ObservedNeoFoamSolver(_NeoFoamBase):
    """Steps an unmodified ``run(argv)`` through NeoFOAM's execution observer."""

    def __init__(self, name: str, run: Callable[[list[str]], Any],
                 argv: Optional[list[str]] = None) -> None:
        self.name = name
        self._run = run
        # argv[0] must be a program name: OpenFOAM's argList crashes on an empty argv.
        self.argv = argv or ["neofoam"]
        #: The observer of the running program (each region's context is in its books).
        self._stepper: Optional[_Stepper] = None

    def program(self, session: ProgramContext) -> Iterator[Event]:
        from neofoam.framework.execution_observer import observe  # noqa: PLC0415 - neofoam: the solver's venv only

        def target() -> Any:
            with observe(stepper):  # type: ignore[arg-type]  # duck-typed observer
                return self._run(self.argv)

        thread = session.program_thread = ThreadedProgram(target)
        stepper = self._stepper = _Stepper(session, thread.pause, thread.lanes)
        for event in thread:
            if event.kind == "end" and thread.result is not None:
                session.ctx = _final_context(thread.result)
            yield event


    def run_every_pass(self, region: Optional[str], ops: list[Op]) -> None:
        """``engine.PassControl``: the region's ``ctx.run_every_pass`` names ``ops``.

        The region's thread reads the set at each run of an op marked final; it is replaced,
        never changed in place, so any thread may hand it over (NeoFOAM checks the names at
        the start of a graph, and an op not marked final when it reaches it: only final ops
        get here). A NeoFOAM without ``run_every_pass``, or a region not running yet: nothing."""
        stepper = self._stepper
        if stepper is not None:
            stepper.run_every_pass(region, ops)


def _final_context(result: Any) -> Any:
    """What ``run()`` returned as the session's context: multiRun's ``{region: Context}``
    as the regions' contexts, a single solver's Context as it is."""
    if isinstance(result, dict) and result and all(hasattr(c, "fields") for c in result.values()):
        return _RegionContexts(result)
    return result


def _region(ctx: Any) -> Optional[str]:
    """The region a context belongs to (``ctx.region``); ``None``: a single-region solver."""
    region = getattr(ctx, "region", None)
    return None if region is None else str(region)


class _RegionRun:
    """What the stepper follows of one run: one region's thread (or the only one)."""

    def __init__(self) -> None:
        #: Enclosing loops, outermost first, as (loop id, iteration).
        self.stack: list[tuple[str, int]] = []
        #: id(Operation) → [(parent loop id or "", node id)]; one op object may sit at several places.
        self.nodes: dict[int, list[tuple[str, str]]] = {}
        self.t0 = 0.0
        #: Ops currently between before/after (an untracked op run inside a tracked one is skipped).
        self.open: list[Optional[str]] = []
        #: Per scope (loop id, "" = top level): nodes already run in its current pass, so the
        #: same Operation object listed twice under one parent maps to ``x`` then ``x#2``.
        self.taken: dict[str, set[str]] = {}
        #: Node of the loop test between before_loop_test and after_loop_test.
        self.test_node: Optional[str] = None
        #: What the region's coupling op hands its ports in its running loop test
        #: (``coupling_exchanged``): ``(port, partner region, exchange name, data)`` by op.
        self.handed: dict[int, list[tuple[Any, Any, Any, Any]]] = {}
        #: The region's coupling iterations so far: the x of its field table.
        self.iterations = 0
        #: The last top-level node: once it ran, the region's run is done.
        self.last: Optional[str] = None
        #: The region's context.
        self.ctx: Any = None

    def pick(self, op: Any) -> Optional[str]:
        """The node ``op`` runs as here: the next not yet run in the current scope's pass."""
        parent = self.stack[-1][0] if self.stack else ""
        places = [node for p, node in self.nodes.get(id(op), ()) if p == parent]
        if not places:
            return None
        taken = self.taken.setdefault(parent, set())
        node = next((n for n in places if n not in taken), places[0])
        taken.add(node)
        return node


class _Stepper:
    """``ExecutionObserver`` hooks → session events (all hooks implemented, so no base class).

    Hooks come from every region's thread at once: each region's bookkeeping is its own
    (:class:`_RegionRun`, by ``ctx.region``; ``None`` for a single-region solver), and
    each hook books and reports under :attr:`lock`, which it never holds while paused.
    """

    def __init__(self, session: ProgramContext, pause: Callable[[Event], None], lanes: Any = None) -> None:
        self.s = session
        self.pause = pause
        #: The engine's per-region gate (``_Lanes``): the receives and finished regions go there.
        self.lanes = lanes
        self.lock = threading.RLock()
        self.runs: dict[Optional[str], _RegionRun] = {}
        self.names: dict[str, str] = {}
        #: Finished couplings of each coupling group (by its controller's label), the x of
        #: its outcome table.
        self.coupling_runs: dict[str, int] = {}
        #: The regions' contexts, the session's context of a multi-region run.
        self.contexts = _RegionContexts()
        self._watched: set[int] = set()

    def run_of(self, region: Optional[str]) -> _RegionRun:
        run = self.runs.get(region)
        if run is None:
            run = self.runs[region] = _RegionRun()
        return run

    # -- init -----------------------------------------------------------------------------

    def init_steps(self, steps: list[Any]) -> None:
        names = {s.name for s in steps}
        new = [
            InitStep(_init_name(st), lambda results: None,
                     [_init_name(st, d) for d in st.depends_on if d in names],
                     category=st.category, doc=_doc(st.initializer),
                     source=_source(st.initializer), region=getattr(st, "region", None))
            for st in steps
        ]
        with self.lock:
            regional = any(st.region is not None for st in new)
            if not regional or not self.s.init:
                self.s.init = new
                if not regional:
                    return
            else:
                # each region initializes on its own thread: add its steps
                self.s.init = [*self.s.init, *new]
            emit = getattr(self.s, "emit", None)
            if emit is not None:  # announce the grown init graph
                emit({"type": "graph", "activity": "init", "steps": [_init_dict(s) for s in self.s.init]})

    def before_init_step(self, step: Any, context: dict[str, Any]) -> None:
        region = getattr(step, "region", None)
        self.pause(Event("init", f"init:{_init_name(step)}", region=region))
        with self.lock:
            self.run_of(region).t0 = time.perf_counter()

    def after_init_step(self, step: Any, context: dict[str, Any], value: Any) -> None:
        region = getattr(step, "region", None)
        with self.lock:
            ms = (time.perf_counter() - self.run_of(region).t0) * 1e3
            name = _init_name(step)
            self.s.results[name] = value
            self.s.observe_result(value)
            self.s.record("init", f"init:{name}", name, ms, **({"region": region} if region else {}))

    # -- execution ------------------------------------------------------------------------

    def operations(self, operations: Any, ctx: Any = None) -> None:
        """The resolved graph of ``ctx``'s region (every region reports its own)."""
        region = _region(ctx)
        injected: dict[int, str] = {}
        ops = [_convert(op, injected, ctx) for op in operations.ops]
        with self.lock:
            run = self.runs[region] = _RegionRun()
            run.ctx = ctx
            if region is None:
                self.s.set_ops(ops)  # assigns the ids
            else:
                coupling = _region_coupling(ctx)
                group = list(getattr(coupling, "group", None) or ())
                self._watch(getattr(coupling, "comm_channel", None))
                self.contexts[region] = ctx
                if group:  # (in stepping order, not the threads' order of arrival)
                    ordered = sorted(self.contexts.items(), key=lambda kv: group.index(kv[0]) if kv[0] in group else len(group))
                    self.contexts.clear()
                    self.contexts.update(ordered)
                self.s.set_context(self.contexts)
                self.s.set_region_ops(region, ops, group.index(region) if region in group else None)

            def index(raw: list[Any], conv: list[Op], parent: str) -> None:
                for r, c in zip(raw, conv):
                    run.nodes.setdefault(id(r), []).append((parent, c.id))
                    self.names[c.id] = c.name
                    if c.kind == "loop":
                        index(r.sub_operations, c.children, c.id)

            index(operations.ops, ops, "")
            run.last = ops[-1].id if ops else None

    def _see(self, ctx: Any, region: Optional[str]) -> None:
        if region is not None:  # the session holds every region's context (operations())
            return
        if self.s.ctx is not ctx:
            self.s.set_context(ctx)

    def _record(self, run: _RegionRun, region: Optional[str], kind: str, node: str, ms: float,
                **extra: Any) -> None:
        if region is not None:
            time_ = getattr(getattr(run.ctx, "time", None), "value", None)
            extra.update(region=region, time=time_)
        self.s.record(kind, node, self.names[node], ms, stack=tuple(run.stack), **extra)

    def _finished(self, run: _RegionRun, region: Optional[str], node: str) -> None:
        """The region's last top-level node ran: its run is done (its thread ends cleanly)."""
        if region is not None and node == run.last and not run.stack and self.lanes is not None:
            self.lanes.region_done(region)

    def before_operation(self, op: Any, ctx: Any) -> None:
        region = _region(ctx)
        with self.lock:
            run = self.run_of(region)
            node = run.pick(op) if not any(run.open) else None
            run.open.append(node)
            if node is None:
                return
            self._see(ctx, region)
            event = Event("exec", node, len(run.stack), stack=tuple(run.stack), region=region)
        self.pause(event)
        run.t0 = time.perf_counter()

    def after_operation(self, op: Any, ctx: Any) -> None:
        region = _region(ctx)
        with self.lock:
            run = self.run_of(region)
            node = run.open.pop()
            if node is None:
                return
            ms = (time.perf_counter() - run.t0) * 1e3
            self._record(run, region, "exec", node, ms)
            self._finished(run, region, node)

    def before_loop_test(self, op: Any, ctx: Any, iteration: int) -> None:
        region = _region(ctx)
        with self.lock:
            run = self.run_of(region)
            run.test_node = None
            run.handed.pop(id(op.func), None)  # (what an unrecorded test handed over)
            if any(run.open):
                return
            if run.stack and run.stack[-1][0] in {n for _, n in run.nodes.get(id(op), ())}:
                node = run.stack.pop()[0]  # the body of the previous iteration is done
            else:
                node = run.pick(op)  # first test: entering the loop
            if node is None:
                return
            run.taken.pop(node, None)  # its body starts a new pass
            run.test_node = node
            self._see(ctx, region)
            event = Event("test", node, len(run.stack), iter=iteration, stack=tuple(run.stack), region=region)
        self.pause(event)
        run.t0 = time.perf_counter()

    def after_loop_test(self, op: Any, ctx: Any, iteration: int, result: bool) -> None:
        region = _region(ctx)
        with self.lock:
            run = self.run_of(region)
            node, run.test_node = run.test_node, None
            if node is None:
                return
            ms = (time.perf_counter() - run.t0) * 1e3
            extra: dict[str, Any] = {}
            coupling = type(op.func).__name__ == "CouplingLoopOp"
            # the leader's coupling op: its Leader decided on the residuals of the last iteration
            residuals = getattr(getattr(op.func, "controller", None), "residuals", None) if coupling else None
            if isinstance(residuals, dict) and residuals:
                extra["residuals"] = {str(k): float(v) for k, v in residuals.items()}
            # the pass the test lets in is the loop's final one (its entry on ``ctx.loops``)
            loops = getattr(ctx, "loops", None)
            if result and loops and getattr(loops[-1], "final", False) is True:
                extra["final"] = True
            # a region's coupling op: what its test handed each port
            exchanges = _exchanged(run.handed.pop(id(op.func), []))
            if exchanges:
                extra["exchanges"] = exchanges
            self._record(run, region, "test", node, ms, iter=iteration, result=result, **extra)
            if coupling:
                self._region_fields(run, region, ctx)
            if result:
                run.stack.append((node, iteration))
            else:
                self._finished(run, region, node)

    # -- run every pass ---------------------------------------------------------------------

    def run_every_pass(self, region: Optional[str], ops: list[Op]) -> None:
        """Hand region ``region``'s context the names of ``ops`` (``ctx.run_every_pass``)."""
        with self.lock:
            run = self.runs.get(region)
            ctx = None if run is None else run.ctx
        if ctx is None or not hasattr(ctx, "run_every_pass"):
            return
        ctx.run_every_pass = {_qualified(op) for op in ops if op.final}  # a new set, in one step

    # -- coupling -------------------------------------------------------------------------

    def _watch(self, comm_channel: Any) -> None:
        """Watch the regions' comm channel (``ParticipantExchanges.comm_channel``): a receive that blocks
        shows its region as waiting for the sender, a send ends that wait.

        The comm channel is NeoFOAM's only link between regions, so this is all a region ever
        waits for. Patches the comm channel object (its ``receive`` / ``send``), once."""
        if comm_channel is None or self.lanes is None or id(comm_channel) in self._watched:
            return
        receive, send = getattr(comm_channel, "receive", None), getattr(comm_channel, "send", None)
        if not callable(receive) or not callable(send):
            return
        lanes = self.lanes
        self._watched.add(id(comm_channel))

        def watched_receive(source: str, target: str, tag: str) -> Any:
            with lanes.waiting(str(target), str(source), str(tag)):
                return receive(source, target, tag)

        def watched_send(source: str, target: str, tag: str, message: Any) -> None:
            lanes.delivered(str(source), str(target), str(tag))
            send(source, target, tag, message)

        comm_channel.receive, comm_channel.send = watched_receive, watched_send

    def _region_fields(self, run: _RegionRun, region: Optional[str], ctx: Any) -> None:
        """At a region's coupling op test: its fields (their mean) as one row of a table
        ``fields · <region>`` (Plots pane), x = the region's coupling iterations so far."""
        emit = getattr(self.s, "emit", None)
        if emit is None or region is None:
            return
        fields = _own_fields(ctx)
        run.iterations += 1
        if not fields:
            return
        emit(table_row(f"fields · {region}", run.iterations, list(fields),
                       [float(np.mean(v)) if np.size(v) else None for v in fields.values()],
                       region=region, x="coupling iteration"))

    def coupling_exchanged(self, coupling: Any, port: Any, data: Any) -> None:
        """A region's coupling op is about to hand ``port`` the (mapped) ``data``, in its loop
        test; recorded with the test."""
        found = _exchange_of(coupling, port)
        partner, name = (None, None) if found is None else (found[1], getattr(found[0], "name", None))
        with self.lock:
            run = self.run_of(getattr(coupling, "region", None))
            run.handed.setdefault(id(coupling), []).append((port, partner, name, data))

    def coupling_finished(self, coupling: Any, outcome: Any) -> None:
        """A time step's coupling ended (the Leader, from its thread): its outcome
        as one row of a ``pptable`` (Plots pane).

        x is the coupling's run count, not the time.
        """
        label = getattr(coupling, "label", None) or "coupling"
        residuals = dict(getattr(outcome, "residuals", {}) or {})
        with self.lock:
            run = self.coupling_runs[label] = self.coupling_runs.get(label, 0) + 1
            emit = getattr(self.s, "emit", None)
            if emit is None:
                return
            emit(table_row(f"coupling · {label}", run,
                           ["iterations", "converged", *(f"residual {q}" for q in residuals)],
                           [float(outcome.iterations), 1.0 if outcome.converged else 0.0,
                            *(float(v) for v in residuals.values())]))


def _exchanged(handed: list[tuple[Any, Any, Any, Any]]) -> list[dict[str, Any]]:
    """What a NeoFOAM coupling op's test handed its ports (``coupling_exchanged``): per port
    ``{"from", "to", "exchange", "data"}``, the partner's data relaxed and mapped onto the
    port's points, each value summarised as text."""
    out = []
    for port, partner, name, data in handed:
        try:
            values = {str(n): _summary(v) for n, v in data}
        except Exception:  # noqa: BLE001 - a view must never break the run
            continue
        out.append({"from": None if partner is None else str(partner), "to": getattr(port, "region", None),
                    "exchange": None if name is None else str(name), "data": values})
    return out


def _summary(value: Any) -> str:
    """One number as ``1.5``, an array as its mean and size."""
    array = np.asarray(value, dtype=float)
    if array.size == 1:
        return f"{float(array.ravel()[0]):.4g}"
    return f"mean {float(array.mean()):.4g} (n={array.size})"


def _init_name(step: Any, name: Optional[str] = None) -> str:
    """A step's name (or its dependency ``name``), prefixed with the step's region if any."""
    name = step.name if name is None else name
    region = getattr(step, "region", None)
    return f"{region}.{name}" if region else name
