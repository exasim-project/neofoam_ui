"""What the stepper needs from a solver."""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from neofoam_ui.engine._graph import InitStep, Op

__all__ = ()


class Solver(Protocol):
    """What the stepper needs from a solver.

    A solver may instead provide ``program(session) -> Iterator[Event]`` and drive
    itself (see :class:`ThreadedProgram`); then only ``scalars`` and ``fields`` are
    used, and ``init_steps`` / ``create_context`` / ``execution_graph`` are not.
    """

    name: str

    def init_steps(self) -> list[InitStep]: ...

    def create_context(self, results: dict[str, Any]) -> Any:
        """Build the run context from the init-step results (keyed by step name)."""

    def execution_graph(self, ctx: Any) -> list[Op]: ...

    def scalars(self, ctx: Any) -> dict[str, Any]: ...

    def fields(self, ctx: Any) -> dict[str, np.ndarray]: ...

    # Optional: ``mesh(ctx) -> geometry dict`` (see :mod:`neofoam_ui.geometry`) enables
    # the field view; it is called once, the first time the view is opened.
    # Optional: ``view_fields(ctx)`` — the fields the view offers, if more than ``fields``
    # (e.g. model fields that the stats table leaves out).
    # Optional: ``observe(value, emit)`` — offered every init-step result, e.g. to hook the
    # post-processing tables (``pptable`` messages, see :mod:`neofoam_ui.pptables`).
