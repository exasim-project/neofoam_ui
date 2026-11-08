"""Tree mode (older NeoFOAM): the run re-assembled from its parts, see the package docs."""

from __future__ import annotations

from functools import partial
from typing import Any, Optional

from neofoam_ui.adapters.neofoam._base import _NeoFoamBase
from neofoam_ui.adapters.neofoam._ops import _convert, _doc, _source, _visible
from neofoam_ui.engine import InitStep, Op

__all__ = ()


class NeoFoamSolver(_NeoFoamBase):
    """Tree mode: the stepper re-assembles a NeoFOAM solver's run from its parts."""

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

        injected: dict[int, str] = {}
        for model in self.runtime.state.optional_models:
            for op in getattr(model, "operations", []) or []:
                injected[id(op)] = getattr(model, "name", type(model).__name__)
        # A NeoFOAM with resolve_operations also applies the graph edits (coupling loops).
        resolve = getattr(self.runtime, "resolve_operations", None)
        if resolve is not None:
            return [_convert(op, injected) for op in _visible(resolve(ctx).ops)]
        builder, model_ops = self.runtime.execution_graph(ctx=ctx)
        resolved = DAGResolver().resolve(builder, model_ops)
        return [_convert(op, injected) for op in _visible(resolved.operations.ops)]
