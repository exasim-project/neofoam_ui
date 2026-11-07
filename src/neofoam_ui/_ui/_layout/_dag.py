"""The solver's activity diagram, one view at a time: the initialization or the execution.

* **Initialization** (``view="init"``) — the init DAG, layered by longest dependency path:
  the driver's steps (no region) on top, below them one box per region with its own steps,
  in the regions' order of the execution view;
* **Execution** (``view="exec"``) — the operation tree. A multi-region solver is drawn as one
  box per region, each the full solver as it would run alone: the tree *projected* onto the
  region — its own ops and every op of no region (the driver's, e.g. ``set_time_step``), in
  their loops. A shared op (a loop, a driver op) thus appears once per box; each copy has the
  id ``<op id>@@<region>`` and ``data.ref``, the shared op's id, which the UI binds to (token,
  selection, breakpoints, pass, residuals). Between the boxes only the coupling nodes of one
  coupling loop are joined: one line with arrowheads at both ends, labelled with the exchanged
  data. A solver without regions is one tree, no boxes. There are no frames: a loop is its
  compact control node (one line: title, pass, residuals; the test diamond at its right end)
  above its body, with a ``[true]`` edge
  into the first op, a dashed back edge from the last op and a ``[false]`` exit on the left.

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


#: Height of a region box's header (its region), its padding around its content, the width of
#: the gap between two boxes (room for the coupling lines and their labels), and the size of a
#: loop's control node.
_BOX_HEAD, _BOX_PAD = 30, 14
_BOX_GAP = 200
_CTRL_W, _CTRL_H = 320, 36
#: Space left of a loop's footprint for its ``[false]`` exit edge.
_EXIT = 24
#: Separates a shared op's id from the region of its copy (``time_loop@@a``).
_SEP = "@@"


def _ref(op: dict[str, Any]) -> str:
    """The id of the op ``op`` stands for: its own, or the shared op's for a region's copy."""
    return str(op.get("ref") or op["id"])


def _project(ops: list[dict[str, Any]], region: str) -> list[dict[str, Any]]:
    """``ops`` as region ``region`` runs them: its own ops and those of no region, in their loops
    (a loop with nothing left of it is dropped). Copies, with ``ref`` the original's id."""
    out = []
    for op in ops:
        if op["kind"] == "action":
            if op.get("region") in (None, region):
                out.append(_copy(op, region, []))
        else:
            children = _project(op["children"], region)
            if children or not op["children"]:
                out.append(_copy(op, region, children))
    return out


def _copy(op: dict[str, Any], region: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    plain = {k: v for k, v in op.items() if not k.startswith("_")}
    return {**plain, "id": f"{op['id']}{_SEP}{region}", "ref": op["id"], "children": children}


def _regions_in(op: dict[str, Any]) -> list[str]:
    """The regions of ``op`` and its body, in order of appearance."""
    seen = [op["region"]] if op.get("region") else []
    for child in op.get("children", []):
        seen += [r for r in _regions_in(child) if r not in seen]
    return seen


def _measure(op: dict[str, Any], collapsed: frozenset[str] = frozenset()) -> tuple[float, float]:
    """Size ``op`` (``_w``, ``_h``); an open loop's footprint is its control node over its body."""
    if op["kind"] == "action":
        op["_w"], op["_h"] = _AW, _AH
    elif _ref(op) in collapsed:
        op["_w"], op["_h"] = _AW + 2 * _PAD, _COLLAPSED_H
    else:
        sizes = [_measure(c, collapsed) for c in op["children"]]
        inner_w = max((w for w, _ in sizes), default=_AW)
        op["_w"] = max(inner_w, _CTRL_W) + _LOOP_RIGHT
        op["_ctrl_x"] = (op["_w"] - _LOOP_RIGHT - _CTRL_W) / 2
        # An empty (predicate-only) loop is just its control node.
        op["_h"] = _CTRL_H + (sum(h for _, h in sizes) + _GAP * len(sizes) if sizes else 0)
    return op["_w"], op["_h"]


def _open(op: dict[str, Any], collapsed: frozenset[str]) -> bool:
    return op["kind"] == "loop" and _ref(op) not in collapsed


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
                # the op it stands for (a region's copy of a shared op: the shared op's id)
                "ref": _ref(op),
                "number": op.get("pos") or op.get("number") or "",
                "collapsed": _ref(op) in collapsed,
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
        # a compact control node: one line, the diamond at its right end
        "position": {"x": _CTRL_W - _DIAMOND - 4, "y": (_CTRL_H - _DIAMOND) / 2},
        "data": {"loop": _ref(op)}, "width": _DIAMOND, "height": _DIAMOND,
        "draggable": False, "selectable": False, "connectable": False,
    })
    if not op["children"]:
        return
    _ops(op["children"], x, y + _CTRL_H + _GAP, op["_w"] - _LOOP_RIGHT, nodes, edges, collapsed)
    placed = {n["id"]: n for n in nodes}
    last = placed[op["children"][-1]["id"]]
    right = max(last["position"]["x"] + last["width"], x + op["_ctrl_x"] + _CTRL_W)
    edges.append(_edge(dec_id, op["children"][0]["id"], label="[true]"))
    edges.append(_edge(op["children"][-1]["id"], dec_id, sourceHandle="r", targetHandle="r",
                       pathOptions={"offset": max(x + op["_w"] - _LOOP_RIGHT / 2 - right, 12), "borderRadius": 8},
                       **{"class": "nf-back", "style": {"strokeDasharray": "5 4"}}))


