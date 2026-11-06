"""Adapter: a NeoFOAM solver as a stepper :class:`~neofoam_ui.engine.Solver`.

It takes apart what ``Solver.initialize()`` + ``execution_graph`` + ``DAGResolver``
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

import inspect
from functools import partial
from typing import Any, Callable, Optional

import numpy as np

from neofoam_ui.engine import InitStep, Op

__all__ = ["NeoFoamSolver", "incompressible_fluid"]


class NeoFoamSolver:
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

    # -- state ------------------------------------------------------------------------------

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


def _convert(op: Any, injected: dict[int, str]) -> Op:
    meta = op.metadata
    name = meta.op_name or meta.name or "operation"
    target, guard = _target(op)
    common = dict(
        domain=meta.domain_name,
        number=str(meta.operation_number) if meta.operation_number is not None else None,
        doc=_doc(target),
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


def incompressible_fluid() -> NeoFoamSolver:
    """``neofoam solver incompressiblefluid`` (pybFoam backend)."""
    from neofoam.solver.incompressibleFluid.incompressibleFluid import (  # noqa: PLC0415
        incompressibleFluid,
    )

    return NeoFoamSolver(incompressibleFluid)
