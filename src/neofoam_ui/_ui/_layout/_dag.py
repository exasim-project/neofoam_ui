"""The solver's activity diagram: initialization DAG and execution tree, stacked in one canvas.

* **Initialization** — the init DAG, layered by longest dependency path;
* **Execution** — the operation tree: loops are VueFlow group nodes (structured
  loop nodes) with a decision diamond at the top, children stacked below,
  and a back edge from the last child to the diamond.

Positions of children are relative to their parent group (VueFlow ``parentNode``).
A *collapsed* loop is drawn as a single node without its body.
"""

from __future__ import annotations

from typing import Any, Optional

from neofoam_ui._ui._layout._common import (
    _ACT_HEAD,
    _AH,
    _AW,
    _COLLAPSED_H,
    _DIAMOND,
    _GAP,
    _IH,
    _INIT_PER_ROW,
    _IW,
    _LOOP_HEAD,
    _LOOP_RIGHT,
    _PAD,
    _TOP,
    _activity,
    _edge,
)
from neofoam_ui._ui._layout._palette import _DOMAIN

__all__ = ()


# -- execution activity --------------------------------------------------------------


def _measure(op: dict[str, Any], collapsed: frozenset[str] = frozenset()) -> tuple[float, float]:
    if op["kind"] == "action":
        op["_w"], op["_h"] = _AW, _AH
    elif op["id"] in collapsed:
        op["_w"], op["_h"] = _AW + 2 * _PAD, _COLLAPSED_H
    else:
        sizes = [_measure(c, collapsed) for c in op["children"]]
        inner_w = max((w for w, _ in sizes), default=_AW)
        op["_w"] = inner_w + 2 * _PAD + _LOOP_RIGHT
        # An empty (predicate-only) loop is just its head.
        op["_h"] = _LOOP_HEAD + sum(h for _, h in sizes) + _GAP * max(len(sizes) - 1, 0) + _PAD
    return op["_w"], op["_h"]