def _exchange_text(exchange: Optional[dict[str, Any]]) -> Optional[str]:
    if not exchange:
        return None
    data = ", ".join(exchange.get("data") or ())
    return f"{data} ← {exchange.get('partner') or exchange['source']}"


def _box(id_: str, label: str, region: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
    """A region's box: a frame with the region's name and colour, behind its nodes."""
    return {
        "id": id_, "type": "region", "position": {"x": x, "y": y},
        "data": {"label": label, "color": _REGION.color(region)},
        "width": w, "height": h, "style": {"width": f"{w}px", "height": f"{h}px"},
        "draggable": False, "selectable": False, "connectable": False,
    }


def _count(op: dict[str, Any]) -> int:
    """Actions inside a loop, at any depth (shown on a collapsed loop)."""
    return sum(1 if c["kind"] == "action" else _count(c) for c in op.get("children", []))


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
    regions: list[str] = []
    for op in ops:
        regions += [r for r in _regions_in(op) if r not in regions]
    if not regions:
        _solver(ops, 0.0, _TOP, "", nodes, edges, collapsed)
        return {"nodes": nodes, "edges": edges}
    regions = _chain_order(ops, regions)
    rows: dict[str, int] = {}
    _rows(ops, {r: 0 for r in regions}, regions, collapsed, rows)
    _boxes_aligned(ops, regions, rows, collapsed, nodes, edges)
    edges += _between(ops, regions, {n["id"]: n for n in nodes}, nodes)
    return {"nodes": nodes, "edges": edges}


def _pairs(ops: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The region pairs that exchange data (a coupling node and its partner region)."""
    out: list[tuple[str, str]] = []
    for op in ops:
        if op.get("exchange") and op.get("region") and op["exchange"].get("partner"):
            pair = tuple(sorted((op["region"], op["exchange"]["partner"])))
            if pair not in out:
                out.append(pair)  # type: ignore[arg-type]
        out += [p for p in _pairs(op.get("children", [])) if p not in out]
    return out


def _chain_order(ops: list[dict[str, Any]], regions: list[str]) -> list[str]:
    """The boxes' order: along the chain of couplings when the regions form one (a - b - c), so
    every coupling line joins neighbouring boxes; else the order of first appearance."""
    pairs = _pairs(ops)
    links = {r: {b if a == r else a for a, b in pairs if r in (a, b)} for r in regions}
    ends = [r for r in regions if len(links[r]) == 1]
    if len(pairs) != len(regions) - 1 or any(len(n) > 2 for n in links.values()) or len(ends) != 2:
        return regions
    order = [ends[0]]  # the end that appears first
    while len(order) < len(regions):
        nxt = [r for r in links[order[-1]] if r not in order]
        if not nxt:
            return regions
        order.append(nxt[0])
    return order


def _present(op: dict[str, Any], regions: list[str]) -> set[str]:
    """The regions whose box shows ``op`` (see :func:`_project`)."""
    if op["kind"] == "action":
        return {op["region"]} if op.get("region") else set(regions)
    if not op["children"]:
        return set(regions)
    out: set[str] = set()
    for c in op["children"]:
        out |= _present(c, regions)
    return out


def _rows(ops: list[dict[str, Any]], cursor: dict[str, int], regions: list[str], collapsed: frozenset[str],
          rows: dict[str, int]) -> None:
    """A row for every op, shared by the boxes: an op takes the first row free in every box that
    shows it, so a shared op's copies line up and the coupling nodes of one exchange face each
    other. Moves ``cursor`` (the next free row of each region) past the ops."""
    for op in ops:
        present = _present(op, regions)
        row = max(cursor[r] for r in present)
        rows[op["id"]] = row
        end = row + 1
        if op["kind"] == "loop" and op["id"] not in collapsed and op["children"]:
            inner = {r: row + 1 for r in present}
            _rows(op["children"], inner, regions, collapsed, rows)
            end = max(inner.values())
        for r in present:
            cursor[r] = end


def _height(op: dict[str, Any], collapsed: frozenset[str]) -> float:
    if op["kind"] == "action":
        return _AH
    return _COLLAPSED_H if _ref(op) in collapsed else _CTRL_H


def _depth(ops: list[dict[str, Any]], collapsed: frozenset[str]) -> int:
    return max((1 + _depth(op["children"], collapsed) for op in ops
                if op["kind"] == "loop" and _ref(op) not in collapsed), default=0)


def _boxes_aligned(ops: list[dict[str, Any]], regions: list[str], rows: dict[str, int], collapsed: frozenset[str],
                   nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """One box per region, side by side: its projected tree in one column, each op on its row."""
    heights: dict[int, float] = {}

    def size(ops_: list[dict[str, Any]]) -> None:
        for op in ops_:
            heights[rows[op["id"]]] = max(heights.get(rows[op["id"]], 0.0), _height(op, collapsed))
            if op["kind"] == "loop" and op["id"] not in collapsed:
                size(op["children"])

    size(ops)
    row_y: dict[int, float] = {}
    y = _TOP + _BOX_HEAD + _BOX_PAD + 22 + _GAP
    for row in sorted(heights):
        row_y[row] = y
        y += heights[row] + _GAP
    bottom = y
    depth = _depth(ops, collapsed)
    side = _EXIT + 8 * depth  # room for the exits on the left and the back edges on the right
    column = max(_CTRL_W, _AW + 2 * _PAD)
    width = column + 2 * side + 2 * _BOX_PAD
    x = 0.0
    for region in regions:
        projected = _project(ops, region)
        nodes.append(_box(f"exec::box:{region}", region, region, x, _TOP, width, bottom + 26 + _BOX_PAD - _TOP))
        x0 = x + _BOX_PAD + side
        cx = x0 + column / 2
        start, end = f"start{_SEP}{region}", f"end{_SEP}{region}"
        nodes.append({"id": start, "type": "initial", "position": {"x": cx - 11, "y": _TOP + _BOX_HEAD + _BOX_PAD},
                      "data": {"ref": "start"}, "width": 22, "height": 22, "draggable": False, "selectable": False})
        _place(projected, cx, {ref: row_y[row] for ref, row in rows.items()}, collapsed, nodes)
        nodes.append({"id": end, "type": "final", "position": {"x": cx - 13, "y": bottom},
                      "data": {"ref": "end"}, "width": 26, "height": 26, "draggable": False, "selectable": False})
        _wire(projected, x0, column, 0, collapsed, nodes, edges)
        edges.append(_edge(start, projected[0]["id"]))
        last = projected[-1]
        edges.append(_exit(last, end, nodes, collapsed) if last["kind"] == "loop" else _edge(last["id"], end))
        x += width + _BOX_GAP


def _place(ops: list[dict[str, Any]], cx: float, y_of: dict[str, float], collapsed: frozenset[str],
           nodes: list[dict[str, Any]]) -> None:
    """Place a projected tree's nodes centred on ``cx``, each at the row (``y_of``) of the op it copies."""
    for op in ops:
        y = y_of[_ref(op)]
        is_open = _open(op, collapsed)
        w = _CTRL_W if is_open else (_AW if op["kind"] == "action" else _AW + 2 * _PAD)
        h = _height(op, collapsed)
        nodes.append({
            "id": op["id"], "type": "action" if op["kind"] == "action" else "loop",
            "position": {"x": cx - w / 2, "y": y},
            "data": {"label": op["name"], "ref": _ref(op), "number": op.get("pos") or op.get("number") or "",
                     "collapsed": _ref(op) in collapsed, "size": _count(op), "color": _node_color(op, "domain"),
                     "guard": op.get("guard"), "injected_by": op.get("injected_by"), "test": op.get("test"),
                     "exchange": _exchange_text(op.get("exchange")), "control": is_open},
            "width": w, "height": h, "style": {"width": f"{w}px", "height": f"{h}px"},
            "draggable": False, "selectable": False, "connectable": False,
        })
        if is_open:
            nodes.append({
                "id": f"{op['id']}::test", "type": "decision", "parentNode": op["id"],
                "position": {"x": _CTRL_W - _DIAMOND - 4, "y": (_CTRL_H - _DIAMOND) / 2},
                "data": {"loop": _ref(op)}, "width": _DIAMOND, "height": _DIAMOND,
                "draggable": False, "selectable": False, "connectable": False,
            })
            _place(op["children"], cx, y_of, collapsed, nodes)


def _wire(ops: list[dict[str, Any]], x0: float, column: float, depth: int, collapsed: frozenset[str],
          nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """The control flow of a placed tree: next op, a loop's [true], back edge and [false] exit;
    nested loops route a little further out (``depth``)."""
    placed = {n["id"]: n for n in nodes}
    for op in ops:
        op["_x"] = x0 - 8 * depth
        op["_ctrl_x"] = placed[op["id"]]["position"]["x"] - op["_x"]
    for a, b in zip(ops, ops[1:]):
        edges.append(_exit(a, b["id"], nodes, collapsed) if a["kind"] == "loop" else _edge(a["id"], b["id"]))
    for op in ops:
        if not _open(op, collapsed) or not op["children"]:
            continue
        dec = f"{op['id']}::test"
        last = placed[op["children"][-1]["id"]]
        right = max(last["position"]["x"] + last["width"], placed[op["id"]]["position"]["x"] + _CTRL_W)
        edges.append(_edge(dec, op["children"][0]["id"], label="[true]"))
        edges.append(_edge(op["children"][-1]["id"], dec, sourceHandle="r", targetHandle="r",
                           pathOptions={"offset": max(x0 + column + 12 + 8 * depth - right, 12), "borderRadius": 8},
                           **{"class": "nf-back", "style": {"strokeDasharray": "5 4"}}))
        _wire(op["children"], x0, column, depth + 1, collapsed, nodes, edges)


def _solver(ops: list[dict[str, Any]], x0: float, y0: float, suffix: str, nodes: list[dict[str, Any]],
            edges: list[dict[str, Any]], collapsed: frozenset[str]) -> float:
    """One solver's tree from its start to its end node at ``(x0, y0)``; returns its height."""
    for op in ops:
        _measure(op, collapsed)
    width = max(op["_w"] for op in ops)
    cx = x0 + width / 2
    start, end = f"start{suffix}", f"end{suffix}"
    nodes.append({"id": start, "type": "initial", "position": {"x": cx - 11, "y": y0},
                  "data": {"ref": "start"}, "width": 22, "height": 22, "draggable": False, "selectable": False})
    y = y0 + 22 + _GAP
    first, last = _ops(ops, x0, y, width, nodes, edges, collapsed)
    y += sum(op["_h"] for op in ops) + _GAP * len(ops)
    nodes.append({"id": end, "type": "final", "position": {"x": cx - 13, "y": y},
                  "data": {"ref": "end"}, "width": 26, "height": 26, "draggable": False, "selectable": False})
    edges.append(_edge(start, first))
    edges.append(_exit(ops[-1], end, nodes, collapsed) if ops[-1]["kind"] == "loop" else _edge(last, end))
    return y + 26 - y0


def _between(ops: list[dict[str, Any]], regions: list[str], placed: dict[str, dict[str, Any]],
             out_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The lines between the region boxes: per coupling loop, one between each pair of coupling
    nodes (their regions' copies), arrowheads at both ends. Each line gets a key (``cl-<n>``,
    its edge class) and a label node in its middle (``exlabel``) with the exchanged data, which
    the UI fills with the values while the run steps (``ex_vals``) and highlights (``ex_hot``)."""
    out: list[dict[str, Any]] = []
    for op in ops:
        if op["kind"] != "loop":
            continue
        out += _between(op["children"], regions, placed, out_nodes)
        couplers = [c for c in op["children"] if c.get("exchange") and c.get("region") in regions]
        done: set[str] = set()
        for c in couplers:
            partner = next((d for d in couplers if d is not c and d.get("region") == c["exchange"].get("partner")
                            and d["exchange"].get("partner") == c.get("region")), None)
            if partner is None or partner["id"] in done:
                continue
            done.add(c["id"])
            left, right = sorted((c, partner), key=lambda n: regions.index(n["region"]))
            source, target = f"{left['id']}{_SEP}{left['region']}", f"{right['id']}{_SEP}{right['region']}"
            if source not in placed or target not in placed:
                continue
            key = str(sum(1 for n in out_nodes if n["type"] == "exlabel"))
            data = [*c["exchange"].get("data", ()), *(n for n in partner["exchange"].get("data", ())
                                                      if n not in c["exchange"].get("data", ()))]
            out.append(_line(source, target, key))
            s, tgt = placed[source], placed[target]
            x0, x1 = s["position"]["x"] + s["width"], tgt["position"]["x"]
            out_nodes.append({
                "id": f"exlabel:{key}", "type": "exlabel",
                "position": {"x": x0 + 8, "y": s["position"]["y"] - 30},
                "data": {"key": key, "data": ", ".join(data), "left": left["region"], "right": right["region"],
                         "nodes": [left["id"], right["id"]]},
                "width": max(x1 - x0 - 16, 40), "height": 28,
                "style": {"width": f"{max(x1 - x0 - 16, 40)}px", "height": "28px"},
                "draggable": False, "selectable": False, "connectable": False,
            })
    return out


def _line(source: str, target: str, key: str) -> dict[str, Any]:
    """A coupling line between two coupling nodes, arrowheads at both ends (class ``cl-<key>``)."""
    color = "#6741d9"
    arrow = {"type": "arrowclosed", "color": color}
    return _edge(source, target, type="straight",
                 sourceHandle="r", targetHandle="lt",
                 style={"stroke": color, "strokeWidth": 2, "strokeDasharray": "7 5"},
                 markerStart=arrow, markerEnd=arrow, **{"class": f"nf-couple cl-{key}"})


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
    # a region not initialized yet (known from an earlier run) is an empty box
    groups = [(r, [s for s in steps if s.get("region") == r]) for r in regions]
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
        nodes.append(_box(f"init::box:{region}", region if placed else f"{region} · not initialized yet",
                          region, x, y, w + 2 * _BOX_PAD, _BOX_HEAD + max(h, _IH) + _BOX_PAD))
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
        "data": {"label": step["name"], "ref": step["id"], "color": _node_color(step, "category")},
        "width": _IW, "height": _IH, "draggable": False, "selectable": False, "connectable": False,
    }


def _build_flow(
    init_steps: list[dict[str, Any]],
    ops: Optional[list[dict[str, Any]]],
    solver_name: str = "",
    collapsed: frozenset[str] = frozenset(),
    view: str = "exec",
    regions: tuple[str, ...] = (),
) -> dict[str, list[dict[str, Any]]]:
    """One view as a VueFlow graph ``{"nodes": [...], "edges": [...]}``: ``view`` is ``"init"``
    (the initialization) or ``"exec"`` (the execution).

    ``collapsed``: ids of loops drawn without their body. ``regions``: regions known before
    they initialize (e.g. from an earlier run), boxes of the init view from the start.
    """
    if view == "init":
        order: list[str] = list(regions)
        for op in ops or ():
            order += [r for r in _regions_in(op) if r not in order]
        return _init_view(init_steps, order)
    return _exec_view(ops, collapsed)
