"""NeoFOAM operations as stepper :class:`~neofoam_ui.engine.Op` nodes."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from neofoam_ui.engine import Exchange, Op, PointMapping

__all__ = ()


def _convert(op: Any, injected: dict[int, str], ctx: Any = None) -> Op:
    """A NeoFOAM operation (and its body) as a stepper node; ``ctx``: the context whose
    graph it is (a region's: its coupling's scheme)."""
    meta = op.metadata
    name = meta.op_name or meta.name or "operation"
    target, guard = _target(op)
    common = dict(
        domain=meta.domain_name,
        number=str(meta.operation_number) if meta.operation_number is not None else None,
        doc=meta.description or _doc(target),
        source=_source(target),
        injected_by=injected.get(id(op)),
        # runs in the final pass of its wrapping loops only (a NeoFOAM without it: never so)
        final=bool(getattr(meta, "final", False)),
    )
    if op.operation_type == "iterative":
        predicate = op.func.func
        kind = type(op.func).__name__
        exchanges, tag = [], None
        if kind == "CouplingLoopOp":  # a region's coupling op: its loop test exchanges
            exchanges = [x for x in (_exchange(op.func, port) for port in op.func.ports) if x is not None]
            scheme = _scheme(op.func, ctx)
            test = "exchange" + ("" if scheme is None else f" · {scheme}")
            tag = "leader" if getattr(op.func, "is_leader", False) else None
        elif inspect.ismethod(predicate):
            test = f"{type(predicate.__self__).__name__}()"
            if hasattr(predicate.__self__, "parallel"):
                test += " · parallel" if predicate.__self__.parallel else " · serial"
        elif inspect.isfunction(predicate):
            test = f"{predicate.__name__}()"
        else:
            test = type(predicate).__name__ + "()"
        return Op(name, "loop", op.func, [_convert(c, injected, ctx) for c in op.sub_operations],
                  test=test, exchanges=exchanges, tag=tag, **common)
    return Op(name, "action", op.run, guard=guard, **common)


def _qualified(op: Op) -> str:
    """NeoFOAM's ``Operation.name`` of a converted op: ``[<domain>.]<op_name>``."""
    return f"{op.domain}.{op.name}" if op.domain else op.name


def _region_coupling(ctx: Any) -> Any:
    """The region's ``ParticipantExchanges`` (``coupling(ctx).coupling``), or ``None``.

    Duck-typed (``ctx.models["coupling"].coupling``): ``coupling(ctx)`` checks the model's class,
    so it rejects a test's stand-in, and raises where this answers ``None``."""
    return getattr((getattr(ctx, "models", None) or {}).get("coupling"), "coupling", None)


def _scheme(coupling: Any, ctx: Any) -> Optional[str]:
    """``serial`` or ``parallel``: the scheme of the op's coupling loop (``op.loop.scheme``), else
    the region's coupling's; ``None`` if neither tells."""
    scheme = getattr(getattr(coupling, "loop", None), "scheme", None)
    if scheme is not None:
        return str(scheme)
    parallel = getattr(_region_coupling(ctx), "parallel", None)
    return None if parallel is None else ("parallel" if parallel else "serial")


def _exchange_of(coupling: Any, port: Any) -> Optional[tuple[Any, str, str]]:
    """``coupling.exchange_of(port)`` and its partner end ``(region, port)``: the end that
    sends what the port receives; ``None`` if the coupling op does not tell."""
    try:
        exchange = coupling.exchange_of(port)
        region, partner_port = exchange.partner(port.region)
    except Exception:  # noqa: BLE001 - a view must never break the run
        return None
    return exchange, str(region), str(partner_port)


def _exchange(coupling: Any, port: Any) -> Optional[Exchange]:
    """What a NeoFOAM region's coupling op hands ``port`` in its loop test (public read API).

    The port's exchange (:func:`_exchange_of`): its name (both ends share it), the partner end
    ``<region>.<port>`` (as ``coupling.partner(port)`` labels it) and the mapping.
    """
    found = _exchange_of(coupling, port)
    if found is None:
        return None
    exchange, region, partner_port = found
    # the port's data fields, of every model it receives
    received = [name for stream in getattr(port, "receives", ()) for name in stream.names]
    name = getattr(exchange, "name", None)
    return Exchange(f"{region}.{partner_port}", tuple(str(n) for n in received), region,
                    _mapping(exchange, port), None if name is None else str(name))


def _mapping(exchange: Any, port: Any) -> Optional[PointMapping]:
    """How a port's data is mapped onto its points, as NeoFOAM's exchange tells.

    The kind is the exchange's ``mapping.type``, the mapped fields the port's ``mapped`` (the
    point fields it receives; the others are handed over as they are), the points the port's
    ``points`` (``None``: it gives none). The partner's points are its own end's to tell.
    """
    kind = getattr(getattr(exchange, "mapping", None), "type", None)
    mapped = getattr(port, "mapped", None)
    mapping = PointMapping(None if kind is None else str(kind),
                           None if mapped is None else tuple(str(name) for name in mapped),
                           None, _points(port))
    return None if mapping == PointMapping() else mapping


def _points(port: Any) -> Optional[int]:
    """The number of interface points of an exchange's port, if it has any."""
    points = getattr(port, "points", None)
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
