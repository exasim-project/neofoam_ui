"""Extending the UI without editing it: panes, registered through an :class:`ExtensionRegistry`.

A pane is a factory ``factory(ctx) -> Pane`` (``ctx``: the app, as a :class:`PaneContext`).
The built-in ones (DAG, Field, Pipeline, Plots) come first; extension panes follow in the
order they were added. A pane with a :class:`PaneToggle` gets a toolbar button bound to
its state key; ``placement="side"`` puts it beside the field view, ``"main"`` in the canvas.

Register them

* explicitly: ``StepperApp(server, solver, registry=registry)``, or
* from an installed package, through the entry point group ``neofoam_ui.extensions``:
  ``[project.entry-points."neofoam_ui.extensions"] mine = "mypkg:register"`` with
  ``def register(registry): registry.add_pane(MyPane)``. Loaded once, when the app starts.

Other extension points (solvers, mesh regions, renderers, post-processing node describers)
live with what they extend: :mod:`neofoam_ui.engine`, :mod:`neofoam_ui.pptables`.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Callable, Literal, Mapping, Optional, Protocol

__all__ = ["ENTRY_POINT_GROUP", "BasePane", "ExtensionRegistry", "Pane", "PaneContext", "PaneToggle"]

#: Entry point group whose ``register(registry)`` functions are called once at start-up.
ENTRY_POINT_GROUP = "neofoam_ui.extensions"

_Handler = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class PaneToggle:
    """A pane's toolbar button: ``key`` is the trame state flag that shows the pane."""

    key: str
    label: str
    icon: str
    title: str


class PaneContext(Protocol):
    """What a pane may use of the app."""

    #: The trame server and its state (pane state keys should share a prefix, e.g. ``fv_``).
    server: Any
    state: Any
    #: MPI ranks of the run (1: serial).
    np: int

    @property
    def run_dir(self) -> str:
        """The current run's directory below the served ``insitu/`` (its images)."""

    def send(self, cmd: dict[str, Any]) -> None:
        """A command to the worker (see :mod:`neofoam_ui.worker`); dropped without a worker."""

    def console(self, text: str, cls: str = "") -> None:
        """A line in the simulation console (``cls``: ``err``, ``out``, ``init``, ``test``, ``bp``)."""

    def name(self, node_id: Optional[str]) -> str:
        """A graph node's name (its id while unknown)."""

    def wants_frames(self) -> bool:
        """Some pane needs field-view frames (:meth:`Pane.wants_frames`)."""


class Pane(Protocol):
    """One pane of the UI."""

    name: str
    toggle: Optional[PaneToggle]
    placement: Literal["main", "side"]
    #: CSS added to the page.
    css: str

    def build(self) -> None:
        """The pane's widgets (called once, inside its slot)."""

    def reset(self) -> None:
        """Defaults of its state: at start-up and on every restart (before the new worker)."""

    def handlers(self) -> Mapping[str, _Handler]:
        """Worker messages it handles, by ``type`` (see :mod:`neofoam_ui.protocol`)."""

    def spawned(self) -> None:
        """A worker started: send it this pane's settings."""

    def layout_changed(self) -> None:
        """A pane opened or closed (e.g. refit a diagram)."""

    def wants_frames(self) -> bool:
        """The worker should render field-view frames for this pane."""


class BasePane:
    """A :class:`Pane` with nothing to do: subclass it and override what the pane needs."""

    name = "pane"
    toggle: Optional[PaneToggle] = None
    placement: Literal["main", "side"] = "side"
    css = ""

    def __init__(self, ctx: PaneContext) -> None:
        self.ctx = ctx

    def build(self) -> None:
        pass

    def reset(self) -> None:
        pass

    def handlers(self) -> Mapping[str, _Handler]:
        return {}

    def spawned(self) -> None:
        pass

    def layout_changed(self) -> None:
        pass

    def wants_frames(self) -> bool:
        return False


_PaneFactory = Callable[[PaneContext], Pane]


class ExtensionRegistry:
    """The panes added to the built-in ones, in order."""

    def __init__(self) -> None:
        self._panes: list[_PaneFactory] = []
        self._loaded = False

    def add_pane(self, factory: _PaneFactory) -> None:
        """Add a pane after the built-in ones and those added before."""
        self._panes.append(factory)

    @property
    def panes(self) -> tuple[_PaneFactory, ...]:
        return tuple(self._panes)

    def load_entry_points(self, group: str = ENTRY_POINT_GROUP) -> None:
        """Call every installed ``register(registry)`` of ``group`` (once; again is a no-op)."""
        if self._loaded:
            return
        self._loaded = True
        for ep in entry_points(group=group):
            ep.load()(self)
