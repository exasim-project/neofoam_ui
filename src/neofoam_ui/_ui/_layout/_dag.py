"""The solver's activity diagram: initialization DAG and execution tree, stacked in one canvas.

* **Initialization** — the init DAG, layered by longest dependency path;
* **Execution** — the operation tree: loops are VueFlow group nodes (structured
  loop nodes) with a decision diamond at the top, children stacked below,
  and a back edge from the last child to the diamond. A *coupling loop* (a loop whose body
  holds coupling nodes, ops with an ``exchange``) is no container: a compact control node
  (title, pass, residuals, diamond) sits above one box per region, side by side. Each box
  is its solver's part of the body, a coupling node right before the op it feeds. The boxes
  are joined only between their coupling nodes: one line with arrowheads at both ends,
  labelled with the exchanged data; the op below a coupling node gets the data from it.

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
from neofoam_ui._ui._layout._palette import _REGION, _node_color

__all__ = ()


# -- execution activity --------------------------------------------------------------


#: Height of a region box's header (its region), its padding around its ops, the width of
#: the gap between two boxes (room for the data lines and their labels), and the size of a
#: coupling loop's control node.
_BOX_HEAD, _BOX_PAD = 30, 14
_BOX_GAP = 200
_CTRL_W, _CTRL_H = 400, 104


def _regions_in(op: dict[str, Any]) -> list[str]:
    """The regions of ``op`` and its body, in order of appearance."""
    seen = [op["region"]] if op.get("region") else []
    for child in op.get("children", []):
        seen += [r for r in _regions_in(child) if r not in seen]
    return seen


def _boxes(op: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """A coupling loop's body as region boxes, one per region, in order of first appearance.

    A coupling loop holds coupling nodes (ops with an ``exchange``). An op goes to its region's
    box; a nested loop (no region of its own) to the box it continues, or else to the box of
    its first region. ``None``: not a coupling loop, or a body op of no region at all (then the
    loop is drawn as usual).
    """
    if not any(child.get("exchange") for child in op.get("children", [])):
        return None
    boxes: list[dict[str, Any]] = []
    for child in op["children"]:
        regions = _regions_in(child)
        if not regions:
            return None
        if boxes and boxes[-1]["region"] in regions and not child.get("region"):
            box = boxes[-1]
        else:
            key = child.get("region") or regions[0]
            box = next((b for b in boxes if b["region"] == key), None)
            if box is None:
                box = {"region": key, "regions": [], "ops": []}
                boxes.append(box)
        box["ops"].append(child)
        box["regions"] += [r for r in regions if r not in box["regions"]]
    return boxes if len(boxes) > 1 else None


def _measure(op: dict[str, Any], collapsed: frozenset[str] = frozenset()) -> tuple[float, float]:
    op["_boxes"] = None
    if op["kind"] == "action":
        op["_w"], op["_h"] = _AW, _AH
    elif op["id"] in collapsed:
        op["_w"], op["_h"] = _AW + 2 * _PAD, _COLLAPSED_H
    elif (boxes := _boxes(op)) is not None:
        op["_boxes"] = _plan(op, boxes, collapsed)
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
        coupled = op["kind"] == "loop" and op["id"] not in collapsed and op["_boxes"]
        w, h = (_CTRL_W, _CTRL_H) if coupled else (op["_w"], op["_h"])
        node: dict[str, Any] = {
            "id": op["id"],
            "type": "action" if op["kind"] == "action" else "loop",
            "position": {"x": x, "y": y},
            "data": {
                "label": op["name"],
                "number": op.get("pos") or op.get("number") or "",
                "collapsed": op["id"] in collapsed,
                "size": _count(op),
                "color": _node_color(op, "domain"),
                "guard": op.get("guard"),
                "injected_by": op.get("injected_by"),
                "test": op.get("test"),
                # a coupling node: the partner it takes data from, e.g. "x ← b"
                "exchange": _exchange_text(op.get("exchange")),
                # a coupling loop's control node (its body is drawn around it, see _coupled)
                "control": bool(coupled),
            },
            "width": w,
            "height": h,
            "style": {"width": f"{w}px", "height": f"{h}px"},
            "draggable": False,
            "selectable": False,
            "connectable": False,
        }
        if coupled:
            node["position"]["x"] = x + op["_ctrl_x"]
        if parent:
            node["parentNode"] = parent
        nodes.append(node)
        if op["kind"] == "loop" and op["id"] not in collapsed:
            inner_w = (_CTRL_W if coupled else op["_w"]) - 2 * _PAD - _LOOP_RIGHT
            dec_id = f"{op['id']}::test"
            nodes.append(
                {
                    "id": dec_id,
                    "type": "decision",
                    "parentNode": op["id"],
                    "position": {"x": _PAD + inner_w / 2 - _DIAMOND / 2 + (_LOOP_RIGHT / 2 if coupled else 0),
                                 "y": (_CTRL_H - _DIAMOND - 12) if coupled else _LOOP_HEAD - _DIAMOND - 22},
                    "data": {"loop": op["id"]},
                    "width": _DIAMOND,
                    "height": _DIAMOND,
                    "draggable": False,
                    "selectable": False,
                    "connectable": False,
                }
            )
            if coupled:
                _coupled(op, parent, x, y, dec_id, nodes, edges, collapsed)
            elif op["children"]:
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


def _exchange_text(exchange: Optional[dict[str, Any]]) -> Optional[str]:
    if not exchange:
        return None
    data = ", ".join(exchange.get("data") or ())
    return f"{data} ← {exchange.get('partner') or exchange['source']}"


def _plan(op: dict[str, Any], boxes: list[dict[str, Any]], collapsed: frozenset[str]) -> list[dict[str, Any]]:
    """Measure a coupling loop's body: one column per box, its ops stacked. Sets the loop's
    footprint."""
    children = op["children"]
    for c in children:
        _measure(c, collapsed)
    x = _BOX_PAD
    for i, box in enumerate(boxes):
        box["w"] = max(c["_w"] for c in box["ops"])
        box["x"] = x
        x += box["w"] + 2 * _BOX_PAD + (_BOX_GAP if i < len(boxes) - 1 else 0)
    width = x - _BOX_PAD + _LOOP_RIGHT
    # every box from the same top, its ops stacked: the coupling nodes, each first in its box,
    # face each other across the gap (the sequence numbers show the order)
    for box in boxes:
        y = _CTRL_H + _GAP + _BOX_HEAD
        for c in box["ops"]:
            c["_x"], c["_y"] = box["x"] + (box["w"] - c["_w"]) / 2, y
            y += c["_h"] + _GAP
    for box in boxes:
        box["y"] = min(c["_y"] for c in box["ops"]) - _BOX_HEAD
        box["h"] = max(c["_y"] + c["_h"] for c in box["ops"]) + _BOX_PAD - box["y"]
    op["_w"] = width
    op["_h"] = max(box["y"] + box["h"] for box in boxes) + _PAD / 2
    # the control node: above the boxes, centred over the gaps
    op["_ctrl_x"] = (boxes[0]["x"] + boxes[0]["w"] + boxes[-1]["x"]) / 2 + _BOX_PAD / 2 - _CTRL_W / 2
    return boxes


def _coupled(op: dict[str, Any], parent: Optional[str], x0: float, y0: float, dec_id: str,
             nodes: list[dict[str, Any]], edges: list[dict[str, Any]], collapsed: frozenset[str]) -> None:
    """Place a coupling loop's body at ``(x0, y0)`` of ``parent``, below its control node."""
    boxes = op["_boxes"]
    box_of: dict[str, int] = {}
    for i, box in enumerate(boxes):
        color = _REGION.color(box["region"])
        w, h = box["w"] + 2 * _BOX_PAD, box["h"]
        node = {
            "id": f"{op['id']}::box{i}", "type": "region",
            "position": {"x": x0 + box["x"] - _BOX_PAD, "y": y0 + box["y"]},
            "data": {"label": ", ".join(box["regions"]), "color": color},
            "width": w, "height": h, "style": {"width": f"{w}px", "height": f"{h}px"},
            "draggable": False, "selectable": False, "connectable": False,
        }
        if parent:
            node["parentNode"] = parent
        nodes.append(node)
        for c in box["ops"]:
            box_of[c["id"]] = i
    children = op["children"]
    for c in children:
        _ops([c], parent, x0 + c["_x"], y0 + c["_y"], c["_w"], nodes, edges, collapsed)
    edges.append(_edge(dec_id, children[0]["id"], label="[true]"))
    edges.append(_edge(children[-1]["id"], dec_id, sourceHandle="r", targetHandle="r",
                       **{"class": "nf-back", "style": {"strokeDasharray": "5 4"}}))
    # each solver's own flow, inside its box (a coupling node into the op it feeds)
    for box in boxes:
        for a, b in zip(box["ops"], box["ops"][1:]):
            edges.append(_edge(a["id"], b["id"]))
    edges += _exchange(op, box_of, nodes)


