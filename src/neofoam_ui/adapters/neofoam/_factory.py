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
    register_insitu()
    argv = ["neofoam", "-parallel"] if rank_info()[1] > 1 else ["neofoam"]
    return ObservedNeoFoamSolver(name or spec, getattr(mod, run), argv)


def incompressible_fluid() -> Any:
    """``neofoam solver incompressiblefluid`` (pybFoam backend)."""
    return neofoam_solver("neofoam.solver.incompressibleFluid.incompressibleFluid", "incompressibleFluid")


def multi_run() -> Any:
    """``neofoam solver multiRun``: every region of the case (``system/controlDict``
    ``participants``), coupled, in threads; the field view draws all regions together."""
    mod = importlib.import_module("neofoam.solver.multiRun.multiRun")
    register_insitu()
    return ObservedNeoFoamSolver("multiRun", lambda argv: mod.run(), ["neofoam"])


def register_insitu() -> None:
    """Register the ``catalyst`` in-situ type with NeoFOAM's ``postProcess`` (idempotent).

    Use it to get Catalyst images while a case runs, at a cadence of its own, without stepping
    it in the UI. :func:`neofoam_solver` calls it before the solver starts; a plain
    ``neofoam solver`` run calls it from the case's ``system/postProcess.py``. Declared in
    ``system/postProcess.yaml``::

        insitu:
          - name: view
            type: catalyst
            field: U            # default: U, p or T, whichever the case has
            component: mag      # mag, 0, 1, 2
            write_control: {write_control_type: timeStep, interval: 5}

    Under the stepper the frames go through its field view
    (:func:`neofoam_ui.catalyst.set_frame_driver`): they reach the UI with the pipeline diagram,
    and a field picked in the UI wins (serial runs). On its own the entry writes ``view_*.png``
    to ``$NEOFOAM_UI_CATALYST_OUT``, else ``postProcessing/<name>`` of the case. Collective:
    every rank calls it at the same steps, on the solver thread. Needs neofoam importable.
    """
    importlib.import_module("neofoam_ui.adapters.neofoam._insitu")
