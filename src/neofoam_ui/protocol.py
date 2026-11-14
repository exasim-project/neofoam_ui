"""The contract between the UI process, the solver worker and its MPI ranks.

Dependency-free: imported by both sides (the worker's interpreter need not have trame).

* **UI → worker** (worker stdin, one JSON object per line): ``{"cmd": <COMMANDS>, ...}``,
  see :mod:`neofoam_ui.worker` for the fields of each command.
* **worker → UI** (worker stdout, one JSON object per line): ``{"type": <MESSAGES>, ...}``.
* **rank 0 → follower rank** (socket, one JSON object per line): ``{"op": <RANK_OPS>, ...}``,
  see :mod:`neofoam_ui.parallel`.
* **UI → worker command line**: ``python -m neofoam_ui.worker <module:Factory> [--sync host:port]
  [--catalyst PREFIX] [--data host:port]``, see :mod:`neofoam_ui.worker`; the rest comes from
  the config files (:mod:`neofoam_ui.config`).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

__all__ = [
    "COMMANDS",
    "LOSSY",
    "MESSAGES",
    "RANK_OPS",
    "UI_URGENT",
    "WORKER_URGENT",
]

# -- UI → worker ----------------------------------------------------------------------------------

COMMANDS = frozenset({"step", "pause", "breakpoints", "every_pass", "delay", "watch", "wait", "ack", "view",
                      "live", "frames", "export", "stop"})

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
