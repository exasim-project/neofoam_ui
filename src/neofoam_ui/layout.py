"""Activity-diagram layout: worker graph messages → VueFlow nodes and edges.

Two activities are stacked vertically inside one canvas:

* **Initialization** — the init DAG, layered by longest dependency path;
* **Execution** — the operation tree: loops are VueFlow group nodes (structured
  loop nodes) with a decision diamond at the top, children stacked below,
  and a back edge from the last child to the diamond.

Positions of children are relative to their parent group (VueFlow ``parentNode``).
A *collapsed* loop is drawn as a single node without its body.

:func:`build_pipeline_flow` lays out the post-processing (Catalyst/ParaView)
pipeline the same way: one activity frame, nodes layered by data flow.
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = ["DOMAIN_COLORS", "PIPELINE_COLORS", "build_flow", "build_pipeline_flow", "domain_color"]

# Sizes in px.
AW, AH = 240, 44  # action
IW, IH = 190, 40  # init step
GAP = 36
PAD = 26
LOOP_HEAD = 104  # title + test text + diamond
LOOP_RIGHT = 36  # lane for the back edge
COLLAPSED_H = 64  # a collapsed loop: title + test line
DIAMOND = 34
ACT_HEAD = 40  # activity frame title
TOP = 0
INIT_PER_ROW = 4  # a dependency layer wider than this wraps onto further rows

DOMAIN_COLORS = {
    "solutionLoop": "#5b6fb5",
    "outer": "#d08a2e",
    "heat": "#c0504d",
    "heatSource": "#8e5bb5",
    "material": "#2f9e8f",
    "fieldWriter": "#5b7a99",
    "postProcess": "#6aa84f",
    "mesh": "#7f8c8d",
    "config": "#95a5a6",
    "field": "#2e86c1",
    "model": "#8e5bb5",
}
_FALLBACK = ["#e67e22", "#16a085", "#c0392b", "#2980b9", "#8e44ad", "#27ae60"]


def domain_color(domain: Optional[str]) -> str:
    if not domain:
        return "#9aa3ad"
    if domain not in DOMAIN_COLORS:
        DOMAIN_COLORS[domain] = _FALLBACK[len(DOMAIN_COLORS) % len(_FALLBACK)]
    return DOMAIN_COLORS[domain]


def _edge(source: str, target: str, **kw: Any) -> dict[str, Any]:
    e = {
        "id": f"{source}->{target}" + (f"#{kw['sourceHandle']}" if "sourceHandle" in kw else ""),
        "source": source,
        "target": target,
        "type": "smoothstep",
        "markerEnd": "arrowclosed",
        "sourceHandle": "b",
        "targetHandle": "t",
    }
    e.update(kw)
    return e


# -- execution activity --------------------------------------------------------------


def _measure(op: dict[str, Any], collapsed: frozenset[str] = frozenset()) -> tuple[float, float]:
    if op["kind"] == "action":
        op["_w"], op["_h"] = AW, AH
    elif op["id"] in collapsed:
        op["_w"], op["_h"] = AW + 2 * PAD, COLLAPSED_H
    else:
        sizes = [_measure(c, collapsed) for c in op["children"]]
        inner_w = max((w for w, _ in sizes), default=AW)
        op["_w"] = inner_w + 2 * PAD + LOOP_RIGHT
        # An empty (predicate-only) loop is just its head.
        op["_h"] = LOOP_HEAD + sum(h for _, h in sizes) + GAP * max(len(sizes) - 1, 0) + PAD
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
                "color": domain_color(op.get("domain")),
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
            inner_w = op["_w"] - 2 * PAD - LOOP_RIGHT
            dec_id = f"{op['id']}::test"
            nodes.append(
                {
                    "id": dec_id,
                    "type": "decision",
                    "parentNode": op["id"],
                    "position": {"x": PAD + inner_w / 2 - DIAMOND / 2, "y": LOOP_HEAD - DIAMOND - 22},
                    "data": {"loop": op["id"]},
                    "width": DIAMOND,
                    "height": DIAMOND,
                    "draggable": False,
                    "selectable": False,
                    "connectable": False,
                }
            )
            if op["children"]:
                first, last = _ops(op["children"], op["id"], PAD, LOOP_HEAD, inner_w, nodes, edges,
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
        y += op["_h"] + GAP
    return ops[0]["id"], ops[-1]["id"]


def _count(op: dict[str, Any]) -> int:
    """Actions inside a loop, at any depth (shown on a collapsed loop)."""
    return sum(1 if c["kind"] == "action" else _count(c) for c in op.get("children", []))


# -- initialization activity ------------------------------------------------------------


def _layers(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Rows of init steps: one per dependency layer, wrapped at ``INIT_PER_ROW``."""
    level: dict[str, int] = {}
    for s in steps:  # steps arrive topologically ordered
        level[s["id"]] = 1 + max((level[d] for d in s["depends_on"]), default=-1)
    layers: list[list[dict[str, Any]]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for s in steps:
        layers[level[s["id"]]].append(s)
    return [layer[i:i + INIT_PER_ROW] for layer in layers for i in range(0, len(layer), INIT_PER_ROW)]


def build_flow(
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
        exec_w = max(op["_w"] for op in ops) + 2 * PAD
        exec_h = ACT_HEAD + sum(op["_h"] for op in ops) + GAP * (len(ops) - 1) + PAD
    else:
        exec_w, exec_h = AW + 2 * PAD, ACT_HEAD + 70 + PAD

    layers = _layers(init_steps)
    widest = max((len(layer) for layer in layers), default=1)
    init_w = max(widest * IW + (widest - 1) * 24 + 2 * PAD, 2 * IW)
    init_h = ACT_HEAD + len(layers) * IH + max(len(layers) - 1, 0) * GAP + PAD
    width = max(init_w, exec_w)
    cx = width / 2

    nodes.append({"id": "start", "type": "initial", "position": {"x": cx - 11, "y": TOP},
                  "data": {}, "width": 22, "height": 22, "draggable": False, "selectable": False})
    y = TOP + 22 + GAP

    nodes.append(_activity("act:init", "Initialization", cx - init_w / 2, y, init_w, init_h))
    edges.append(_edge("start", "act:init"))
    for li, layer in enumerate(layers):
        row_w = len(layer) * IW + (len(layer) - 1) * 24
        for si, step in enumerate(layer):
            nodes.append(
                {
                    "id": step["id"],
                    "type": "init",
                    "parentNode": "act:init",
                    "position": {"x": (init_w - row_w) / 2 + si * (IW + 24),
                                 "y": ACT_HEAD + li * (IH + GAP)},
                    "data": {"label": step["name"], "color": domain_color(step.get("category"))},
                    "width": IW, "height": IH,
                    "draggable": False, "selectable": False, "connectable": False,
                }
            )
            for dep in step["depends_on"]:
                edges.append(_edge(dep, step["id"]))
    y += init_h + GAP

    title = f"Execution · {solver_name}" if solver_name else "Execution"
    nodes.append(_activity("act:exec", title, cx - exec_w / 2, y, exec_w, exec_h))
    edges.append(_edge("act:init", "act:exec"))
    if ops:
        first, last = _ops(ops, "act:exec", PAD, ACT_HEAD, exec_w - 2 * PAD, nodes, edges, collapsed)
        last_kind = ops[-1]["kind"]
    else:
        nodes.append({"id": "exec:placeholder", "type": "placeholder", "parentNode": "act:exec",
                      "position": {"x": PAD, "y": ACT_HEAD}, "data": {},
                      "width": AW, "height": 70, "style": {"width": f"{AW}px", "height": "70px"},
                      "draggable": False, "selectable": False})
        last_kind = None
    y += exec_h + GAP

    nodes.append({"id": "end", "type": "final", "position": {"x": cx - 13, "y": y},
                  "data": {}, "width": 26, "height": 26, "draggable": False, "selectable": False})
    edges.append(_edge("act:exec", "end", **({"label": "[false]"} if last_kind == "loop" else {})))
    return {"nodes": nodes, "edges": edges}


def _activity(id_: str, label: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "activity",
        "position": {"x": x, "y": y},
        "data": {"label": label},
        "width": w,
        "height": h,
        "style": {"width": f"{w}px", "height": f"{h}px"},
        "draggable": False,
        "selectable": False,
        "connectable": False,
    }


# -- post-processing pipeline ------------------------------------------------------------

PW = 260  # pipeline node width
PH_HEAD, PH_ROW = 42, 15  # its name + type lines, one settings row
PIPE_PER_ROW = 3

PIPELINE_COLORS = {
    "source": "#2e86c1",
    "filter": "#6aa84f",
    "display": "#d08a2e",
    "view": "#8e5bb5",
    "extractor": "#c0504d",
}


def _pipe_layers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Rows of pipeline nodes: one per longest-path depth from a source, wrapped at ``PIPE_PER_ROW``."""
    preds: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        preds[e["target"]].append(e["source"])
    level: dict[str, int] = {}

    def depth(nid: str, seen: frozenset[str] = frozenset()) -> int:
        if nid not in level:  # ``seen`` guards against a cycle (a pipeline has none)
            level[nid] = 1 + max((depth(p, seen | {nid}) for p in preds[nid] if p not in seen), default=-1)
        return level[nid]

    for n in nodes:
        depth(n["id"])
    layers: list[list[dict[str, Any]]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for n in nodes:
        layers[level[n["id"]]].append(n)
    return [layer[i:i + PIPE_PER_ROW] for layer in layers for i in range(0, len(layer), PIPE_PER_ROW)]


def build_pipeline_flow(desc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The pipeline :func:`neofoam_ui.catalyst.describe_pipeline` found, as a VueFlow graph."""
    known = {n["id"] for n in desc["nodes"]}
    edges_in = [e for e in desc["edges"] if e["source"] in known and e["target"] in known]
    layers = _pipe_layers(desc["nodes"], edges_in)
    heights = [max((PH_HEAD + PH_ROW * len(n["settings"]) + 8 for n in row), default=PH_HEAD)
               for row in layers]
    widest = max((len(row) for row in layers), default=1)
    w = widest * PW + (widest - 1) * 24 + 2 * PAD
    h = ACT_HEAD + sum(heights) + GAP * max(len(layers) - 1, 0) + PAD
    script = desc.get("script")
    nodes = [_activity("pp:act", "Post-processing" + (f" · {script}" if script else ""), 0, 0, w, h)]
    y = ACT_HEAD
    for row, row_h in zip(layers, heights):
        row_w = len(row) * PW + (len(row) - 1) * 24
        for i, n in enumerate(row):
            nh = PH_HEAD + PH_ROW * len(n["settings"]) + 8
            nodes.append({
                "id": n["id"], "type": "pp", "parentNode": "pp:act",
                "position": {"x": (w - row_w) / 2 + i * (PW + 24), "y": y + (row_h - nh) / 2},
                "data": {"label": n["name"], "type": n["type"], "kind": n["kind"],
                         "settings": n["settings"], "color": PIPELINE_COLORS.get(n["kind"], "#9aa3ad")},
                "width": PW, "height": nh, "style": {"width": f"{PW}px", "height": f"{nh}px"},
                "draggable": False, "selectable": False, "connectable": False,
            })
        y += row_h + GAP
    return {"nodes": nodes, "edges": [_edge(e["source"], e["target"]) for e in edges_in]}
