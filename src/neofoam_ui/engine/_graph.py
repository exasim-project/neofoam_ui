"""The solver graph: actions, loops, init steps, and the events the engine pauses before."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

__all__ = ()


@dataclass(frozen=True)
class PointMapping:
    """How a coupling node's data reaches its side's interface points (preCICE's "mapping").

    The partner's data lives on the partner's interface points; the node hands it over on its
    own side's points. Point fields (one value per point) are mapped, the others (one value for
    the whole interface) are handed over as they are. Every field is optional: ``None`` means
    the solver does not tell.
    """

    #: How point data is mapped, e.g. ``"identity"`` or ``"nearest-neighbor"`` (a custom
    #: mapping: its own name).
    kind: Optional[str] = None
    #: Names of :attr:`Exchange.data` mapped point by point; the others are handed over unmapped.
    mapped: Optional[tuple[str, ...]] = None
    #: Interface points of the partner's side (mapped from) and of this side (mapped onto).
    source_points: Optional[int] = None
    target_points: Optional[int] = None


@dataclass(frozen=True)
class Exchange:
    """A coupling node's data exchange: the data of an op of the partner region, for the op after it.

    A coupling node is an action of a multi-region solver that runs right before the op it
    belongs to and hands it the partner's data.
    """

    #: Name of the partner's op whose data the node hands over (an op of the same loop).
    source: str
    #: Names of the exchanged data, e.g. ``("T", "h")``.
    data: tuple[str, ...] = ()
    #: The partner's region.
    partner: Optional[str] = None
    #: How the data is mapped onto this side's interface points (``None``: not known).
    mapping: Optional[PointMapping] = None
    #: The exchange's name, which both of its ends share (``None``: the solver does not tell).
    name: Optional[str] = None


@dataclass
class Op:
    """One node of the execution graph: an action, or a loop with children."""

    name: str
    kind: Literal["action", "loop"]
    #: Action body ``f(ctx)``, or the loop predicate ``f(ctx) -> bool``.
    func: Callable[[Any], Any]
    children: list[Op] = field(default_factory=list)
    domain: Optional[str] = None
    number: Optional[str] = None
    #: Loop predicate as shown in the diagram, e.g. ``"time < endTime"``.
    test: Optional[str] = None
    #: Guard shown on the action, e.g. ``"finalIter()"``.
    guard: Optional[str] = None
    #: Name of the optional model that injected this op through the resolver.
    injected_by: Optional[str] = None
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    doc: str = ""
    #: ``file:line`` shown in the inspector; derived from ``func`` when empty.
    source: Optional[str] = None
    id: str = ""
    #: Position in the tree, ``"1.4.2"`` (set by :func:`assign_ids`).
    pos: str = ""
    #: The region of a multi-region solver the op belongs to (``None``: the solver's own).
    region: Optional[str] = None
    #: A coupling node's data exchange (``None``: not a coupling node).
    exchange: Optional[Exchange] = None
    #: A region's coupling op (a loop whose test exchanges): one exchange per coupled side;
    #: takes the place of ``exchange``.
    exchanges: list[Exchange] = field(default_factory=list)
    #: A short tag shown on the node, e.g. ``"leader"`` (the coupling op that decides).
    tag: Optional[str] = None
    #: Runs only in the final pass of the wrapping (coupling) loops around it (NeoFOAM's
    #: ``OperationMetadata.final``); a :class:`~neofoam_ui.engine.PassControl` solver can run
    #: it in every pass instead (:meth:`~neofoam_ui.engine.Session.set_every_pass`).
    final: bool = False


@dataclass
class InitStep:
    """One node of the initialization DAG (mirrors neofoam's ``InitStep``)."""

    name: str
    func: Callable[[dict[str, Any]], Any]
    depends_on: list[str] = field(default_factory=list)
    category: Optional[str] = None
    doc: str = ""
    source: Optional[str] = None
    #: The region of a multi-region solver the step initializes (``None``: the solver's own).
    region: Optional[str] = None

    @property
    def id(self) -> str:
        return f"init:{self.name}"



@dataclass
class Event:
    """The point the engine is paused before."""

    kind: Literal["init", "exec", "test", "end"]
    node: Optional[str] = None
    depth: int = 0
    iter: Optional[int] = None
    #: Enclosing loops, outermost first, as ``(loop id, iteration)``.
    stack: tuple[tuple[str, int], ...] = ()
    #: The region of a multi-region solver whose thread the event is on (``None``: one region).
    region: Optional[str] = None
    #: A multi-region solver at rest: where each region is (the event itself is one of them).
    regions: tuple[RegionState, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "node": self.node,
            "depth": self.depth,
            "iter": self.iter,
            "stack": [list(s) for s in self.stack],
        }
        if self.region is not None:  # optional: only a multi-region solver's events carry it
            out["region"] = self.region
        if self.regions:
            out["regions"] = [r.to_dict() for r in self.regions]
        return out


@dataclass(frozen=True)
class RegionState:
    """Where one region of a multi-region solver is while the regions rest."""

    region: str
    #: ``paused`` (before :attr:`event`), ``waiting`` (for :attr:`waiting_for`'s data, in a
    #: receive, after :attr:`event`), ``running``, ``done`` or ``failed``.
    state: str
    event: Optional[Event] = None
    #: The region it waits for.
    waiting_for: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"region": self.region, "state": self.state,
                "event": None if self.event is None else self.event.to_dict(),
                "waiting_for": self.waiting_for}


def assign_ids(ops: list[Op], prefix: str = "", pos: str = "") -> None:
    """Give every op a path id (``time_loop/inner_loop/solve``) and a position (``1.4.2``).

    Repeated names get ``#2``, ``#3``.
    """
    seen: dict[str, int] = {}
    for i, op in enumerate(ops, 1):
        seen[op.name] = seen.get(op.name, 0) + 1
        suffix = f"#{seen[op.name]}" if seen[op.name] > 1 else ""
        op.id = f"{prefix}{op.name}{suffix}"
        op.pos = f"{pos}{i}"
        assign_ids(op.children, op.id + "/", op.pos + ".")


def topological_order(steps: list[InitStep]) -> list[InitStep]:
    """Kahn's algorithm, stable in declaration order; an already valid order is kept as is."""
    by_name = {s.name: s for s in steps}
    for s in steps:
        for dep in s.depends_on:
            if dep not in by_name:
                raise ValueError(f"init step {s.name!r} depends on unknown step {dep!r}")
    position = {s.name: i for i, s in enumerate(steps)}
    if all(position[d] < position[s.name] for s in steps for d in s.depends_on):
        return list(steps)
    done: set[str] = set()
    order: list[InitStep] = []
    pending = list(steps)
    while pending:
        ready = [s for s in pending if all(d in done for d in s.depends_on)]
        if not ready:
            raise ValueError("cycle in init steps: " + ", ".join(s.name for s in pending))
        for s in ready:
            order.append(s)
            done.add(s.name)
            pending.remove(s)
    return order