def _ops(
    ops: list[dict[str, Any]],
    parent: Optional[str],
    x0: float,
    y0: float,
    width: float,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    collapsed: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Place ``ops`` stacked in a column of ``width``; return (first id, last id)."""
    y = y0
    prev: Optional[dict[str, Any]] = None
    for op in ops:
        x = x0 + (width - op["_w"]) / 2
        node: dict[str, Any] = {
            "id": op["id"],
            "type": "action" if op["kind"] == "action" else "loop",
            "position": {"x": x, "y": y},
            "data": {
                "label": op["name"],
                "number": op.get("pos") or op.get("number") or "",
                "collapsed": op["id"] in collapsed,
                "size": _count(op),
                "color": _DOMAIN.color(op.get("domain")),
                "guard": op.get("guard"),
                "injected_by": op.get("injected_by"),
                "test": op.get("test"),
            },
            "width": op["_w"],
            "height": op["_h"],
            "style": {"width": f"{op['_w']}px", "height": f"{op['_h']}px"},
            "draggable": False,
            "selectable": False,
            "connectable": False,
        }
        if parent:
            node["parentNode"] = parent
        nodes.append(node)
        if op["kind"] == "loop" and op["id"] not in collapsed:
            inner_w = op["_w"] - 2 * _PAD - _LOOP_RIGHT
            dec_id = f"{op['id']}::test"
            nodes.append(
                {
                    "id": dec_id,
                    "type": "decision",
                    "parentNode": op["id"],
                    "position": {"x": _PAD + inner_w / 2 - _DIAMOND / 2, "y": _LOOP_HEAD - _DIAMOND - 22},
                    "data": {"loop": op["id"]},
                    "width": _DIAMOND,
                    "height": _DIAMOND,
                    "draggable": False,
                    "selectable": False,
                    "connectable": False,
                }
            )
            if op["children"]:
                first, last = _ops(op["children"], op["id"], _PAD, _LOOP_HEAD, inner_w, nodes, edges,
                                   collapsed)
                edges.append(_edge(dec_id, first, label="[true]"))
                edges.append(
                    _edge(last, dec_id, sourceHandle="r", targetHandle="r",
                          **{"class": "nf-back", "style": {"strokeDasharray": "5 4"}})
                )
        if prev is not None:
            label = "[false]" if prev["kind"] == "loop" else None
            edges.append(_edge(prev["id"], op["id"], **({"label": label} if label else {})))
        prev = op
        y += op["_h"] + _GAP
    return ops[0]["id"], ops[-1]["id"]


def _count(op: dict[str, Any]) -> int:
    """Actions inside a loop, at any depth (shown on a collapsed loop)."""
    return sum(1 if c["kind"] == "action" else _count(c) for c in op.get("children", []))


# -- initialization activity ------------------------------------------------------------


def _layers(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Rows of init steps: one per dependency layer, wrapped at ``_INIT_PER_ROW``."""
    level: dict[str, int] = {}
    for s in steps:  # steps arrive topologically ordered
        level[s["id"]] = 1 + max((level[d] for d in s["depends_on"]), default=-1)
    layers: list[list[dict[str, Any]]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for s in steps:
        layers[level[s["id"]]].append(s)
    return [layer[i:i + _INIT_PER_ROW] for layer in layers for i in range(0, len(layer), _INIT_PER_ROW)]


def _build_flow(
    init_steps: list[dict[str, Any]],
    ops: Optional[list[dict[str, Any]]],
    solver_name: str = "",
    collapsed: frozenset[str] = frozenset(),
) -> dict[str, list[dict[str, Any]]]:
    """Both activities as one VueFlow graph ``{"nodes": [...], "edges": [...]}``.

    ``collapsed``: ids of loops drawn without their body.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Execution activity first, to know the canvas width.
    if ops:
        for op in ops:
            _measure(op, collapsed)
        exec_w = max(op["_w"] for op in ops) + 2 * _PAD
        exec_h = _ACT_HEAD + sum(op["_h"] for op in ops) + _GAP * (len(ops) - 1) + _PAD
    else:
        exec_w, exec_h = _AW + 2 * _PAD, _ACT_HEAD + 70 + _PAD

    layers = _layers(init_steps)
    widest = max((len(layer) for layer in layers), default=1)
    init_w = max(widest * _IW + (widest - 1) * 24 + 2 * _PAD, 2 * _IW)
    init_h = _ACT_HEAD + len(layers) * _IH + max(len(layers) - 1, 0) * _GAP + _PAD
    width = max(init_w, exec_w)
    cx = width / 2

    nodes.append({"id": "start", "type": "initial", "position": {"x": cx - 11, "y": _TOP},
                  "data": {}, "width": 22, "height": 22, "draggable": False, "selectable": False})
    y = _TOP + 22 + _GAP

    nodes.append(_activity("act:init", "Initialization", cx - init_w / 2, y, init_w, init_h))
    edges.append(_edge("start", "act:init"))
    for li, layer in enumerate(layers):
        row_w = len(layer) * _IW + (len(layer) - 1) * 24
        for si, step in enumerate(layer):
            nodes.append(
                {
                    "id": step["id"],
                    "type": "init",
                    "parentNode": "act:init",
                    "position": {"x": (init_w - row_w) / 2 + si * (_IW + 24),
                                 "y": _ACT_HEAD + li * (_IH + _GAP)},
                    "data": {"label": step["name"], "color": _DOMAIN.color(step.get("category"))},
                    "width": _IW, "height": _IH,
                    "draggable": False, "selectable": False, "connectable": False,
                }
            )
            for dep in step["depends_on"]:
                edges.append(_edge(dep, step["id"]))
    y += init_h + _GAP

    title = f"Execution · {solver_name}" if solver_name else "Execution"
    nodes.append(_activity("act:exec", title, cx - exec_w / 2, y, exec_w, exec_h))
    edges.append(_edge("act:init", "act:exec"))
    if ops:
        first, last = _ops(ops, "act:exec", _PAD, _ACT_HEAD, exec_w - 2 * _PAD, nodes, edges, collapsed)
        last_kind = ops[-1]["kind"]
    else:
        nodes.append({"id": "exec:placeholder", "type": "placeholder", "parentNode": "act:exec",
                      "position": {"x": _PAD, "y": _ACT_HEAD}, "data": {},
                      "width": _AW, "height": 70, "style": {"width": f"{_AW}px", "height": "70px"},
                      "draggable": False, "selectable": False})
        last_kind = None
    y += exec_h + _GAP

    nodes.append({"id": "end", "type": "final", "position": {"x": cx - 13, "y": y},
                  "data": {}, "width": 26, "height": 26, "draggable": False, "selectable": False})
    edges.append(_edge("act:exec", "end", **({"label": "[false]"} if last_kind == "loop" else {})))
    return {"nodes": nodes, "edges": edges}
