"""The graph as the worker sends it (``graph`` messages)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from neofoam_ui.engine._graph import Exchange, InitStep, Op

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
    if op.tag is not None:  # optional
        out["tag"] = op.tag
    exchanges = op.exchanges or ([op.exchange] if op.exchange is not None else [])
    if exchanges:  # optional: only coupling nodes / coupling ops
        out["exchanges"] = [_exchange_dict(x) for x in exchanges]
        out["exchange"] = out["exchanges"][0]  # (the one of a coupling node, as before)
    return out


def _exchange_dict(exchange: Exchange) -> dict[str, Any]:
    out: dict[str, Any] = {"source": exchange.source, "data": list(exchange.data), "partner": exchange.partner}
    if exchange.name is not None:  # optional: pairs the two ends of one exchange
        out["name"] = exchange.name
    mapping = exchange.mapping
    if mapping is not None:  # optional: a solver that tells how it maps the data
        out["mapping"] = {
            "kind": mapping.kind,
            "mapped": None if mapping.mapped is None else list(mapping.mapped),
            "source_points": mapping.source_points,
            "target_points": mapping.target_points,
        }
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
