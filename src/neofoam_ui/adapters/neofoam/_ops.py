"""NeoFOAM operations as stepper :class:`~neofoam_ui.engine.Op` nodes."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from neofoam_ui.engine import Op

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
    )
    if op.operation_type == "iterative":
        predicate = op.func.func
        test = (type(predicate).__name__ + "()") if not inspect.isfunction(predicate) \
            else f"{predicate.__name__}()"
        return Op(name, "loop", op.func, [_convert(c, injected) for c in op.sub_operations],
                  test=test, **common)
    return Op(name, "action", op.run, guard=guard, **common)


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
