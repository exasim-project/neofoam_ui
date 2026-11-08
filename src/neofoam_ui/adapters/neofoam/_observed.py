"""Observed mode: NeoFOAM's own ``run()`` in a thread, paused by an ``ExecutionObserver``."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator, Optional

import numpy as np

from neofoam_ui.adapters.neofoam._base import _NeoFoamBase
from neofoam_ui.adapters.neofoam._ops import _convert, _doc, _source
from neofoam_ui.engine import Event, InitStep, Op, ProgramContext, ThreadedProgram
from neofoam_ui.engine._wire import _init_dict

__all__ = ()


class ObservedNeoFoamSolver(_NeoFoamBase):
    """Steps an unmodified ``run(argv)`` through NeoFOAM's execution observer."""

    def __init__(self, name: str, run: Callable[[list[str]], Any],
                 argv: Optional[list[str]] = None) -> None:
        self.name = name
        self._run = run
        # argv[0] must be a program name: OpenFOAM's argList crashes on an empty argv.
        self.argv = argv or ["neofoam"]

    def program(self, session: ProgramContext) -> Iterator[Event]:
        from neofoam.framework.execution_observer import observe  # noqa: PLC0415

        def target() -> Any:
            with observe(stepper):  # type: ignore[arg-type]  # duck-typed observer
                return self._run(self.argv)

        thread = session.program_thread = ThreadedProgram(target)
        stepper = _Stepper(session, thread.pause)
        for event in thread:
            if event.kind == "end" and thread.result is not None:
                session.ctx = thread.result
            yield event


