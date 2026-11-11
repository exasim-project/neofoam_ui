"""The solver's activity diagram, one view at a time: the initialization or the execution.

* **Initialization** (``view="init"``) — the init DAG, layered by longest dependency path:
  the driver's steps (no region) on top, below them one box per region with its own steps,
  in the regions' order of the execution view; once the execution graph is known, every
  interface's mapping node between its two boxes, on the coupling line that joins them;
* **Execution** (``view="exec"``) — the operation tree. A multi-region solver (every op
  carries its region) is drawn as one box per region, each that region's own graph, as its
  solver runs alone on its own thread. Between the boxes only the coupling ops are joined:
  one line per exchange, arrowheads at both ends, through the exchange's *mapping node* in
  the gap (how the data goes from one port's interface points onto the other's, and what
  each port sends). A coupling op is a *coupling action*, or a region's *coupling loop*: a
  loop whose test exchanges (NeoFOAM's ``<region>.couple``), with one exchange per coupled
  port (``exchanges``), so it may sit on several lines. The boxes share rows: the k-th
  coupling op of every box sits on one row, so the lines are horizontal. A solver without regions
  is one tree, no boxes. There are no frames: a loop is its compact control node (one line:
  title, pass, residuals; the test diamond at its right end) above its body, with a
  ``[true]`` edge into the first op, a dashed back edge from the last op and a ``[false]``
  exit on the left.

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
#: the gap between two boxes (room for the coupling lines and their mapping nodes), and the
#: size of a loop's control node.
_BOX_HEAD, _BOX_PAD = 30, 14
_BOX_GAP = 200
_CTRL_W, _CTRL_H = 320, 36
#: Space left of a loop's footprint for its ``[false]`` exit edge.
_EXIT = 24


def _ref(op: dict[str, Any]) -> str:
    """The id the UI binds a node to (token, selection, breakpoints, pass): the op's own."""
    return str(op["id"])


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
                # a coupling action: the partner it takes data from, e.g. "x ← b"
                "exchange": _exchange_text(op), "tag": op.get("tag"),
                # runs in the final pass of its wrapping loops only (a toggle: every pass)
                "final": bool(op.get("final")),
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


def _exchanges(op: dict[str, Any]) -> list[dict[str, Any]]:
    """What a coupling action or coupling loop receives: one exchange per coupled port."""
    return op.get("exchanges") or ([op["exchange"]] if op.get("exchange") else [])


def _exchange_text(op: dict[str, Any]) -> Optional[str]:
    """``x ← b`` per exchange, e.g. ``x ← a · x ← c``."""
    texts = [f"{', '.join(x.get('data') or ())} ← {x.get('partner') or x['source']}" for x in _exchanges(op)]
    return " · ".join(texts) or None


def _box(id_: str, label: str, region: str, x: float, y: float, w: float, h: float,
         **data: Any) -> dict[str, Any]:
    """A region's box: a frame with the region's name and colour, behind its nodes."""
    return {
        "id": id_, "type": "region", "position": {"x": x, "y": y},
        "data": {"label": label, "color": _REGION.color(region), "region": region, **data},
        "width": w, "height": h, "style": {"width": f"{w}px", "height": f"{h}px"},
        "draggable": False, "selectable": False, "connectable": False,
    }


def _count(op: dict[str, Any]) -> int:
    """Actions inside a loop, at any depth (shown on a collapsed loop)."""
    return sum(1 if c["kind"] == "action" else _count(c) for c in op.get("children", []))


def _exec_view(ops: Optional[list[dict[str, Any]]], collapsed: frozenset[str],
               closed: frozenset[str] = frozenset()) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    if not ops:
        nodes.append({"id": "exec:placeholder", "type": "placeholder", "position": {"x": 0, "y": _TOP},
                      "data": {}, "width": _AW, "height": 70, "style": {"width": f"{_AW}px", "height": "70px"},
                      "draggable": False, "selectable": False})
        return {"nodes": nodes, "edges": edges}
    regions = list(dict.fromkeys(str(op["region"]) for op in ops if op.get("region")))
    if not regions:
        _solver(ops, 0.0, _TOP, "", nodes, edges, collapsed)
        return {"nodes": nodes, "edges": edges}
    regions = _chain_order(ops, regions)
    trees = {r: [op for op in ops if op.get("region") == r] for r in regions}
    _boxes(trees, regions, _rows(trees, collapsed), collapsed, nodes, edges, closed)
    edges += _between(ops, regions, {n["id"]: n for n in nodes}, nodes)
    return {"nodes": nodes, "edges": edges}


