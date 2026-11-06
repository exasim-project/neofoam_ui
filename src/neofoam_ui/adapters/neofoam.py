"""Adapter: a NeoFOAM solver as a stepper :class:`~neofoam_ui.engine.Solver`.

Two modes:

* **observed** (NeoFOAM with ``neofoam.framework.execution_observer``): the
  solver's own ``run()`` executes unmodified in a thread; an
  ``ExecutionObserver`` pauses it before every init step, operation and loop
  test (:class:`ObservedNeoFoamSolver`);
* **tree** (older NeoFOAM): the stepper re-assembles the run from its parts
  (:class:`NeoFoamSolver`, below).

``NEOFOAM_UI_MODE=tree|observed`` forces one; the default is observed when available.

Tree mode takes apart what ``Solver.initialize()`` + ``execution_graph`` + ``DAGResolver``
do in a solver's ``run()`` so the engine can pause between the pieces:

* **init steps** — the solver's ``StagedInitRunner`` (injected into its
  ``@initializer``) is driven through load → resolve → build; each resulting
  ``InitStep`` runs through neofoam's own ``execute_step``;
* **context** — ``build_context_from_results`` on the step results, plus the
  runtime-state bookkeeping of ``SolverSpec._run_initialize``;
* **execution graph** — ``runtime.execution_graph(ctx)`` resolved by
  ``DAGResolver``; actions run through ``Operation.run`` (telemetry spans stay),
  loops are evaluated by the engine with the op's own predicate.

Needs neofoam importable (pybFoam + a sourced OpenFOAM) and the case directory
as the working directory, exactly like ``neofoam solver <name>``.
"""

from __future__ import annotations

import importlib
import inspect
import os
import time
from functools import partial
from typing import Any, Callable, Iterator, Optional

import numpy as np

from neofoam_ui.engine import Event, InitStep, Op, Session, ThreadedProgram
from neofoam_ui.parallel import rank_info

__all__ = ["NeoFoamSolver", "ObservedNeoFoamSolver", "has_observer", "incompressible_fluid"]


def has_observer() -> bool:
    try:
        import neofoam.framework.execution_observer  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


class _FieldsMixin:
    def scalars(self, ctx: Any) -> dict[str, Any]:
        t = getattr(ctx, "time", None)
        if t is None:
            return {}
        return {"time": t.value, "deltaT": t.delta_t, "timeIndex": t.index,
                "writeTime": t.write_time}

    def fields(self, ctx: Any) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for name, field in (getattr(ctx, "fields", None) or {}).items():
            try:
                out[name] = np.asarray(field.internalField())
            except Exception:  # not a geometric field (or no internalField)
                continue
        return out


class ObservedNeoFoamSolver(_FieldsMixin):
    """Steps an unmodified ``run(argv)`` through NeoFOAM's execution observer."""

    def __init__(self, name: str, run: Callable[[list[str]], Any],
                 argv: Optional[list[str]] = None) -> None:
        self.name = name
        self._run = run
        # argv[0] must be a program name: OpenFOAM's argList crashes on an empty argv.
        self.argv = argv or ["neofoam"]

    def program(self, session: Session) -> Iterator[Event]:
        from neofoam.framework.execution_observer import observe  # noqa: PLC0415

        def target() -> Any:
            with observe(stepper):  # type: ignore[arg-type]  # duck-typed observer
                return self._run(self.argv)

        thread = ThreadedProgram(target)
        stepper = _Stepper(session, thread.pause)
        for event in thread:
            if event.kind == "end" and thread.result is not None:
                session.ctx = thread.result
            yield event


