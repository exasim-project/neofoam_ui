"""Step-by-step execution of a solver graph — the core of the DAG stepper (no trame here).

* :class:`Op`, :class:`InitStep` — the graph a solver describes; :class:`Event` — the point
  the engine is paused before;
* :class:`Session` — drives one run event by event, see :mod:`neofoam_ui.engine._session`;
* :class:`ThreadedProgram` — a solver that drives itself (calls back into the stepper);
* :class:`Solver` — what the stepper needs from a solver.
"""

from neofoam_ui.engine._graph import Event, InitStep, Op, assign_ids, topological_order
from neofoam_ui.engine._protocols import Solver
from neofoam_ui.engine._session import UNTRACKED, Mode, Session
from neofoam_ui.engine._threaded import ThreadedProgram

__all__ = [
    "Event",
    "InitStep",
    "Mode",
    "Op",
    "Session",
    "Solver",
    "ThreadedProgram",
    "UNTRACKED",
    "assign_ids",
    "topological_order",
]
