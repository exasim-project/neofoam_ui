"""The solver's activity diagram: initialization DAG and execution tree, stacked in one canvas.

* **Initialization** — the init DAG, layered by longest dependency path;
* **Execution** — the operation tree: loops are VueFlow group nodes (structured
  loop nodes) with a decision diamond at the top, children stacked below,
  and a back edge from the last child to the diamond. A *coupling loop* (a loop whose body
  holds coupling nodes, ops with an ``exchange``) is no container: a compact control node
  (title, pass, residuals, diamond) sits above one box per region, side by side, and the
  coupling nodes sit in the gaps between the boxes, outside both. A coupling node is joined
  to the boxes only by lines: a dashed one in the partner's colour from the partner's op
  whose data it takes, and one into the op it feeds. Each coupling node shares a row with
  the op it feeds, rows in execution order, so the serial order reads top to bottom.

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
#: the gap between two boxes (room for a coupling node and its lines), and the size of a
#: coupling loop's control node.
_BOX_HEAD, _BOX_PAD = 30, 14
_BOX_GAP = _AW + 120
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


def _find(op: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    """The op ``name`` in ``op``'s body, the loop's own ops first (a nested loop may hold one of
    the same name)."""
    stack = list(op["children"])
    while stack:
        node = stack.pop(0)
        if node["name"] == name:
            return node
        stack += node.get("children", [])
    return None


def _plan(op: dict[str, Any], boxes: list[dict[str, Any]], collapsed: frozenset[str]) -> list[dict[str, Any]]:
    """Measure a coupling loop's body: the boxes' columns, the gaps, one row per coupling node
    (with the op it feeds) or other op, in execution order. Sets the loop's footprint."""
    children = op["children"]
    box_of = {c["id"]: i for i, box in enumerate(boxes) for c in box["ops"]}
    for c in children:
        _measure(c, collapsed)
        c["_feeds"] = c["_source"] = None
    rows: list[list[dict[str, Any]]] = []
    row_of: dict[str, int] = {}
    for k, c in enumerate(children):
        if c["id"] in row_of:
            continue
        members = [c]
        if c.get("exchange") and k + 1 < len(children) and not children[k + 1].get("exchange"):
            members.append(children[k + 1])  # the op it feeds
            c["_feeds"] = children[k + 1]
        for m in members:
            row_of[m["id"]] = len(rows)
        rows.append(members)
    for i, box in enumerate(boxes):
        box["body"] = [c for c in box["ops"] if not c.get("exchange")]
        box["w"] = max((c["_w"] for c in box["body"]), default=_AW)
    # the gap of each coupling node: next to its own box, towards its partner's
    for c in children:
        if not c.get("exchange"):
            continue
        own = box_of[c["id"]]
        source = _find(op, c["exchange"]["source"])
        top = next((t for t in children if source is not None and (t is source or _contains(t, source))), None)
        partner = box_of.get(top["id"], own) if top else own
        c["_source"] = source
        c["_gap"] = (own if partner > own else own - 1) if partner != own else min(own, len(boxes) - 2)
    # columns
    x = _BOX_PAD
    for i, box in enumerate(boxes):
        box["x"] = x
        x += box["w"] + 2 * _BOX_PAD + (_BOX_GAP if i < len(boxes) - 1 else 0)
    width = x - _BOX_PAD + _LOOP_RIGHT
    # rows
    y = _CTRL_H + _GAP + _BOX_HEAD
    row_y = []
    for members in rows:
        row_y.append(y)
        y += max(m["_h"] for m in members) + _GAP
    for c in children:
        c["_y"] = row_y[row_of[c["id"]]]
        if c.get("exchange"):
            fed = c.get("_feeds")
            if fed is not None and fed["kind"] == "action":
                c["_y"] += (fed["_h"] - _AH) / 2
            gap = c["_gap"]
            left = boxes[gap]["x"] + boxes[gap]["w"] + _BOX_PAD
            c["_x"] = left + (_BOX_GAP - c["_w"]) / 2
        else:
            box = boxes[box_of[c["id"]]]
            c["_x"] = box["x"] + (box["w"] - c["_w"]) / 2
    for box in boxes:
        if box["body"]:
            box["y"] = min(c["_y"] for c in box["body"]) - _BOX_HEAD
            box["h"] = max(c["_y"] + c["_h"] for c in box["body"]) + _BOX_PAD - box["y"]
        else:
            box["y"], box["h"] = _CTRL_H + _GAP, _BOX_HEAD + _BOX_PAD
    op["_w"] = width
    op["_h"] = max(box["y"] + box["h"] for box in boxes) + _PAD / 2
    # the control node: above the boxes, centred over the gaps
    op["_ctrl_x"] = (boxes[0]["x"] + boxes[0]["w"] + boxes[-1]["x"]) / 2 + _BOX_PAD / 2 - _CTRL_W / 2
    return boxes


def _coupled(op: dict[str, Any], parent: Optional[str], x0: float, y0: float, dec_id: str,
             nodes: list[dict[str, Any]], edges: list[dict[str, Any]], collapsed: frozenset[str]) -> None:
    """Place a coupling loop's body at ``(x0, y0)`` of ``parent``, around its control node."""
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
    # control flow inside a box: between consecutive ops of the same box
    for a, b in zip(children, children[1:]):
        if not a.get("exchange") and not b.get("exchange") and box_of[a["id"]] == box_of[b["id"]]:
            edges.append(_edge(a["id"], b["id"]))
    edges += _exchange(op, boxes, box_of, nodes)


def _exchange(op: dict[str, Any], boxes: list[dict[str, Any]], box_of: dict[str, int],
              nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The lines of each coupling node: a dashed one in the partner's colour from the partner's
    op whose data it takes (``exchange.source``), labelled with the data, and one in its own
    region's colour into the op it feeds."""
    placed = {n["id"] for n in nodes}
    out = []
    for c in op["children"]:
        exchange = c.get("exchange")
        if not exchange:
            continue
        gap_x = c["_gap"]  # the gap left of box gap_x + 1
        source, fed = c.get("_source"), c.get("_feeds")
        if source is not None and source["id"] in placed:
            color = _REGION.color(exchange.get("partner") or source.get("region"))
            top = next((t for t in op["children"] if t is source or _contains(t, source)), source)
            from_left = box_of.get(top["id"], 0) <= gap_x
            out.append(_edge(source["id"], c["id"], type="default", label=", ".join(exchange.get("data") or ()),
                             labelShowBg=False,
                             sourceHandle="r" if from_left else "ls",
                             targetHandle="lt" if from_left else "rt",
                             style={"stroke": color, "strokeWidth": 2, "strokeDasharray": "7 5"},
                             labelStyle={"fill": color, "fontWeight": 600,
                                         "paintOrder": "stroke", "stroke": "white", "strokeWidth": 4},
                             markerEnd={"type": "arrowclosed", "color": color},
                             **{"class": "nf-couple"}))
        if fed is not None:
            color = _REGION.color(c.get("region"))
            to_left = box_of.get(fed["id"], 0) <= gap_x
            out.append(_edge(c["id"], fed["id"], type="default",
                             sourceHandle="ls" if to_left else "r",
                             targetHandle="rt" if to_left else "lt",
                             style={"stroke": color, "strokeWidth": 2},
                             markerEnd={"type": "arrowclosed", "color": color},
                             **{"class": "nf-feed"}))
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