class _Stepper:
    """``ExecutionObserver`` hooks → session events (all hooks implemented, so no base class)."""

    def __init__(self, session: Session, pause: Callable[[Event], None]) -> None:
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

    # -- init -----------------------------------------------------------------------------

    def init_steps(self, steps: list[Any]) -> None:
        names = {s.name for s in steps}
        self.s.init = [
            InitStep(st.name, lambda results: None,
                     [d for d in st.depends_on if d in names],
                     category=st.category, doc=_doc(st.initializer),
                     source=_source(st.initializer))
            for st in steps
        ]

    def before_init_step(self, step: Any, context: dict[str, Any]) -> None:
        self.pause(Event("init", f"init:{step.name}"))
        self.t0 = time.perf_counter()

    def after_init_step(self, step: Any, context: dict[str, Any], value: Any) -> None:
        ms = (time.perf_counter() - self.t0) * 1e3
        self.s.results[step.name] = value
        self.s.record("init", f"init:{step.name}", step.name, ms)

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

    def _node(self, op: Any) -> Optional[str]:
        parent = self.stack[-1][0] if self.stack else ""
        for p, node in self.nodes.get(id(op), ()):
            if p == parent:
                return node
        return None

    def _see(self, ctx: Any) -> None:
        if self.s.ctx is not ctx:
            self.s.set_context(ctx)

    def before_operation(self, op: Any, ctx: Any) -> None:
        node = self._node(op) if not any(self.open) else None
        self.open.append(node)
        if node is None:
            return
        self._see(ctx)
        self.pause(Event("exec", node, len(self.stack), stack=tuple(self.stack)))
        self.t0 = time.perf_counter()

    def after_operation(self, op: Any, ctx: Any) -> None:
        node = self.open.pop()
        if node is None:
            return
        ms = (time.perf_counter() - self.t0) * 1e3
        self.s.record("exec", node, self.names[node], ms, stack=tuple(self.stack))

    def before_loop_test(self, op: Any, ctx: Any, iteration: int) -> None:
        if self.stack and self.stack[-1][0] in {n for _, n in self.nodes.get(id(op), ())}:
            self.stack.pop()  # the body of the previous iteration is done
        node = self._node(op)
        if node is None or any(self.open):
            return
        self._see(ctx)
        self.pause(Event("test", node, len(self.stack), iter=iteration, stack=tuple(self.stack)))
        self.t0 = time.perf_counter()

    def after_loop_test(self, op: Any, ctx: Any, iteration: int, result: bool) -> None:
        node = self._node(op)
        if node is None or any(self.open):
            return
        ms = (time.perf_counter() - self.t0) * 1e3
        self.s.record("test", node, self.names[node], ms, stack=tuple(self.stack),
                      iter=iteration, result=result)
        if result:
            self.stack.append((node, iteration))


class NeoFoamSolver(_FieldsMixin):
    def __init__(self, spec: Any, argv: Optional[list[str]] = None) -> None:
        self.spec = spec
        self.name = spec.name
        # argv[0] must be a program name: OpenFOAM's argList crashes on an empty argv.
        self.runtime = spec.instantiate(argv=argv or ["neofoam"])
        self._runner: Any = None
        self._sorted: list[Any] = []

    # -- initialization -----------------------------------------------------------------

    def _make_runner(self) -> Any:
        from neofoam.framework.initialization import StagedInitRunner  # noqa: PLC0415

        spec = self.spec
        kwargs = spec._dependency_resolver.resolve_arguments(spec._initialize_func, None)
        runners = [v for v in kwargs.values() if isinstance(v, StagedInitRunner)]
        if len(runners) != 1:
            raise TypeError(
                f"solver {self.name!r}: the stepper needs an @initializer that takes one "
                "StagedInitRunner (e.g. Depends(create_init)) and returns init.run()"
            )
        runner = runners[0]
        runner.argv = self.runtime.argv
        return runner

    def init_steps(self) -> list[InitStep]:
        from neofoam.framework.initialization.config_context import ConfigContext  # noqa: PLC0415
        from neofoam.framework.initialization.execution import (  # noqa: PLC0415
            InitializationGraphError,
            execute_step,
            validate,
        )
        from neofoam.framework.initialization.execution.ordering import (  # noqa: PLC0415
            _topological_sort,
        )
        from neofoam.framework.initialization.execution.validation import (  # noqa: PLC0415
            check_replacements,
        )

        # Mirrors StagedInitRunner.run() up to (excluding) execution.
        runner = self._runner = self._make_runner()
        load_result = runner.run_load()
        config = ConfigContext()
        for loaded_model in load_result.all_models:
            key = getattr(loaded_model, "name", None) or type(loaded_model).__name__.lower()
            if config.contains(key):
                raise ValueError(f"Duplicate model registration key: '{key}'")
            config.register(key, loaded_model)
        runner.run_resolve(config)
        lazy_inits = runner.run_build()

        report = validate(lazy_inits)
        if not report.is_valid:
            raise InitializationGraphError(report)
        check_replacements(lazy_inits)
        self._sorted = _topological_sort(lazy_inits, validate_graph=False)

        names = {s.name for s in self._sorted}
        return [
            InitStep(
                s.name,
                partial(execute_step, s),
                [d for d in s.depends_on if d in names],
                category=s.category,
                doc=_doc(s.initializer),
                source=_source(s.initializer),
            )
            for s in self._sorted
        ]

    def create_context(self, results: dict[str, Any]) -> Any:
        from neofoam.framework.initialization.execution import (  # noqa: PLC0415
            InitResult,
            build_context_from_results,
            default_router,
        )

        init_results = [
            InitResult(name=s.name, category=s.category, value=results[s.name], write=s.write)
            for s in self._sorted
        ]
        ctx = build_context_from_results(init_results, router=default_router())

        # Bookkeeping of SolverSpec._run_initialize after the initializer returned.
        runtime, runner = self.runtime, self._runner
        if getattr(runner, "state", None) is not None:
            runtime.state = runner.state
        else:
            runtime.state.core_models = runner.core_models
            runtime.state.optional_models = runner.optional_models
        if self.spec._config_classes and runtime.config is None:
            aggregated = self.spec._aggregate_configs(runtime.state.core_models)
            if aggregated is not None:
                runtime.config = aggregated
        return ctx

    # -- execution graph ------------------------------------------------------------------

    def execution_graph(self, ctx: Any) -> list[Op]:
        from neofoam.framework.graph import DAGResolver  # noqa: PLC0415

        builder, model_ops = self.runtime.execution_graph(ctx=ctx)
        injected: dict[int, str] = {}
        for model in self.runtime.state.optional_models:
            for op in getattr(model, "operations", []) or []:
                injected[id(op)] = getattr(model, "name", type(model).__name__)
        resolved = DAGResolver().resolve(builder, model_ops)
        return [_convert(op, injected) for op in resolved.operations.ops]


