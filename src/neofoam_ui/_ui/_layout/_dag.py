"""The solver's activity diagram, one view at a time: the initialization or the execution.

* **Initialization** (``view="init"``) — the init DAG, layered by longest dependency path:
  the driver's steps (no region) on top, below them one box per region with its own steps,
  in the regions' order of the execution view;
* **Execution** (``view="exec"``) — the operation tree. There are no frames: a loop is its
  control node (title, pass, residuals, test diamond) above its body, with a ``[true]`` edge
  into the first op, a dashed back edge from the last op and a ``[false]`` exit on the left.
  A *coupling loop* (a loop whose body holds coupling nodes, ops with an ``exchange``) lays
  its body out as one box per region, side by side; each box is its solver's part, a
  coupling node right before the op it feeds. The boxes are joined only between their
  coupling nodes: one line with arrowheads at both ends, labelled with the exchanged data.

Node positions are absolute, except a loop's diamond, which sits in its control node. A
*collapsed* loop is drawn as a single node without its body.
"""

from __future__ import annotations

from typing import Any, Optional

from neofoam_ui._ui._layout._common import (
    _AH,
    _AW,
    _COLLAPSED_H,
    _DIAMOND,
    _GAP,
    _IH,
    _INIT_PER_ROW,
    _IW,
    _LOOP_RIGHT,
    _PAD,
    _TOP,
    _edge,
)
from neofoam_ui._ui._layout._palette import _REGION, _node_color

__all__ = ()


# -- execution activity --------------------------------------------------------------


#: Height of a region box's header (its region), its padding around its ops, the width of
#: the gap between two boxes (room for the data lines and their labels), and the size of a
#: loop's control node.
_BOX_HEAD, _BOX_PAD = 30, 14
_BOX_GAP = 200
_CTRL_W, _CTRL_H = 400, 104
#: Space left of a loop's footprint for its ``[false]`` exit edge.
_EXIT = 24


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
    """Size ``op`` (``_w``, ``_h``); an open loop's footprint is its control node over its body."""
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
        op["_w"] = max(inner_w, _CTRL_W) + _LOOP_RIGHT
        op["_ctrl_x"] = (op["_w"] - _LOOP_RIGHT - _CTRL_W) / 2
        # An empty (predicate-only) loop is just its control node.
        op["_h"] = _CTRL_H + (sum(h for _, h in sizes) + _GAP * len(sizes) if sizes else 0)
    return op["_w"], op["_h"]


def _open(op: dict[str, Any], collapsed: frozenset[str]) -> bool:
    return op["kind"] == "loop" and op["id"] not in collapsed


def _ops(
    ops: list[dict[str, Any]],
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
        x = op["_x"] = x0 + (width - op["_w"]) / 2
        is_open = _open(op, collapsed)
        w, h = (_CTRL_W, _CTRL_H) if is_open else (op["_w"], op["_h"])
        nodes.append({
            "id": op["id"],
            "type": "action" if op["kind"] == "action" else "loop",
            "position": {"x": x + op["_ctrl_x"] if is_open else x, "y": y},
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
                # an open loop's control node (its body is drawn below it)
                "control": is_open,
            },
            "width": w,
            "height": h,
            "style": {"width": f"{w}px", "height": f"{h}px"},
            "draggable": False,
            "selectable": False,
            "connectable": False,
        })
        if is_open:
            _loop_body(op, x, y, nodes, edges, collapsed)
        if prev is not None:
            edges.append(_exit(prev, op["id"], nodes, collapsed) if prev["kind"] == "loop"
                         else _edge(prev["id"], op["id"]))
        prev = op
        y += op["_h"] + _GAP
    return ops[0]["id"], ops[-1]["id"]


def _exit(loop: dict[str, Any], target: str, nodes: list[dict[str, Any]], collapsed: frozenset[str]) -> dict[str, Any]:
    """A loop's ``[false]`` edge to the placed node ``target``: from the left of an open loop's
    control node, down the left of its body, into the target's left."""
    if not _open(loop, collapsed):
        return _edge(loop["id"], target, label="[false]")
    node = next(n for n in reversed(nodes) if n["id"] == target)
    left = min(loop["_x"] + loop["_ctrl_x"], node["position"]["x"])
    return _edge(loop["id"], target, label="[false]", sourceHandle="ls", targetHandle="lt",
                 pathOptions={"offset": max(left - loop["_x"] + _EXIT, 12), "borderRadius": 8})


