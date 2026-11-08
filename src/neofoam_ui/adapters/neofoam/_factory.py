"""Which NeoFOAM solver, in which mode."""

from __future__ import annotations

import importlib
import os
from typing import Any, Optional

from neofoam_ui.adapters.neofoam._observed import ObservedNeoFoamSolver
from neofoam_ui.adapters.neofoam._tree import NeoFoamSolver
from neofoam_ui.parallel import rank_info
from neofoam_ui.protocol import MODE_ENV

__all__ = ()



def has_observer() -> bool:
    """This NeoFOAM has ``neofoam.framework.execution_observer`` (observed mode)."""
    try:
        import neofoam.framework.execution_observer  # noqa: F401, PLC0415 - the probe itself
    except ImportError:
        return False
    return True


def _observed() -> bool:
    mode = os.environ.get(MODE_ENV, "")
    if mode not in ("", "tree", "observed"):
        raise ValueError(f"{MODE_ENV} must be 'tree' or 'observed', got {mode!r}")
    if mode == "observed" and not has_observer():
        raise RuntimeError(f"{MODE_ENV}=observed, but this NeoFOAM has no execution observer")
    return mode == "observed" or (mode == "" and has_observer())


def neofoam_solver(module: str, spec: str, run: str = "run", name: Optional[str] = None) -> Any:
    """The NeoFOAM solver ``spec`` (a ``SolverSpec``) of ``module``, whose ``run(argv)`` is ``run``.

    Observed mode steps ``module.run`` unmodified, tree mode re-assembles ``module.spec``;
    under MPI the solver gets ``-parallel``. Needs the case directory as the working directory.
    """
    # A package may re-export the spec under the submodule's name: import the module by path.
    mod = importlib.import_module(module)
    argv = ["neofoam", "-parallel"] if rank_info()[1] > 1 else ["neofoam"]
    if _observed():
        return ObservedNeoFoamSolver(name or spec, getattr(mod, run), argv)
    return NeoFoamSolver(getattr(mod, spec), argv)


def incompressible_fluid() -> Any:
    """``neofoam solver incompressiblefluid`` (pybFoam backend)."""
    return neofoam_solver("neofoam.solver.incompressibleFluid.incompressibleFluid", "incompressibleFluid")
