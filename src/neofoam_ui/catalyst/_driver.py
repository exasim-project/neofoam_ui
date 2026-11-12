"""Who draws the frames a solver's in-situ operation asks for: a stepper session, or the operation itself."""

from __future__ import annotations

from typing import Callable, Optional

__all__ = ()

#: Draws one frame (``params``: field, component) on the calling (solver) thread, collectively.
FrameDriver = Callable[[dict[str, str]], None]


class _Driver:
    """Catalyst initializes once per process: under the stepper the operation's frames go through
    the field view's session (set here), never a second one."""

    current: Optional[FrameDriver] = None


def set_frame_driver(driver: Optional[FrameDriver]) -> None:
    """Route the frames of a solver's in-situ operation through ``driver``.

    The stepper's worker sets its session's ``solver_frame`` on every rank, so the operation
    draws through the field view's Catalyst session and the frames reach the UI; ``None``
    lets the operation start a session of its own. One per process, set before the solver
    runs (it is read on the solver thread)::

        catalyst.set_frame_driver(session.solver_frame)
    """
    _Driver.current = driver


def frame_driver() -> Optional[FrameDriver]:
    """The driver of :func:`set_frame_driver`, if any: what an in-situ operation asks first.

    ``None`` outside the stepper::

        driver = catalyst.frame_driver()
        if driver is not None:
            driver({"field": "p", "component": "mag"})
    """
    return _Driver.current
