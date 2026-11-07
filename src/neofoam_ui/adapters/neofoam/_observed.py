"""Observed mode: NeoFOAM's own ``run()`` in a thread, paused by an ``ExecutionObserver``."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator, Optional

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
        extra = {}
        residuals = getattr(op.func, "residuals", None)  # a NeoFOAM CouplingOp: the last pass's
        if isinstance(residuals, dict) and residuals:
            extra["residuals"] = {str(k): float(v) for k, v in residuals.items()}
        self.s.record("test", node, self.names[node], ms, stack=tuple(self.stack),
                      iter=iteration, result=result, **extra)
        if result:
            self.stack.append((node, iteration))

    # -- coupling -------------------------------------------------------------------------

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


def _init_name(step: Any, name: Optional[str] = None) -> str:
    """A step's name (or its dependency ``name``), prefixed with the step's region if any."""
    name = step.name if name is None else name
    region = getattr(step, "region", None)
    return f"{region}.{name}" if region else name
