"""What the stepper needs from a solver, and the seams it is extended through.

Small protocols, each one capability (checked with ``isinstance``, decided once per session):

* every solver is a :class:`FieldSource` (``name``, ``scalars``, ``fields``) and either a
  :class:`GraphSolver` (the stepper runs its init steps and operation tree) or a
  :class:`SelfDrivingSolver` (it runs itself and reports through a :class:`ProgramContext`);
* optional: :class:`MeshProvider` and :class:`ViewFieldProvider` (the field view),
  :class:`RegionProvider` (several mesh regions), :class:`ResultObserver` (sees every
  init-step result, e.g. to hook post-processing);
* :class:`Renderer` / :class:`RenderBackend`: what draws the field view (default: Catalyst);
* :class:`Peers`: the other MPI ranks, as rank 0 keeps them in step.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional, Protocol, Union, runtime_checkable

import numpy as np

from neofoam_ui.engine._graph import Event, InitStep, Op

__all__ = ()

_Emit = Callable[[dict[str, Any]], None]


@runtime_checkable
class FieldSource(Protocol):
    """What every solver offers: its name, the scalars and the cell fields of a run context."""

    name: str

    def scalars(self, ctx: Any) -> dict[str, Any]: ...

    def fields(self, ctx: Any) -> dict[str, np.ndarray]: ...


@runtime_checkable
class GraphSolver(FieldSource, Protocol):
    """A solver the stepper runs: init steps, the context they build, then the operation tree."""

    def init_steps(self) -> list[InitStep]: ...

    def create_context(self, results: dict[str, Any]) -> Any:
        """Build the run context from the init-step results (keyed by step name)."""

    def execution_graph(self, ctx: Any) -> list[Op]: ...


class ProgramContext(Protocol):
    """What a self-driving program reports to (the :class:`~neofoam_ui.engine.Session`)."""

    init: list[InitStep]
    results: dict[str, Any]
    ctx: Any
    ops: list[Op]
    #: The thread the program runs on (a :class:`~neofoam_ui.engine.ThreadedProgram`).
    program_thread: Any

    def set_context(self, ctx: Any) -> None: ...

    def set_ops(self, ops: list[Op]) -> None: ...

    def set_region_ops(self, region: str, ops: list[Op], rank: Optional[int] = None) -> None:
        """A multi-region solver: region ``region``'s own graph (each region reports its own)."""

    def observe_result(self, value: Any) -> None: ...

    def record(self, kind: str, node: str, name: str, ms: float, **extra: Any) -> None: ...

    def in_solver_thread(self, fn: Callable[[], Any], timeout: Optional[float] = None) -> bool: ...


@runtime_checkable
class SelfDrivingSolver(Protocol):
    """A solver that drives itself: ``program`` yields an :class:`Event` before each step
    (see :class:`~neofoam_ui.engine.ThreadedProgram`); ``init_steps`` and co. are not used."""

    def program(self, session: ProgramContext) -> Iterator[Event]: ...


#: A solver the stepper can step (and a :class:`FieldSource` either way).
Solver = Union[GraphSolver, SelfDrivingSolver]


@runtime_checkable
class MeshProvider(Protocol):
    """``mesh(ctx)``: this rank's cells for the field view (a :mod:`neofoam_ui.geometry` dict).

    Called once, the first time the view is opened."""

    def mesh(self, ctx: Any) -> Optional[dict[str, Any]]: ...


@runtime_checkable
class ViewFieldProvider(Protocol):
    """``view_fields(ctx)``: the fields the view offers, if more than ``fields`` (model fields)."""

    def view_fields(self, ctx: Any) -> dict[str, np.ndarray]: ...


@runtime_checkable
class ResultObserver(Protocol):
    """``observe(value, emit)``: offered every init-step result (e.g. the post-processor)."""

    def observe(self, value: Any, emit: _Emit) -> None: ...


@runtime_checkable
class Region(Protocol):
    """One mesh region: its cells and its cell fields."""

    name: str

    def mesh(self, ctx: Any) -> Optional[dict[str, Any]]: ...

    def fields(self, ctx: Any) -> dict[str, np.ndarray]: ...


@runtime_checkable
class RegionProvider(Protocol):
    """A solver with several mesh regions (CHT, FSI); the first is the one viewed for now."""

    def regions(self, ctx: Any) -> list[Region]: ...


@runtime_checkable
class Renderer(Protocol):
    """Draws one rank's part of a frame (collective under MPI, on the solver thread).

    Optional: ``describe() -> dict`` (the pipeline, for the diagram), ``prune(keep)`` (delete
    older images) and ``ms`` (timings of the last execute)."""

    def execute(self, geom: dict[str, Any], fields: dict[str, np.ndarray], time: float,
                params: dict[str, str]) -> str: ...

    def finalize(self) -> None: ...


class RenderBackend(Protocol):
    """Makes the :class:`Renderer` of a session (on its first frame)."""

    def unavailable(self) -> Optional[str]:
        """Why there is no view (e.g. not configured), or ``None``."""

    def publishes_all(self) -> bool:
        """Hand every cell field to the renderer, not only the viewed one."""

    def create(self) -> Renderer: ...


class Peers(Protocol):
    """The other ranks of an MPI run, seen from rank 0 (see :mod:`neofoam_ui.parallel`).

    Lockstep: rank 0 calls :meth:`go` before it resumes its own program, :meth:`free` with
    the same parameters it passes itself, :meth:`render` before its own (collective) render."""

    def go(self, track: bool) -> None:
        """Resume past the current event (tracking writes or not), as rank 0 is about to."""

    def free(self, params: Optional[dict[str, Any]]) -> None:
        """The free-run parameters of rank 0 (``Session.set_free``)."""

    def render(self, params: dict[str, str]) -> None:
        """Render the frame rank 0 is about to render."""

    def stats(self) -> list[dict[str, dict[str, Any]]]:
        """Each other rank's field stats (``Session.field_stats``)."""

    def stop(self) -> None: ...
