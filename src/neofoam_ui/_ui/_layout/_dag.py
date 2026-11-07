"""The solver's activity diagram: initialization DAG and execution tree, stacked in one canvas.

* **Initialization** — the init DAG, layered by longest dependency path;
* **Execution** — the operation tree: loops are VueFlow group nodes (structured
  loop nodes) with a decision diamond at the top, children stacked below,
  and a back edge from the last child to the diamond. A *coupling loop* (an op with
  ``couples``) puts each region's ops in a lane of their own, side by side, and draws the
  data exchange as dashed edges in the sending region's colour between the lanes.

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


#: Height of a lane's header (its region) and the gap between two lanes (room for the
#: exchange edges and their labels).
_LANE_HEAD, _LANE_GAP = 30, 90


def _regions_in(op: dict[str, Any]) -> list[str]:
    """The regions of ``op`` and its body, in order of appearance."""
    seen = [op["region"]] if op.get("region") else []
    for child in op.get("children", []):
        seen += [r for r in _regions_in(child) if r not in seen]
    return seen


def _lanes(op: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """A coupling loop's body as lanes, one per region, in order of first appearance.

    An op goes to its region's lane; a nested loop (no region of its own) to the lane it
    continues, or else to the lane of its first region. ``None``: not a coupling loop, or a
    body op of no region at all (then the loop is drawn as usual).
    """
    if not op.get("couples"):
        return None
    lanes: list[dict[str, Any]] = []
    for child in op["children"]:
        regions = _regions_in(child)
        if not regions:
            return None
        if lanes and lanes[-1]["region"] in regions and not child.get("region"):
            lane = lanes[-1]
        else:
            key = child.get("region") or regions[0]
            lane = next((ln for ln in lanes if ln["region"] == key), None)
            if lane is None:
                lane = {"region": key, "regions": [], "ops": []}
                lanes.append(lane)
        lane["ops"].append(child)
        lane["regions"] += [r for r in regions if r not in lane["regions"]]
    return lanes if len(lanes) > 1 else None


def _measure(op: dict[str, Any], collapsed: frozenset[str] = frozenset()) -> tuple[float, float]:
    op["_lanes"] = None
    if op["kind"] == "action":
        op["_w"], op["_h"] = _AW, _AH
    elif op["id"] in collapsed:
        op["_w"], op["_h"] = _AW + 2 * _PAD, _COLLAPSED_H
    elif (lanes := _lanes(op)) is not None:
        for lane in lanes:
            sizes = [_measure(c, collapsed) for c in lane["ops"]]
            lane["w"] = max(w for w, _ in sizes)
            lane["h"] = _LANE_HEAD + sum(h for _, h in sizes) + _GAP * (len(sizes) - 1) + _PAD / 2
        op["_lanes"] = lanes
        op["_w"] = sum(ln["w"] for ln in lanes) + _LANE_GAP * (len(lanes) - 1) + 2 * _PAD + _LOOP_RIGHT
        op["_h"] = _LOOP_HEAD + max(ln["h"] for ln in lanes) + _PAD
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
                "color": _node_color(op, "domain"),
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
            if op["_lanes"]:
                _lane_body(op, dec_id, nodes, edges, collapsed)
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


#: Control flow inside a coupling loop: kept, but behind the data exchange.
_SECONDARY = {"class": "nf-secondary", "style": {"opacity": 0.45}}


def _lane_body(op: dict[str, Any], dec_id: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
               collapsed: frozenset[str]) -> None:
    """A coupling loop's lanes side by side, the control flow through them, the exchange between them."""
    flow: list[dict[str, Any]] = []
    x, ends, lane_of = _PAD, [], {}
    for i, lane in enumerate(op["_lanes"]):
        color = _REGION.color(lane["region"])
        nodes.append({
            "id": f"{op['id']}::lane{i}", "type": "lane", "parentNode": op["id"],
            "position": {"x": x - 8, "y": _LOOP_HEAD - 6},
            "data": {"label": ", ".join(lane["regions"]), "color": color},
            "width": lane["w"] + 16, "height": lane["h"] + 6,
            "style": {"width": f"{lane['w'] + 16}px", "height": f"{lane['h'] + 6}px"},
            "draggable": False, "selectable": False, "connectable": False,
        })
        ends.append(_ops(lane["ops"], op["id"], x, _LOOP_HEAD + _LANE_HEAD, lane["w"], nodes, flow, collapsed))
        for child in lane["ops"]:
            lane_of[child["id"]] = i
        x += lane["w"] + _LANE_GAP
    flow.append(_edge(dec_id, ends[0][0], label="[true]"))
    for (_, last), (first, _) in zip(ends, ends[1:]):  # lane i, then lane i + 1
        flow.append(_edge(last, first, sourceHandle="b", targetHandle="t"))
    flow.append(_edge(ends[-1][1], dec_id, sourceHandle="r", targetHandle="r",
                      **{"class": "nf-back", "style": {"strokeDasharray": "5 4"}}))
    # (a nested coupling loop's exchange edges arrive in ``flow`` too: they stay as they are)
    edges += [e if e.get("class") == "nf-couple"
              else {**e, **_SECONDARY, "style": {**e.get("style", {}), **_SECONDARY["style"]}} for e in flow]
    edges += _exchange(op, lane_of)


def _exchange(op: dict[str, Any], lane_of: dict[str, int]) -> list[dict[str, Any]]:
    """The loop's ``couples`` as dashed edges in the sending region's colour, send → receive."""
    def find(name: str) -> Optional[dict[str, Any]]:
        # the loop's own ops first: a nested loop may hold an op of the same name
        for child in op["children"]:
            if child["name"] == name:
                return child
        stack = list(op["children"])
        while stack:
            node = stack.pop(0)
            if node["name"] == name:
                return node
            stack += node.get("children", [])
        return None

    def lane(node: dict[str, Any]) -> int:
        top = next((c for c in op["children"] if c is node or _contains(c, node)), None)
        return lane_of.get(top["id"], 0) if top else 0

    out = []
    for send_name, receive_name, data in op.get("couples", []):
        send, receive = find(send_name), find(receive_name)
        if send is None or receive is None:
            continue
        color = _REGION.color(send.get("region"))
        rightwards = lane(send) <= lane(receive)
        out.append(_edge(send["id"], receive["id"], type="default", label=data,
                         sourceHandle="r" if rightwards else "ls",
                         targetHandle="lt" if rightwards else "rt",
                         style={"stroke": color, "strokeWidth": 2, "strokeDasharray": "7 5"},
                         labelStyle={"fill": color, "fontWeight": 600},
                         markerEnd={"type": "arrowclosed", "color": color},
                         **{"class": "nf-couple"}))
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