def _pairs(ops: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The region pairs that exchange data (a coupling op and its partner region)."""
    out: list[tuple[str, str]] = []
    for op in ops:
        for x in _exchanges(op) if op.get("region") else ():
            if not x.get("partner"):
                continue
            pair = tuple(sorted((op["region"], x["partner"])))
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


def _shown_ops(ops: list[dict[str, Any]], collapsed: frozenset[str]) -> list[dict[str, Any]]:
    """``ops`` and the bodies of their open loops, in the order they are drawn (top down)."""
    out: list[dict[str, Any]] = []
    for op in ops:
        out.append(op)
        if op["kind"] == "loop" and op["id"] not in collapsed:
            out += _shown_ops(op["children"], collapsed)
    return out


def _rows(trees: dict[str, list[dict[str, Any]]], collapsed: frozenset[str]) -> dict[str, int]:
    """A row for every drawn op, shared by the boxes: each box's ops one per row, top down,
    except that the k-th coupling op of every box takes one row (the first free in all of
    them), so the coupling ops of one exchange face each other."""
    seqs = {r: _shown_ops(t, collapsed) for r, t in trees.items()}
    rows: dict[str, int] = {}
    at = dict.fromkeys(seqs, 0)
    cursor = dict.fromkeys(seqs, 0)
    while True:
        for r, seq in seqs.items():
            while at[r] < len(seq) and not _exchanges(seq[at[r]]):
                rows[seq[at[r]]["id"]] = cursor[r]
                cursor[r] += 1
                at[r] += 1
        coupled = [r for r, seq in seqs.items() if at[r] < len(seq)]
        if not coupled:
            return rows
        row = max(cursor[r] for r in coupled)
        for r in coupled:
            rows[seqs[r][at[r]]["id"]] = row
            cursor[r] = row + 1
            at[r] += 1


def _height(op: dict[str, Any], collapsed: frozenset[str]) -> float:
    if op["kind"] == "action":
        return _AH
    return _COLLAPSED_H if _ref(op) in collapsed else _CTRL_H


def _depth(ops: list[dict[str, Any]], collapsed: frozenset[str]) -> int:
    return max((1 + _depth(op["children"], collapsed) for op in ops
                if op["kind"] == "loop" and _ref(op) not in collapsed), default=0)


def _boxes(trees: dict[str, list[dict[str, Any]]], regions: list[str], rows: dict[str, int],
           collapsed: frozenset[str], nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
           closed: frozenset[str] = frozenset()) -> None:
    """One box per region, side by side: its own tree in one column, each op on its row.

    A ``closed`` region's box is narrow and holds only its coupling ops, on their rows, so the
    coupling lines stay attached."""
    heights: dict[int, float] = {}
    for tree in trees.values():
        for op in _shown_ops(tree, collapsed):
            heights[rows[op["id"]]] = max(heights.get(rows[op["id"]], 0.0), _height(op, collapsed))
    row_y: dict[int, float] = {}
    y = _TOP + _BOX_HEAD + _BOX_PAD + 22 + _GAP
    for row in sorted(heights):
        row_y[row] = y
        y += heights[row] + _GAP
    bottom = y
    depth = max(_depth(tree, collapsed) for tree in trees.values())
    side = _EXIT + 8 * depth  # room for the exits on the left and the back edges on the right
    column = max(_CTRL_W, _AW + 2 * _PAD)
    width = column + 2 * side + 2 * _BOX_PAD
    y_of = {op_id: row_y[row] for op_id, row in rows.items()}
    height = bottom + 26 + _BOX_PAD - _TOP
    x = 0.0
    for region in regions:
        tree = trees[region]
        if region in closed:
            narrow = _AW + 2 * _BOX_PAD
            nodes.append(_box(f"exec::box:{region}", region, region, x, _TOP, narrow, height,
                              toggle=region, closed=True))
            # (a coupling op without its body)
            _place([{**op, "children": []} for op in _flat(tree) if _exchanges(op) and op["id"] in y_of],
                   x + narrow / 2, y_of, collapsed, nodes)
            x += narrow + _BOX_GAP
            continue
        nodes.append(_box(f"exec::box:{region}", region, region, x, _TOP, width, height, toggle=region, closed=False))
        x0 = x + _BOX_PAD + side
        cx = x0 + column / 2
        start, end = f"exec::start:{region}", f"exec::end:{region}"
        nodes.append({"id": start, "type": "initial", "position": {"x": cx - 11, "y": _TOP + _BOX_HEAD + _BOX_PAD},
                      "data": {"ref": "start", "region": region}, "width": 22, "height": 22,
                      "draggable": False, "selectable": False})
        _place(tree, cx, y_of, collapsed, nodes)
        nodes.append({"id": end, "type": "final", "position": {"x": cx - 13, "y": bottom},
                      "data": {"ref": "end", "region": region}, "width": 26, "height": 26,
                      "draggable": False, "selectable": False})
        _wire(tree, x0, column, 0, collapsed, nodes, edges)
        edges.append(_edge(start, tree[0]["id"]))
        last = tree[-1]
        edges.append(_exit(last, end, nodes, collapsed) if last["kind"] == "loop" else _edge(last["id"], end))
        x += width + _BOX_GAP


def _flat(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [x for op in ops for x in (op, *_flat(op.get("children", [])))]


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
                     "exchange": _exchange_text(op), "tag": op.get("tag"), "final": bool(op.get("final")),
                     "control": is_open},
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


def _interfaces(ops: list[dict[str, Any]], regions: list[str]) -> list[dict[str, Any]]:
    """The exchanges between the boxes, one per pair of coupling ops of two regions
    that take each other's data: ``{"key", "left", "right", "lx", "rx", "name"}``,
    ``left``/``right`` the two coupling ops in the boxes' order (``regions``), ``lx``/``rx``
    what each receives over this exchange. Ends pair by region and, when the solver names its
    exchanges, by name. ``key`` (``"0"``, ``"1"``, …) keys the exchange's line, its mapping
    node, its values (``ex_vals``) and its highlight (``ex_hot``)."""
    out: list[dict[str, Any]] = []
    ends = [(c, x) for c in _flat(ops) if c.get("region") in regions for x in _exchanges(c)]
    done: set[int] = set()
    for i, (c, x) in enumerate(ends):
        if i in done:
            continue
        match = next((j for j, (d, y) in enumerate(ends) if j != i and j not in done
                      and d.get("region") == x.get("partner") and y.get("partner") == c.get("region")
                      and (x.get("name") is None or y.get("name") is None or x["name"] == y["name"])),
                     None)
        if match is None:
            continue
        done |= {i, match}
        (left, lx), (right, rx) = sorted((ends[i], ends[match]), key=lambda e: regions.index(e[0]["region"]))
        out.append({"key": str(len(out)), "left": left, "right": right, "lx": lx, "rx": rx,
                    "name": x.get("name")})
    return out


def _mapping_data(face: dict[str, Any]) -> dict[str, Any]:
    """What an interface's mapping node shows (``data``): the data each way, mapped or not, the
    mapping kind and each port's interface points; ``None`` where the solver does not tell.

    A coupling op's ``exchange`` is what it *receives*: the right node's is the data that
    goes left → right, mapped from the left port's points onto its own."""
    left, right = face["left"], face["right"]
    lx, rx = face.get("lx") or left["exchange"], face.get("rx") or right["exchange"]
    lm, rm = lx.get("mapping") or {}, rx.get("mapping") or {}

    def fields(exchange: dict[str, Any], mapping: dict[str, Any]) -> list[dict[str, Any]]:
        mapped = mapping.get("mapped")
        return [{"name": n, "mapped": None if mapped is None else n in mapped} for n in exchange.get("data") or ()]

    def first(*values: Any) -> Any:
        return next((v for v in values if v is not None), None)

    kinds = list(dict.fromkeys(k for k in (rm.get("kind"), lm.get("kind")) if k))
    names = [*(lx.get("data") or ()), *(n for n in rx.get("data") or () if n not in (lx.get("data") or ()))]
    return {
        "key": face["key"], "left": left["region"], "right": right["region"],
        "nodes": [left["id"], right["id"]], "data": ", ".join(names), "name": face.get("name"),
        "kind": " / ".join(kinds) or None,
        "points": {left["region"]: first(rm.get("source_points"), lm.get("target_points")),
                   right["region"]: first(lm.get("source_points"), rm.get("target_points"))},
        # left → right is what the right node receives; right → left what the left one does
        "ways": [{"from": left["region"], "to": right["region"], "fields": fields(rx, rm)},
                 {"from": right["region"], "to": left["region"], "fields": fields(lx, lm)}],
    }


#: A mapping node: the place on a coupling line, between the boxes, where the data is mapped
#: from one port's interface points onto the other's. A small chip (its kind); the rest of
#: :func:`_mapping_data` shows on hover / tap.
_MAP_W, _MAP_H = 148, 24


def _map_node(face: dict[str, Any], cx: float, cy: float) -> dict[str, Any]:
    """The mapping node of interface ``face``, centred on ``(cx, cy)``."""
    return {
        "id": f"exmap:{face['key']}", "type": "exmap",
        "position": {"x": cx - _MAP_W / 2, "y": cy - _MAP_H / 2},
        "data": _mapping_data(face),
        "width": _MAP_W, "height": _MAP_H, "style": {"width": f"{_MAP_W}px", "height": f"{_MAP_H}px"},
        "draggable": False, "selectable": False, "connectable": False,
    }


def _between(ops: list[dict[str, Any]], regions: list[str], placed: dict[str, dict[str, Any]],
             out_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The lines between the region boxes: per exchange (:func:`_interfaces`) one from the left
    coupling op through the interface's mapping node (``exmap``, in the middle of the gap) to
    the right one, arrowheads at the coupling ops. Both halves carry the line's class
    (``cl-<key>``); the mapping node shows the data each way and is filled with the values
    while the run steps (``ex_vals``) and lit with the line (``ex_hot``)."""
    out: list[dict[str, Any]] = []
    for face in _interfaces(ops, regions):
        left, right = face["left"], face["right"]
        source, target = left["id"], right["id"]
        if source not in placed or target not in placed:
            continue
        s, tgt = placed[source], placed[target]
        # in the middle of the gap between the two boxes (else between the two nodes)
        a, b = placed.get(f"exec::box:{left['region']}", s), placed.get(f"exec::box:{right['region']}", tgt)
        x0, x1 = a["position"]["x"] + a["width"], b["position"]["x"]
        cy = s["position"]["y"] + s["height"] / 2
        node = _map_node(face, (x0 + x1) / 2, cy)
        out_nodes.append(node)
        out += _line(source, [node["id"]], target, face["key"])
    return out


def _line(source: str, mappings: list[str], target: str, key: str, straight: bool = True) -> list[dict[str, Any]]:
    """A coupling line from ``source`` through its mapping node(s) to ``target`` (class
    ``cl-<key>``): one piece per hop, arrowheads where the data arrives, at the two ends."""
    color = "#6741d9"
    arrow = {"type": "arrowclosed", "color": color}
    common = {"type": "straight" if straight else "smoothstep",
              "style": {"stroke": color, "strokeWidth": 2, "strokeDasharray": "7 5"},
              "class": f"nf-couple cl-{key}"}
    hops = [source, *mappings, target]
    out = []
    for i, (a, b) in enumerate(zip(hops, hops[1:])):
        edge = _edge(a, b, sourceHandle="r", targetHandle="lt", **common)
        if i == 0:
            edge["markerStart"] = arrow
        if i < len(hops) - 2:
            del edge["markerEnd"]  # (only the target's end has one)
        else:
            edge["markerEnd"] = arrow
        out.append(edge)
    return out


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


def _init_view(steps: list[dict[str, Any]], regions: list[str],
               ops: Optional[list[dict[str, Any]]] = None) -> dict[str, list[dict[str, Any]]]:
    """The init DAG; with the execution graph known (``ops``), the boxes in its order and every
    interface's mapping node between its two boxes, joined to them by the coupling line."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    driver = [s for s in steps if not s.get("region")]
    regions = [*regions, *(s["region"] for s in steps if s.get("region") and s["region"] not in regions)]
    regions = list(dict.fromkeys(regions))
    if ops:
        regions = _chain_order(ops, regions)
    faces = _interfaces(ops or [], regions)
    # a region not initialized yet (known from an earlier run) is an empty box
    groups = [(r, [s for s in steps if s.get("region") == r]) for r in regions]
    top_w, top_h, top = _init_block(driver)
    blocks = [(r, *_init_block(g)) for r, g in groups]
    gap = _BOX_GAP if faces else 60
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
    boxes = {n["id"][len("init::box:"):]: n for n in nodes if n["type"] == "region"}
    for face in faces:
        a, b = boxes[face["left"]["region"]], boxes[face["right"]["region"]]
        x0, x1 = a["position"]["x"] + a["width"], b["position"]["x"]
        cy = min(a["position"]["y"] + a["height"] / 2, b["position"]["y"] + b["height"] / 2)
        node = _map_node(face, (x0 + x1) / 2, cy)
        nodes.append(node)
        edges += _line(a["id"], [node["id"]], b["id"], face["key"], straight=False)
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
    closed: frozenset[str] = frozenset(),
) -> dict[str, list[dict[str, Any]]]:
    """One view as a VueFlow graph ``{"nodes": [...], "edges": [...]}``: ``view`` is ``"init"``
    (the initialization) or ``"exec"`` (the execution).

    ``collapsed``: ids of loops drawn without their body. ``regions``: regions known before
    they initialize (e.g. from an earlier run), boxes of the init view from the start.
    ``closed``: regions whose execution box shows only its coupling ops.
    """
    if view == "init":
        order: list[str] = list(regions)
        for op in ops or ():
            order += [r for r in _regions_in(op) if r not in order]
        return _init_view(init_steps, order, ops)
    return _exec_view(ops, collapsed, closed)
