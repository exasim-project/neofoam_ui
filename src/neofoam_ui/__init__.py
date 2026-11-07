"""neofoam_ui — step through a NeoFOAM solver graph like an activity-diagram simulation.

The public API (everything else is private, ``_``-prefixed):

* :mod:`neofoam_ui.engine` — the stepper core: graph, session, solver protocols (re-exported here);
* :mod:`neofoam_ui.app` — the trame UI (``neofoam-ui``), :mod:`neofoam_ui.extensions` — its panes;
* :mod:`neofoam_ui.worker` — the solver process, :mod:`neofoam_ui.protocol` — what both sides say;
* :mod:`neofoam_ui.parallel`, :mod:`neofoam_ui.catalyst`, :mod:`neofoam_ui.geometry`,
  :mod:`neofoam_ui.pptables` — MPI, the in-situ view, meshes, post-processing tables;
* :mod:`neofoam_ui.adapters.neofoam`, :mod:`neofoam_ui.toy.heat1d` — solvers.

Importing this package loads neither trame nor a solver framework.
"""

from neofoam_ui.engine import (
    Event,
    FieldSource,
    GraphSolver,
    InitStep,
    Op,
    SelfDrivingSolver,
    Session,
    Solver,
    ThreadedProgram,
    assign_ids,
    topological_order,
)

__all__ = [
    "Event",
    "FieldSource",
    "GraphSolver",
    "InitStep",
    "Op",
    "SelfDrivingSolver",
    "Session",
    "Solver",
    "ThreadedProgram",
    "assign_ids",
    "topological_order",
]
