"""The graph as the worker sends it (``graph`` messages)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from neofoam_ui.engine._graph import InitStep, Op

__all__ = ()


def _op_dict(op: Op) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": op.id,
        "name": op.name,
        "kind": op.kind,
        "domain": op.domain,
        "number": op.number,
        "pos": op.pos,
        "test": op.test,
        "guard": op.guard,
        "injected_by": op.injected_by,
        "reads": op.reads,
        "writes": op.writes,
        "doc": op.doc,
        "source": op.source or _source(op.func),
        "children": [_op_dict(c) for c in op.children],
    }
    if op.region is not None:  # optional: only a multi-region solver's ops carry it
        out["region"] = op.region
    if op.exchange is not None:  # optional: only coupling nodes
        out["exchange"] = {"source": op.exchange.source, "data": list(op.exchange.data),
                           "partner": op.exchange.partner}
    return out


def _init_dict(step: InitStep) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": step.id,
        "name": step.name,
        "depends_on": [f"init:{d}" for d in step.depends_on],
        "category": step.category,
        "doc": step.doc,
        "source": step.source or _source(step.func),
    }
    if step.region is not None:  # optional, as for ops
        out["region"] = step.region
    return out


def _source(func: Callable[..., Any]) -> Optional[str]:
    code = getattr(func, "__code__", None)
    if code is None:
        return None
    return f"{code.co_filename}:{code.co_firstlineno}"