def _loop_body(op: dict[str, Any], x: float, y: float, nodes: list[dict[str, Any]],
               edges: list[dict[str, Any]], collapsed: frozenset[str]) -> None:
    """An open loop's diamond in its control node, and its body below the control node."""
    dec_id = f"{op['id']}::test"
    nodes.append({
        "id": dec_id, "type": "decision", "parentNode": op["id"],
        "position": {"x": _CTRL_W / 2 - _DIAMOND / 2, "y": _CTRL_H - _DIAMOND - 12},
        "data": {"loop": op["id"]}, "width": _DIAMOND, "height": _DIAMOND,
        "draggable": False, "selectable": False, "connectable": False,
    })
    if not op["children"]:
        return
    if op["_boxes"]:
        _coupled(op, x, y, nodes, edges, collapsed)
    else:
        _ops(op["children"], x, y + _CTRL_H + _GAP, op["_w"] - _LOOP_RIGHT, nodes, edges, collapsed)
    placed = {n["id"]: n for n in nodes}
    last = placed[op["children"][-1]["id"]]
    right = max(last["position"]["x"] + last["width"], x + op["_ctrl_x"] + _CTRL_W / 2 + _DIAMOND / 2)
    edges.append(_edge(dec_id, op["children"][0]["id"], label="[true]"))
    edges.append(_edge(op["children"][-1]["id"], dec_id, sourceHandle="r", targetHandle="r",
                       pathOptions={"offset": max(x + op["_w"] - _LOOP_RIGHT / 2 - right, 12), "borderRadius": 8},
                       **{"class": "nf-back", "style": {"strokeDasharray": "5 4"}}))


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


def _coupled(op: dict[str, Any], x0: float, y0: float, nodes: list[dict[str, Any]],
             edges: list[dict[str, Any]], collapsed: frozenset[str]) -> None:
    """Place a coupling loop's region boxes and their ops at ``(x0, y0)``, below its control node."""
    boxes = op["_boxes"]
    box_of: dict[str, int] = {}
    for i, box in enumerate(boxes):
        nodes.append(_box(f"{op['id']}::box{i}", ", ".join(box["regions"]), box["region"],
                          x0 + box["x"] - _BOX_PAD, y0 + box["y"], box["w"] + 2 * _BOX_PAD, box["h"]))
        for c in box["ops"]:
            box_of[c["id"]] = i
    for c in op["children"]:
        _ops([c], x0 + c["_x"], y0 + c["_y"], c["_w"], nodes, edges, collapsed)
    # each solver's own flow, inside its box (a coupling node into the op it feeds)
    for box in boxes:
        for a, b in zip(box["ops"], box["ops"][1:]):
            edges.append(_exit(a, b["id"], nodes, collapsed) if a["kind"] == "loop" else _edge(a["id"], b["id"]))
    edges += _exchange(op, box_of, nodes)


