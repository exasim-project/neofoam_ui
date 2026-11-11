"""Adapter: a NeoFOAM solver as a stepper :class:`~neofoam_ui.engine.Solver`.

The solver's own ``run()`` executes unmodified in a thread; NeoFOAM's
``ExecutionObserver`` (``neofoam.framework.execution_observer``) pauses it
before every init step, operation and loop test (:class:`ObservedNeoFoamSolver`).

A NeoFOAM solver is one factory away: ``--solver mypkg:my_solver`` with
``def my_solver(): return neofoam_solver("neofoam.solver.mySolver.mySolver", "mySolver")``
(:func:`incompressible_fluid` is exactly that; :func:`multi_run` runs a multi-region case).

Needs neofoam importable (pybFoam + a sourced OpenFOAM) and the case directory
as the working directory, exactly like ``neofoam solver <name>``.
"""

from neofoam_ui.adapters.neofoam._factory import incompressible_fluid, multi_run, neofoam_solver
from neofoam_ui.adapters.neofoam._observed import ObservedNeoFoamSolver

__all__ = ["ObservedNeoFoamSolver", "incompressible_fluid", "multi_run", "neofoam_solver"]
