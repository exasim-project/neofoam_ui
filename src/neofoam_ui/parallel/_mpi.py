"""``MPI_Finalize`` on stop (through ctypes: nothing here links MPI)."""

from __future__ import annotations

import ctypes
import ctypes.util

from neofoam_ui.engine import Session

__all__ = ()

#: Seconds a rank waits for MPI_Finalize on stop before exiting regardless.
_FINALIZE_TIMEOUT = 10.0


def _finalize_mpi() -> None:
    """``MPI_Finalize`` if the solver initialised MPI, so ``mpirun`` sees a clean exit.

    OpenFOAM finalizes in the argList destructor, which never runs here: the
    session holds the context until the process ends. Collective, so every rank
    calls it on ``stop``. Call it on the thread that initialised MPI
    (:meth:`Session.in_solver_thread`), with a timeout: a rank stuck in a
    collective never returns from it.
    """
    name = ctypes.util.find_library("mpi")
    if name is None:
        return
    try:
        lib = ctypes.CDLL(name)  # the copy OpenFOAM's Pstream already loaded
    except OSError:
        return
    flag = ctypes.c_int(0)
    lib.MPI_Initialized(ctypes.byref(flag))
    if not flag.value:
        return
    lib.MPI_Finalized(ctypes.byref(flag))
    if flag.value:
        return
    lib.MPI_Finalize()


def _finish(session: Session) -> bool:
    """Finalize MPI on the solver's thread; False if it did not complete in time."""
    return session.in_solver_thread(_finalize_mpi, _FINALIZE_TIMEOUT)
