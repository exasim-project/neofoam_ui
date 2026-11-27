"""A scene's graph as plain data (no pydantic): the built-in ``Field`` scene, inputs, order, the View's cells."""

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
        {"id": "view", "type": "View", "props": {"camera": "reset", "zoom": 1.0, "layout": "1", "cameras": "linked",
                                                 "cell_cameras": {}}},
    ],
    "edges": [["grid", "surface"], ["surface", "display"], ["display", "view"]],
}


#: The View's layouts: rows, columns and the cells (its input ports), row by row.
_LAYOUTS: dict[str, tuple[int, int, tuple[str, ...]]] = {
    "1": (1, 1, ("displays",)), "1x2": (1, 2, ("left", "right")), "2x1": (2, 1, ("top", "bottom")),
    "2x2": (2, 2, ("tl", "tr", "bl", "br"))}


def _field_graph() -> dict[str, Any]:
    return copy.deepcopy(_FIELD_GRAPH)


def _inputs(graph: dict[str, Any]) -> dict[str, list[str]]:
    inputs: dict[str, list[str]] = {n["id"]: [] for n in graph["nodes"]}
    for source, target, *_ in graph["edges"]:
        inputs[target].append(source)
    return inputs


def _view_props(graph: dict[str, Any]) -> dict[str, Any]:
    return next(n.get("props") or {} for n in graph["nodes"] if n["type"] == "View")


def _cell_of(graph: dict[str, Any]) -> dict[str, str]:
    """Each Display linked to the View: its cell (an edge without a port: the first cell)."""
    first = _LAYOUTS[_view_props(graph).get("layout", "1")][2][0]
    views = {n["id"] for n in graph["nodes"] if n["type"] == "View"}
    return {edge[0]: edge[2] if len(edge) > 2 else first for edge in graph["edges"] if edge[1] in views}


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