def _box(id_: str, label: str, region: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
    """A region's box: a frame with the region's name and colour, behind its nodes."""
    return {
        "id": id_, "type": "region", "position": {"x": x, "y": y},
        "data": {"label": label, "color": _REGION.color(region)},
        "width": w, "height": h, "style": {"width": f"{w}px", "height": f"{h}px"},
        "draggable": False, "selectable": False, "connectable": False,
    }


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


def _contains(op: dict[str, Any], node: dict[str, Any]) -> bool:
    return any(c is node or _contains(c, node) for c in op.get("children", []))


def _count(op: dict[str, Any]) -> int:
    """Actions inside a loop, at any depth (shown on a collapsed loop)."""
    return sum(1 if c["kind"] == "action" else _count(c) for c in op.get("children", []))


def _exec_view(ops: Optional[list[dict[str, Any]]], collapsed: frozenset[str]) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    if not ops:
        nodes.append({"id": "exec:placeholder", "type": "placeholder", "position": {"x": 0, "y": _TOP},
                      "data": {}, "width": _AW, "height": 70, "style": {"width": f"{_AW}px", "height": "70px"},
                      "draggable": False, "selectable": False})
        return {"nodes": nodes, "edges": edges}
    for op in ops:
        _measure(op, collapsed)
    width = max(op["_w"] for op in ops)
    cx = width / 2
    nodes.append({"id": "start", "type": "initial", "position": {"x": cx - 11, "y": _TOP},
                  "data": {}, "width": 22, "height": 22, "draggable": False, "selectable": False})
    y = _TOP + 22 + _GAP
    first, last = _ops(ops, 0.0, y, width, nodes, edges, collapsed)
    y += sum(op["_h"] for op in ops) + _GAP * len(ops)
    nodes.append({"id": "end", "type": "final", "position": {"x": cx - 13, "y": y},
                  "data": {}, "width": 26, "height": 26, "draggable": False, "selectable": False})
    edges.append(_edge("start", first))
    edges.append(_exit(ops[-1], "end", nodes, collapsed) if ops[-1]["kind"] == "loop" else _edge(last, "end"))
    return {"nodes": nodes, "edges": edges}


# -- initialization activity ------------------------------------------------------------


def _layers(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Rows of init steps: one per dependency layer (among ``steps``), wrapped at ``_INIT_PER_ROW``."""
    level: dict[str, int] = {}
    for s in steps:  # steps arrive topologically ordered
        level[s["id"]] = 1 + max((level[d] for d in s["depends_on"] if d in level), default=-1)
    layers: list[list[dict[str, Any]]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for s in steps:
        layers[level[s["id"]]].append(s)
    return [layer[i:i + _INIT_PER_ROW] for layer in layers for i in range(0, len(layer), _INIT_PER_ROW)]


def _init_block(steps: list[dict[str, Any]]) -> tuple[float, float, list[tuple[dict[str, Any], float, float]]]:
    """``steps`` layered: (width, height, [(step, x, y)]) relative to the block."""
    layers = _layers(steps)
    widest = max((len(layer) for layer in layers), default=0)
    width = max(widest * _IW + max(widest - 1, 0) * 24, _IW)
    placed = []
    for li, layer in enumerate(layers):
        row_w = len(layer) * _IW + (len(layer) - 1) * 24
        for si, step in enumerate(layer):
            placed.append((step, (width - row_w) / 2 + si * (_IW + 24), li * (_IH + _GAP)))
    height = len(layers) * _IH + max(len(layers) - 1, 0) * _GAP
    return width, height, placed


def _init_view(steps: list[dict[str, Any]], regions: list[str]) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    driver = [s for s in steps if not s.get("region")]
    regions = [*regions, *(s["region"] for s in steps if s.get("region") and s["region"] not in regions)]
    regions = list(dict.fromkeys(regions))
    groups = [(r, [s for s in steps if s.get("region") == r]) for r in regions]
    groups = [(r, g) for r, g in groups if g]
    top_w, top_h, top = _init_block(driver)
    blocks = [(r, *_init_block(g)) for r, g in groups]
    gap = 60
    boxes_w = sum(w + 2 * _BOX_PAD for _, w, _, _ in blocks) + gap * max(len(blocks) - 1, 0)
    width = max(top_w, boxes_w)
    y = _TOP
    for step, sx, sy in top:
        nodes.append(_init_node(step, (width - top_w) / 2 + sx, y + sy))
    y += top_h + (2 * _GAP if top else 0)
    x = (width - boxes_w) / 2
    for region, w, h, placed in blocks:
        nodes.append(_box(f"init::box:{region}", region, region, x, y, w + 2 * _BOX_PAD,
                          _BOX_HEAD + h + _BOX_PAD))
        for step, sx, sy in placed:
            nodes.append(_init_node(step, x + _BOX_PAD + sx, y + _BOX_HEAD + sy))
        x += w + 2 * _BOX_PAD + gap
    shown = {s["id"] for s in steps}
    for step in steps:
        for dep in step["depends_on"]:
            if dep in shown:
                edges.append(_edge(dep, step["id"]))
    return {"nodes": nodes, "edges": edges}


def _init_node(step: dict[str, Any], x: float, y: float) -> dict[str, Any]:
    return {
        "id": step["id"], "type": "init", "position": {"x": x, "y": y},
        "data": {"label": step["name"], "color": _node_color(step, "category")},
        "width": _IW, "height": _IH, "draggable": False, "selectable": False, "connectable": False,
    }


def _build_flow(
    init_steps: list[dict[str, Any]],
    ops: Optional[list[dict[str, Any]]],
    solver_name: str = "",
    collapsed: frozenset[str] = frozenset(),
    view: str = "exec",
) -> dict[str, list[dict[str, Any]]]:
    """One view as a VueFlow graph ``{"nodes": [...], "edges": [...]}``: ``view`` is ``"init"``
    (the initialization) or ``"exec"`` (the execution).

    ``collapsed``: ids of loops drawn without their body.
    """
    if view == "init":
        order: list[str] = []
        for op in ops or ():
            order += [r for r in _regions_in(op) if r not in order]
        return _init_view(init_steps, order)
    return _exec_view(ops, collapsed)
