"""Step-by-step execution of a solver graph — the core of the DAG stepper (no trame here).

* :class:`Op`, :class:`InitStep` — the graph a solver describes; :class:`Event` — the point
  the engine is paused before;
* :class:`Session` — drives one run event by event, see :mod:`neofoam_ui.engine._session`;
* :class:`ThreadedProgram` — a solver that drives itself (calls back into the stepper);
* :class:`Solver` and the small protocols around it — what the stepper needs from a solver
  and the seams it is extended through (regions, rendering), see
  :mod:`neofoam_ui.engine._protocols`.
"""

from neofoam_ui.engine._graph import Event, InitStep, Op, assign_ids, topological_order
from neofoam_ui.engine._protocols import (
    FieldSource,
    GraphSolver,
    MeshProvider,
    Peers,
    ProgramContext,
    Region,
    RegionProvider,
    RenderBackend,
    Renderer,
    ResultObserver,
    SelfDrivingSolver,
    Solver,
    ViewFieldProvider,
)
from neofoam_ui.engine._regions import DEFAULT_REGION
from neofoam_ui.engine._session import Mode, Session
from neofoam_ui.engine._tracking import UNTRACKED
from neofoam_ui.engine._threaded import ThreadedProgram

__all__ = [
    "DEFAULT_REGION",
    "Event",
    "FieldSource",
    "GraphSolver",
    "InitStep",
    "MeshProvider",
    "Mode",
    "Op",
    "Peers",
    "ProgramContext",
    "Region",
    "RegionProvider",
    "RenderBackend",
    "Renderer",
    "ResultObserver",
    "SelfDrivingSolver",
    "Session",
    "Solver",
    "ThreadedProgram",
    "UNTRACKED",
    "ViewFieldProvider",
    "assign_ids",
    "topological_order",
]
