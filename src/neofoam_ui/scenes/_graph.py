"""A scene's graph as plain data (no pydantic): the built-in ``Field`` scene, inputs and order."""

from __future__ import annotations

import copy
from typing import Any

__all__ = ()

_FIELD_GRAPH: dict[str, Any] = {
    "version": 1,
    "nodes": [
        {"id": "grid", "type": "grid", "props": {}},
        {"id": "surface", "type": "Surface", "props": {}},
        {"id": "display", "type": "Display", "props": {
            "field": "@field", "component": "@component", "colormap": "viridis", "range": "auto",
            "log": False, "color": [1.0, 1.0, 1.0], "line_width": 1.0, "opacity": 1.0, "bar": True}},
        {"id": "view", "type": "View", "props": {"camera": "reset", "zoom": 1.0}},
    ],
    "edges": [["grid", "surface"], ["surface", "display"], ["display", "view"]],
}


def _field_graph() -> dict[str, Any]:
    return copy.deepcopy(_FIELD_GRAPH)


def _inputs(graph: dict[str, Any]) -> dict[str, list[str]]:
    inputs: dict[str, list[str]] = {n["id"]: [] for n in graph["nodes"]}
    for source, target in graph["edges"]:
        inputs[target].append(source)
    return inputs


def _ordered(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Nodes with every input before its consumers (Kahn); a cycle raises ``ValueError`` naming a node in it."""
    inputs = {k: set(v) for k, v in _inputs(graph).items()}
    by_id = {n["id"]: n for n in graph["nodes"]}
    out: list[dict[str, Any]] = []
    ready = [n["id"] for n in graph["nodes"] if not inputs[n["id"]]]
    while ready:
        node = ready.pop(0)
        out.append(by_id[node])
        for target, sources in inputs.items():
            if node in sources:
                sources.discard(node)
                if not sources and target not in ready and by_id[target] not in out:
                    ready.append(target)
    if len(out) < len(by_id):
        stuck = next(n for n in by_id if by_id[n] not in out)
        raise ValueError(f"node {stuck!r}: its inputs form a cycle")
    return out
