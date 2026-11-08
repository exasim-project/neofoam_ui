"""NeoFOAM operations as stepper :class:`~neofoam_ui.engine.Op` nodes."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from neofoam_ui.engine import Exchange, Op, PointMapping

__all__ = ()


def _is_mapping_node(op: Any) -> bool:
    """A NeoFOAM mapping node (``map_<exchange>_to_<region>``): the coupling line's mapping chip
    stands for it, so the stepper does not show it as a node of its own."""
    return type(getattr(getattr(op, "func", None), "func", None)).__name__ == "MappingNode"


def _visible(ops: list[Any]) -> list[Any]:
    """The ops the stepper shows (all but the mapping nodes)."""
    return [op for op in ops if not _is_mapping_node(op)]


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
        return Op(name, "loop", op.func, [_convert(c, injected) for c in _visible(op.sub_operations)],
                  test=test, **common)
    return Op(name, "action", op.run, guard=guard, exchange=_exchange(target), **common)


def _exchange(func: Any) -> Optional[Exchange]:
    """A NeoFOAM coupling node's exchange: the partner's op whose data it hands over.

    The node's function (a ``CouplingNode``) gives its ``side``, the ``partner`` side and the
    ``exchange`` (NeoFOAM's public read API); ``partner.op`` is the op the partner's node runs
    before, the data the fields this side receives. ``None`` for any other op.
    """
    side, partner = getattr(func, "side", None), getattr(func, "partner", None)
    source = getattr(partner, "op", None)
    if side is None or not source:
        return None
    received = getattr(getattr(side, "receives", None), "model_fields", None) or {}
    return Exchange(str(source), tuple(str(name) for name in received), getattr(partner, "region", None),
                    _mapping(getattr(func, "exchange", None), side, partner))


def _mapping(exchange: Any, side: Any, partner: Any) -> Optional[PointMapping]:
    """How a coupling node's data is mapped onto its side's points, as NeoFOAM's exchange tells.

    The kind is the exchange's ``mapping.kind``, the mapped fields the side's ``mapped`` (the
    point fields it receives; the others are handed over as they are), the points each side's
    ``points`` (``None``: the side gives none).
    """
    kind = getattr(getattr(exchange, "mapping", None), "kind", None)
    mapped = getattr(side, "mapped", None)
    mapping = PointMapping(None if kind is None else str(kind),
                           None if mapped is None else tuple(str(name) for name in mapped),
                           _points(partner), _points(side))
    return None if mapping == PointMapping() else mapping


def _points(side: Any) -> Optional[int]:
    """The number of interface points of an exchange side, if it has any."""
    points = getattr(side, "points", None)
    return int(points) if isinstance(points, int) else None


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
