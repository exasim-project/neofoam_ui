"""NeoFOAM operations as stepper :class:`~neofoam_ui.engine.Op` nodes."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from neofoam_ui.engine import Exchange, Op, PointMapping

__all__ = ()


def _convert(op: Any, injected: dict[int, str]) -> Op:
    meta = op.metadata
    name = meta.op_name or meta.name or "operation"
    target, guard = _target(op)
    common = dict(
        domain=meta.domain_name,
        number=str(meta.operation_number) if meta.operation_number is not None else None,
        doc=meta.description or _doc(target),
        source=_source(target),
        injected_by=injected.get(id(op)),
        region=getattr(meta, "region", None),
    )
    if op.operation_type == "iterative":
        predicate = op.func.func
        if inspect.ismethod(predicate):  # e.g. a CouplingOp's own pass predicate
            test = f"{type(predicate.__self__).__name__}()"
            if hasattr(predicate.__self__, "parallel"):
                test += " · parallel" if predicate.__self__.parallel else " · serial"
        elif inspect.isfunction(predicate):
            test = f"{predicate.__name__}()"
        else:
            test = type(predicate).__name__ + "()"
        return Op(name, "loop", op.func, [_convert(c, injected) for c in op.sub_operations],
                  test=test, **common)
    return Op(name, "action", op.run, guard=guard, exchange=_exchange(target), **common)


def _exchange(func: Any) -> Optional[Exchange]:
    """A NeoFOAM coupling node's exchange: the partner's op whose data it hands over.

    The node's function (a ``CouplingNode``) names its coupling and the index of its side;
    the coupling's other side is the partner, and ``op`` the op its own node runs before.
    ``None`` for any other op, and for a coupling that lacks its partner.
    """
    sides = getattr(getattr(func, "coupling", None), "sides", None)
    index = getattr(func, "index", None)
    if not isinstance(sides, list) or len(sides) != 2 or index not in (0, 1):
        return None
    side, partner = sides[index], sides[1 - index]
    source = getattr(partner, "op", None)
    if not source:
        return None
    kinds = getattr(side, "kinds", {}) or {}
    return Exchange(str(source), tuple(str(name) for name in kinds), getattr(partner, "region", None),
                    _mapping(func.coupling, side, partner, kinds))


#: The mapping kind of NeoFOAM's own transfers (``CouplingOp.transfer``), by their name.
_TRANSFERS = {"identity_transfer": "identity", "MappedTransfer": "nearest-neighbor"}


def _mapping(coupling: Any, side: Any, partner: Any, kinds: dict[str, str]) -> Optional[PointMapping]:
    """How a coupling node maps its partner's data onto its side's points, as far as NeoFOAM tells.

    The kind from the coupling's ``transfer`` (a transfer of its own: its name), the mapped
    fields from the kinds (``Array`` fields go through the transfer, the others are handed over
    as they are), the points from each side's ``coordinates`` (``None``: the side gives none).
    """
    transfer = getattr(coupling, "transfer", None)
    kind = None
    if transfer is not None:
        name = getattr(transfer, "__name__", None) or type(transfer).__name__
        kind = _TRANSFERS.get(name, name)
    mapped = tuple(str(name) for name, k in kinds.items() if k == "Array") if kinds else None
    mapping = PointMapping(kind, mapped, _points(partner), _points(side))
    return None if mapping == PointMapping() else mapping


def _points(attached: Any) -> Optional[int]:
    """The number of interface points of a coupled side (rows of its ``coordinates``), if it has any."""
    coordinates = getattr(getattr(attached, "side", None), "coordinates", None)
    shape = getattr(coordinates, "shape", None)
    if shape:
        return int(shape[0])
    try:
        return len(coordinates) if coordinates is not None else None
    except TypeError:
        return None


def _turb_corr_gate(func: Any) -> Optional[tuple[Any, str]]:
    """incompressibleFluid's ``_under_turb_corr``: a closure ``gated`` over the original op."""
    if getattr(func, "__name__", "") == "gated" and func.__closure__:
        for cell in func.__closure__:
            inner = cell.cell_contents
            if hasattr(inner, "metadata") and hasattr(inner, "func"):
                return inner, "turbCorr()"
    return None


#: Solver gates around an op's function: ``gate(func) -> (wrapped op, guard text)`` or ``None``.
#: A solver with its own gate adds one here.
_GATES: list[Callable[[Any], Optional[tuple[Any, str]]]] = [_turb_corr_gate]


def _target(op: Any) -> tuple[Callable[..., Any], Optional[str]]:
    """The user function behind an op, and a guard if a solver gate wraps it."""
    func = inspect.unwrap(getattr(op.func, "func", op.func))
    for gate in _GATES:
        gated = gate(func)
        if gated is not None:
            return _target(gated[0])[0], gated[1]
    return func, None


def _doc(func: Any) -> str:
    doc = inspect.getdoc(func) or ""
    return doc.split("\n\n", 1)[0]


def _source(func: Any) -> Optional[str]:
    try:
        target = inspect.unwrap(func)
        target = getattr(target, "__func__", target)
        if not (inspect.isfunction(target) or inspect.ismethod(target)):
            target = type(target).__call__
        return f"{inspect.getsourcefile(target)}:{inspect.getsourcelines(target)[1]}"
    except (TypeError, OSError):
        return None
