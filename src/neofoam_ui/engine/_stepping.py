"""When a step stops: one policy for the tree walk, the self-driving thread and every MPI rank.

All of them decide with the same predicate (:func:`_until`), so ranks given the same
parameters pass the same events.
"""

from __future__ import annotations

from typing import Callable, Literal, Optional

from neofoam_ui.engine._graph import Event

__all__ = ()

#: Commands that advance the engine; ``None`` = until breakpoint / end / pause.
Mode = Literal["into", "over", "out", "time_step", "skip_init", "run", "run_to"]
#: Free runs: a self-driving solver passes the events they would not stop at on its own thread.
_FREE_MODES = ("run", "run_to", "time_step", "skip_init")


def _free_run(mode: Mode, start: Event) -> bool:
    """Run, run to, next time step, skip init — and stepping over the time loop (to the end)."""
    return mode in _FREE_MODES or (mode == "over" and start.kind == "test" and start.depth == 0)


def _until(mode: Mode, start: Event, target: Optional[str]) -> Callable[[Event], bool]:
    d = start.depth
    if mode == "into":
        return lambda ev: True
    if mode == "over":
        if start.kind == "test":
            # Run the whole loop: stop at the first event after it.
            return lambda ev: ev.depth <= d and not (ev.kind == "test" and ev.node == start.node)
        return lambda ev: True
    if mode == "out":
        if start.kind == "init":  # out of the initialization activity
            return lambda ev: ev.kind != "init"
        if d == 0:  # out of the top-level activity = run to the end
            return lambda ev: False
        return lambda ev: ev.depth < d
    if mode == "time_step":
        return lambda ev: ev.kind == "test" and ev.depth == 0
    if mode == "skip_init":
        return lambda ev: ev.kind != "init"
    if mode == "run_to":
        return lambda ev: ev.node == target
    if mode == "run":
        return lambda ev: False
    raise ValueError(f"unknown step mode {mode!r}")