class _Stepper:
    """``ExecutionObserver`` hooks → session events (all hooks implemented, so no base class)."""

    def __init__(self, session: ProgramContext, pause: Callable[[Event], None]) -> None:
        self.s = session
        self.pause = pause
        #: Enclosing loops, outermost first, as (loop id, iteration).
        self.stack: list[tuple[str, int]] = []
        #: id(Operation) → [(parent loop id or "", node id)]; one op object may sit at several places.
        self.nodes: dict[int, list[tuple[str, str]]] = {}
        self.names: dict[str, str] = {}
        self.t0 = 0.0
        #: Ops currently between before/after (an untracked op run inside a tracked one is skipped).
        self.open: list[Optional[str]] = []
        #: Per scope (loop id, "" = top level): nodes already run in its current pass, so the
        #: same Operation object listed twice under one parent maps to ``x`` then ``x#2``.
        self.taken: dict[str, set[str]] = {}
        #: Node of the loop test between before_loop_test and after_loop_test.
        self.test_node: Optional[str] = None
        #: Finished runs of each coupling loop (by its label), the x of its outcome table.
        self.coupling_runs: dict[str, int] = {}
        #: Coupling loop tests so far (all loops): the x of the region field tables.
        self.passes = 0
        #: What a NeoFOAM region's coupling op hands its sides in its running loop test
        #: (``coupling_exchanged``): ``(side, partner, exchange name, data)`` by coupling op.
        self.handed: dict[int, list[tuple[Any, Any, Any, Any]]] = {}

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
        regional = any(st.region is not None for st in new)
        if not regional or not self.s.init:
            self.s.init = new
            return
        # A region of a multi-region solver initializes after the driver: add its steps
        # and announce the grown init graph.
        self.s.init = [*self.s.init, *new]
        emit = getattr(self.s, "emit", None)
        if emit is not None:
            emit({"type": "graph", "activity": "init", "steps": [_init_dict(s) for s in self.s.init]})

    def before_init_step(self, step: Any, context: dict[str, Any]) -> None:
        self.pause(Event("init", f"init:{_init_name(step)}"))
        self.t0 = time.perf_counter()

    def after_init_step(self, step: Any, context: dict[str, Any], value: Any) -> None:
        ms = (time.perf_counter() - self.t0) * 1e3
        name = _init_name(step)
        self.s.results[name] = value
        self.s.observe_result(value)
        self.s.record("init", f"init:{name}", name, ms)

    # -- execution ------------------------------------------------------------------------

    def operations(self, operations: Any) -> None:
        injected: dict[int, str] = {}
        ops = [_convert(op, injected) for op in operations.ops]
        self.s.set_ops(ops)  # assigns the ids
        self.nodes.clear()

        def index(raw: list[Any], conv: list[Op], parent: str) -> None:
            for r, c in zip(raw, conv):
                self.nodes.setdefault(id(r), []).append((parent, c.id))
                self.names[c.id] = c.name
                if c.kind == "loop":
                    index(r.sub_operations, c.children, c.id)

        index(operations.ops, ops, "")

    def _pick(self, op: Any) -> Optional[str]:
        """The node ``op`` runs as here: the next not yet run in the current scope's pass."""
        parent = self.stack[-1][0] if self.stack else ""
        places = [node for p, node in self.nodes.get(id(op), ()) if p == parent]
        if not places:
            return None
        taken = self.taken.setdefault(parent, set())
        node = next((n for n in places if n not in taken), places[0])
        taken.add(node)
        return node

    def _see(self, ctx: Any, op: Any) -> None:
        # A region's op runs on its region's context; the session keeps the driver's,
        # which reaches every region (see _FieldAccess.regions).
        if getattr(op.metadata, "region", None) is not None and self.s.ctx is not None:
            return
        if self.s.ctx is not ctx:
            self.s.set_context(ctx)

    def before_operation(self, op: Any, ctx: Any) -> None:
        node = self._pick(op) if not any(self.open) else None
        self.open.append(node)
        if node is None:
            return
        self._see(ctx, op)
        self.pause(Event("exec", node, len(self.stack), stack=tuple(self.stack)))
        self.t0 = time.perf_counter()

    def after_operation(self, op: Any, ctx: Any) -> None:
        node = self.open.pop()
        if node is None:
            return
        ms = (time.perf_counter() - self.t0) * 1e3
        self.s.record("exec", node, self.names[node], ms, stack=tuple(self.stack))

    def before_loop_test(self, op: Any, ctx: Any, iteration: int) -> None:
        self.test_node = None
        self.handed.pop(id(op.func), None)  # (what an unrecorded test handed over)
        if any(self.open):
            return
        if self.stack and self.stack[-1][0] in {n for _, n in self.nodes.get(id(op), ())}:
            node = self.stack.pop()[0]  # the body of the previous iteration is done
        else:
            node = self._pick(op)  # first test: entering the loop
        if node is None:
            return
        self.taken.pop(node, None)  # its body starts a new pass
        self.test_node = node
        self._see(ctx, op)
        self.pause(Event("test", node, len(self.stack), iter=iteration, stack=tuple(self.stack)))
        self.t0 = time.perf_counter()

    def after_loop_test(self, op: Any, ctx: Any, iteration: int, result: bool) -> None:
        node, self.test_node = self.test_node, None
        if node is None:
            return
        ms = (time.perf_counter() - self.t0) * 1e3
        extra: dict[str, Any] = {}
        # NeoFOAM's coupling group: its leader decided on the residuals of the last pass
        group = type(op.func).__name__ == "CouplingGroup"
        residuals = getattr(getattr(op.func, "leader", None), "residuals", None) if group else None
        if isinstance(residuals, dict) and residuals:
            extra["residuals"] = {str(k): float(v) for k, v in residuals.items()}
        # a region's coupling op: what its test handed each side
        exchanges = _exchanged(self.handed.pop(id(op.func), []))
        if exchanges:
            extra["exchanges"] = exchanges
        self.s.record("test", node, self.names[node], ms, stack=tuple(self.stack),
                      iter=iteration, result=result, **extra)
        if group:
            self._region_fields(ctx)
        if result:
            self.stack.append((node, iteration))

    # -- coupling -------------------------------------------------------------------------

    def _region_fields(self, ctx: Any) -> None:
        """At a coupling loop's test: each region's fields (their mean) as one row of a table
        ``fields · <region>`` (Plots pane), x = the coupling passes so far (all loops)."""
        from neofoam_ui.adapters.neofoam._fields import _FieldAccess, _region_contexts  # noqa: PLC0415

        emit = getattr(self.s, "emit", None)
        contexts = _region_contexts(ctx)
        if emit is None or not contexts:
            return
        self.passes += 1
        for region, region_ctx in contexts.items():
            fields = _FieldAccess()._own_fields(region_ctx)
            if not fields:
                continue
            emit({"type": "pptable", "name": f"fields · {region}", "time": float(self.passes),
                  "columns": list(fields), "group_columns": [], "groups": [[]],
                  "values": [[float(np.mean(v)) if np.size(v) else None for v in fields.values()]],
                  "region": region, "x": "coupling pass"})

    def coupling_exchanged(self, coupling: Any, side: Any, data: Any) -> None:
        """A region's coupling op is about to hand ``side`` the (mapped) ``data``, in its loop
        test; recorded with the test."""
        try:
            partner, name = coupling.partner(side), coupling.exchange_of(side).name
        except Exception:  # noqa: BLE001 - a view must never break the run
            partner, name = None, None
        self.handed.setdefault(id(coupling), []).append((side, partner, name, data))

    def coupling_finished(self, coupling: Any, outcome: Any) -> None:
        """A coupling loop ended: its outcome as one row of a ``pptable`` (Plots pane).

        x is the loop's run count, not the time: a nested loop ends once per outer pass.
        """
        label = getattr(coupling, "label", None) or "coupling"
        run = self.coupling_runs[label] = self.coupling_runs.get(label, 0) + 1
        residuals = dict(getattr(outcome, "residuals", {}) or {})
        emit = getattr(self.s, "emit", None)
        if emit is None:
            return
        emit({"type": "pptable", "name": f"coupling · {label}", "time": float(run),
              "columns": ["passes", "converged", *(f"residual {q}" for q in residuals)],
              "group_columns": [], "groups": [[]],
              "values": [[float(outcome.passes), 1.0 if outcome.converged else 0.0,
                          *(float(v) for v in residuals.values())]]})


def _exchanged(handed: list[tuple[Any, Any, Any, Any]]) -> list[dict[str, Any]]:
    """What a NeoFOAM coupling op's test handed its sides (``coupling_exchanged``): per side
    ``{"from", "to", "exchange", "data"}``, the partner's data relaxed and mapped onto the
    side's points, each value summarised as text."""
    out = []
    for side, partner, name, data in handed:
        try:
            values = {str(n): _summary(v) for n, v in data}
        except Exception:  # noqa: BLE001 - a view must never break the run
            continue
        out.append({"from": getattr(partner, "region", None), "to": getattr(side, "region", None),
                    "exchange": None if name is None else str(name), "data": values})
    return out


def _summary(value: Any) -> str:
    """One number as ``1.5``, an array as its mean and size."""
    import numpy as np  # noqa: PLC0415

    array = np.asarray(value, dtype=float)
    if array.size == 1:
        return f"{float(array.ravel()[0]):.4g}"
    return f"mean {float(array.mean()):.4g} (n={array.size})"


def _init_name(step: Any, name: Optional[str] = None) -> str:
    """A step's name (or its dependency ``name``), prefixed with the step's region if any."""
    name = step.name if name is None else name
    region = getattr(step, "region", None)
    return f"{region}.{name}" if region else name
