"""Adapter: a NeoFOAM solver as a stepper :class:`~neofoam_ui.engine.Solver`.

Two modes:

* **observed** (NeoFOAM with ``neofoam.framework.execution_observer``): the
  solver's own ``run()`` executes unmodified in a thread; an
  ``ExecutionObserver`` pauses it before every init step, operation and loop
  test (:class:`ObservedNeoFoamSolver`);
* **tree** (older NeoFOAM): the stepper re-assembles the run from its parts
  (:class:`NeoFoamSolver`, below).

``NEOFOAM_UI_MODE=tree|observed`` forces one; the default is observed when available.

A NeoFOAM solver is one factory away: ``--solver mypkg:my_solver`` with
``def my_solver(): return neofoam_solver("neofoam.solver.mySolver.mySolver", "mySolver")``
(:func:`incompressible_fluid` is exactly that).

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

from neofoam_ui.adapters.neofoam._factory import has_observer, incompressible_fluid, neofoam_solver
from neofoam_ui.adapters.neofoam._observed import ObservedNeoFoamSolver
from neofoam_ui.adapters.neofoam._tree import NeoFoamSolver

__all__ = ["NeoFoamSolver", "ObservedNeoFoamSolver", "has_observer", "incompressible_fluid", "neofoam_solver"]
