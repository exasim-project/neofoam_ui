"""Solver worker process: runs a :class:`~neofoam_ui.engine.Session`, controlled over stdio.

Protocol — one JSON object per line.

UI → worker (stdin)::

    {"cmd": "step", "mode": "into|over|out|time_step|skip_init|run|run_to", "node": "<id>"}
    {"cmd": "pause"}                      # honoured between two events
    {"cmd": "breakpoints", "ids": [...]}
    {"cmd": "every_pass", "ids": [...]}   # ops marked final to run in every pass (debug)
    {"cmd": "delay", "seconds": 0.2}      # animation while running (0: none)
    {"cmd": "watch", "on": true}          # the DAG pane is open: report the events of a run
    {"cmd": "wait", "on": true}           # "wait for UI": block until the UI acknowledged
    {"cmd": "ack", "seq": 7}              # ... the ``sync`` message 7 (and all before it)
    {"cmd": "view", "field": "U", "component": "mag|0|1|2", "on": true,  # field view
     "mode": "image|data",                    # data: Catalyst Live
     "options": {"width": "800", ...}}        # display options (pipelines/view.py)
    {"cmd": "live", "on": true}           # field view frames while running ...
    {"cmd": "frames", "every": 1, "seconds": 0.5}   # ... every N time steps, >= s apart
    {"cmd": "stop"}

worker → UI (stdout): the engine messages (``graph``, ``log``, ``paused``,
``running``, ``breakpoint``, ``error``, and for an open field view a ``frame`` —
in image mode the PNG Catalyst rendered in situ (base64, ``image``), in data mode none
(Catalyst Live carries the data) — at each pause where the values changed; with ``pipeline_ms`` and, when it changed, the ``pipeline`` diagram; for NeoFOAM's
post-processing tables ``pptables`` once and a ``pptable`` row set per table and step, see
:mod:`neofoam_ui.pptables`) plus ``stdout`` for anything the solver
prints. File descriptor 1 is redirected into a pipe, so neither Python ``print``
nor C++ output (OpenFOAM ``Info``) can corrupt the protocol stream, which uses a
duplicate of the original stdout.

The solver never waits for the UI: messages go through a queue to a writer thread,
and while the UI lags behind, the writer thins out what only shows progress — the
``log`` / ``running`` / ``stdout`` messages of a batch beyond the newest few (a ``skipped``
message counts what was left out). Everything else (``paused``, ``frame``, ``pptable``,
errors, ``stopped``, …) always goes out, in order. With "wait for UI" on nothing is thinned,
and the session blocks at every time step and frame on a ``sync`` message until the UI
answers it with an ``ack``: every event and frame is shown, the solver runs at the UI's pace.

Usage: ``python -m neofoam_ui.worker neofoam_ui.toy.heat1d:Heat1D``

The message and command names are in :mod:`neofoam_ui.protocol`.

Under ``mpirun`` only rank 0 speaks this protocol; see :mod:`neofoam_ui.parallel`.
"""

from neofoam_ui.worker._main import load_solver, main

__all__ = ["load_solver", "main"]