def _exchange(op: dict[str, Any], box_of: dict[str, int], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line between the two coupling nodes of each exchange, arrowheads at both ends,
    labelled with the exchanged data.

    A coupling node's partner node is the coupling node of its partner region that takes
    data from this node's region.
    """
    placed = {n["id"] for n in nodes}
    couplers = [c for c in op["children"] if c.get("exchange") and c["id"] in placed]
    out, done = [], set()
    for c in couplers:
        partner = next((d for d in couplers if d is not c and d.get("region") == c["exchange"].get("partner")
                        and d["exchange"].get("partner") == c.get("region")), None)
        if partner is None or partner["id"] in done:
            continue
        done.add(c["id"])
        left, right = (c, partner) if box_of.get(c["id"], 0) <= box_of.get(partner["id"], 0) else (partner, c)
        names = [*c["exchange"].get("data", ()), *(n for n in partner["exchange"].get("data", ())
                                                    if n not in c["exchange"].get("data", ()))]
        color = "#6741d9"
        arrow = {"type": "arrowclosed", "color": color}
        out.append(_edge(left["id"], right["id"], type="straight", label=", ".join(names),
                         sourceHandle="r", targetHandle="lt", labelShowBg=False,
                         style={"stroke": color, "strokeWidth": 2, "strokeDasharray": "7 5"},
                         labelStyle={"fill": color, "fontWeight": 700, "fontSize": "14px",
                                     "paintOrder": "stroke", "stroke": "white", "strokeWidth": 5},
                         markerStart=arrow, markerEnd=arrow, **{"class": "nf-couple"}))
    return out


def _contains(op: dict[str, Any], node: dict[str, Any]) -> bool:
    return any(c is node or _contains(c, node) for c in op.get("children", []))


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
                    "data": {"label": step["name"], "color": _node_color(step, "category")},
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
