"""Which NeoFOAM solver."""

from __future__ import annotations

import importlib
from typing import Any, Optional

from neofoam_ui.adapters.neofoam._observed import ObservedNeoFoamSolver
from neofoam_ui.parallel import rank_info

__all__ = ()


def neofoam_solver(module: str, spec: str, run: str = "run", name: Optional[str] = None) -> Any:
    """The NeoFOAM solver ``spec`` of ``module``, stepped through its ``run(argv)`` (``run``).

    ``module.run`` executes unmodified; under MPI the solver gets ``-parallel``.
    Needs the case directory as the working directory.
    """
    # A package may re-export the spec under the submodule's name: import the module by path.
    mod = importlib.import_module(module)
    argv = ["neofoam", "-parallel"] if rank_info()[1] > 1 else ["neofoam"]
    return ObservedNeoFoamSolver(name or spec, getattr(mod, run), argv)


def incompressible_fluid() -> Any:
    """``neofoam solver incompressiblefluid`` (pybFoam backend)."""
    return neofoam_solver("neofoam.solver.incompressibleFluid.incompressibleFluid", "incompressibleFluid")


def multi_run() -> Any:
    """``neofoam solver multiRun``: every region of the case (``system/controlDict``
    ``regionSolvers``), coupled, in threads; the field view draws all regions together."""
    mod = importlib.import_module("neofoam.solver.multiRun.multiRun")
    return ObservedNeoFoamSolver("multiRun", lambda argv: mod.run(), ["neofoam"])
