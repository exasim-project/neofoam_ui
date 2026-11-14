"""MPI runs: rank 0 talks to the UI, every other rank follows it event by event.

``mpirun -np N python -m neofoam_ui.worker ...`` starts one worker per rank.
Rank 0 is the ordinary worker (stdin/stdout protocol to the UI). The others
are *followers*: they connect to rank 0 over a socket (the worker's ``--sync host:port``,
set by the UI) and run the same solver, but only advance when rank 0 does —
one ``go`` per event — so all ranks pause at the same event and a step never
leaves a rank behind in a collective. In a free run (Run, Run to, Next time step)
every rank gets the same parameters once (``free``) and passes the events in
between on its own; only the time steps' loop tests, the target and the
breakpoints still take a ``go``. At each pause rank 0 collects the
followers' field statistics, so the UI shows global min / max / mean.

Rank 0 → follower, one JSON object per line (``op`` names in :mod:`neofoam_ui.protocol`)::

    {"op": "go", "track": false}   # resume past the current event (tracking writes or not)
    {"op": "stats"}   # reply with Session.field_stats()
    {"op": "render", "params": {...}}   # in-situ frame: Session.render (collective)
    {"op": "free", "params": {...}}     # a free run: Session.set_free (null: none)
    {"op": "stop"}

The rank is read from the launcher's environment (Open MPI, MPICH / PMI);
nothing here calls MPI.
"""

from neofoam_ui.parallel._ranks import rank_info

__all__ = ["rank_info"]
