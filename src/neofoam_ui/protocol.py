"""The contract between the UI process, the solver worker and its MPI ranks.

Dependency-free: imported by both sides (the worker's interpreter need not have trame).

* **UI → worker** (worker stdin, one JSON object per line): ``{"cmd": <COMMANDS>, ...}``,
  see :mod:`neofoam_ui.worker` for the fields of each command.
* **worker → UI** (worker stdout, one JSON object per line): ``{"type": <MESSAGES>, ...}``.
* **rank 0 → follower rank** (socket, one JSON object per line): ``{"op": <RANK_OPS>, ...}``,
  see :mod:`neofoam_ui.parallel`.
* **environment** the UI sets for the worker, or the user for either.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

__all__ = [
    "CATALYST_ENV",
    "CATALYST_LIVE_ENV",
    "CATALYST_OUT_ENV",
    "CATALYST_SCRIPT_ENV",
    "COMMANDS",
    "DELAY_ENV",
    "LOSSY",
    "MESSAGES",
    "MODE_ENV",
    "RANK_OPS",
    "SYNC_ENV",
    "UI_URGENT",
    "WAIT_ENV",
    "WORKER_URGENT",
]

# -- environment --------------------------------------------------------------------------------

#: Catalyst 2 + ParaView install prefix for the in-situ field view.
CATALYST_ENV = "NEOFOAM_UI_CATALYST"
#: Directory the worker's Catalyst writes its images to (set by the UI).
CATALYST_OUT_ENV = "NEOFOAM_UI_CATALYST_OUT"
#: Another Catalyst pipeline script than ``pipelines/view.py`` (gets every cell field).
CATALYST_SCRIPT_ENV = "NEOFOAM_UI_CATALYST_SCRIPT"
#: ``host:port`` for ParaView's Catalyst Live (``off`` or unset: none). The pipeline script
#: ``pipelines/view.py`` reads the same name on its own (it imports nothing from here).
CATALYST_LIVE_ENV = "NEOFOAM_UI_CATALYST_LIVE"
#: ``host:port`` rank 0 listens on for the other ranks of an MPI run (set by the UI).
SYNC_ENV = "NEOFOAM_UI_SYNC"
#: ``tree`` or ``observed``: how the NeoFOAM adapter steps a solver (default: observed if possible).
MODE_ENV = "NEOFOAM_UI_MODE"
#: Default animation delay in ms (the UI's ``--delay``).
DELAY_ENV = "NEOFOAM_UI_DELAY_MS"
#: Default of "wait for UI" (the UI's ``--wait-ui``).
WAIT_ENV = "NEOFOAM_UI_WAIT"

# -- UI → worker ----------------------------------------------------------------------------------

COMMANDS = frozenset({"step", "pause", "breakpoints", "delay", "watch", "wait", "ack", "view", "live",
                      "frames", "stop"})

# -- worker → UI ----------------------------------------------------------------------------------

MESSAGES = frozenset({"graph", "log", "log_error", "stdout", "skipped", "breakpoint", "running", "paused",
                      "error", "frame", "pptables", "pptable", "stopped", "sync"})
#: Progress-only messages the worker thins out while the UI lags: kept per batch, the newest n.
LOSSY: Mapping[str, int] = MappingProxyType({"log": 40, "running": 1, "stdout": 200})
#: Messages the worker writes at once (the others wait a moment to go out together).
WORKER_URGENT = frozenset({"paused", "sync", "stopped", "error", "log_error", "breakpoint", "frame", "graph"})
#: Messages the UI pushes to the browser at once (the others at a capped rate).
UI_URGENT = frozenset({"paused", "stopped", "error", "frame", "graph", "sync"})

# -- rank 0 → followers -------------------------------------------------------------------------

RANK_OPS = frozenset({"go", "stats", "render", "free", "stop"})