def _convert(op: Any, injected: dict[int, str]) -> Op:
    meta = op.metadata
    name = meta.op_name or meta.name or "operation"
    target, guard = _target(op)
    common = dict(
        domain=meta.domain_name,
        number=str(meta.operation_number) if meta.operation_number is not None else None,
        doc=meta.description or _doc(target),
        source=_source(target),
        injected_by=injected.get(id(op)),
    )
    if op.operation_type == "iterative":
        predicate = op.func.func
        test = (type(predicate).__name__ + "()") if not inspect.isfunction(predicate) \
            else f"{predicate.__name__}()"
        return Op(name, "loop", op.func, [_convert(c, injected) for c in op.sub_operations],
                  test=test, **common)
    return Op(name, "action", op.run, guard=guard, **common)


def _target(op: Any) -> tuple[Callable[..., Any], Optional[str]]:
    """The user function behind an op, and a guard if a solver gate wraps it."""
    func = inspect.unwrap(getattr(op.func, "func", op.func))
    # incompressibleFluid's _under_turb_corr: a closure ``gated`` over the original op.
    if getattr(func, "__name__", "") == "gated" and func.__closure__:
        for cell in func.__closure__:
            inner = cell.cell_contents
            if hasattr(inner, "metadata") and hasattr(inner, "func"):
                return _target(inner)[0], "turbCorr()"
    return func, None


def _doc(func: Any) -> str:
    doc = inspect.getdoc(func) or ""
    return doc.split("\n\n", 1)[0]


def _source(func: Any) -> Optional[str]:
    try:
        target = inspect.unwrap(func)
        target = getattr(target, "__func__", target)
        if not (inspect.isfunction(target) or inspect.ismethod(target)):
            target = type(target).__call__
        return f"{inspect.getsourcefile(target)}:{inspect.getsourcelines(target)[1]}"
    except (TypeError, OSError):
        return None


def _observed() -> bool:
    mode = os.environ.get("NEOFOAM_UI_MODE", "")
    if mode not in ("", "tree", "observed"):
        raise ValueError(f"NEOFOAM_UI_MODE must be 'tree' or 'observed', got {mode!r}")
    if mode == "observed" and not has_observer():
        raise RuntimeError("NEOFOAM_UI_MODE=observed, but this NeoFOAM has no execution observer")
    return mode == "observed" or (mode == "" and has_observer())


def incompressible_fluid() -> Any:
    """``neofoam solver incompressiblefluid`` (pybFoam backend)."""
    # The package re-exports the spec under the submodule's name, so import by path.
    module = importlib.import_module("neofoam.solver.incompressibleFluid.incompressibleFluid")

    argv = ["neofoam", "-parallel"] if rank_info()[1] > 1 else ["neofoam"]
    if _observed():
        return ObservedNeoFoamSolver("incompressibleFluid", module.run, argv)
    return NeoFoamSolver(module.incompressibleFluid, argv)
