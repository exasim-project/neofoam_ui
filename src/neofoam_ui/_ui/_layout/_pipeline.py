"""The post-processing pipelines (Catalyst/ParaView's and each NeoFOAM table's) as diagrams:
one activity frame each, nodes layered by data flow."""

from __future__ import annotations

from typing import Any, Optional

from neofoam_ui._ui._layout._common import _ACT_HEAD, _GAP, _PAD, _activity, _edge
from neofoam_ui._ui._layout._palette import _PIPELINE

__all__ = ()


_PW = 260  # pipeline node width
_PH_HEAD, _PH_ROW = 42, 15  # its name + type lines, one settings row
_PIPE_PER_ROW = 3

_FRAMES_ROW_W = 1700  # pipeline frames wrap onto a new row beyond this width


def _pipe_layers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Rows of pipeline nodes: one per longest-path depth from a source, wrapped at ``_PIPE_PER_ROW``."""
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
    return [layer[i:i + _PIPE_PER_ROW] for layer in layers for i in range(0, len(layer), _PIPE_PER_ROW)]


def _build_pipeline_flow(desc: Optional[dict[str, Any]] = None,
                        tables: Any = ()) -> dict[str, list[dict[str, Any]]]:
    """The post-processing pipelines as one VueFlow graph, one activity frame each.

    ``desc``: the ParaView pipeline :meth:`neofoam_ui.catalyst.InSitu.describe` found
    (frame ``pp:act``); ``tables``: NeoFOAM's tables as :func:`neofoam_ui.pptables.describe_table`
    describes them (frames ``pp:act:<table>``). Frames sit side by side, wrapping onto
    further rows beyond ``_FRAMES_ROW_W``.
    """
    frames: list[tuple[str, str, dict[str, Any]]] = []
    if desc is not None:
        script = desc.get("script")
        frames.append(("pp:act", "ParaView (Catalyst)" + (f" · {script}" if script else ""), desc))
    for table in tables:
        frames.append((f"pp:act:{table['name']}", f"NeoFOAM post-processing · {table['name']}", table))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    x = y = row_h = 0.0
    for act, title, frame in frames:
        frame_nodes, frame_edges, w, h = _pipeline_frame(act, frame)
        if x > 0 and x + w > _FRAMES_ROW_W:
            x, y, row_h = 0.0, y + row_h + _GAP, 0.0
        nodes.append(_activity(act, title, x, y, w, h))
        nodes += frame_nodes
        edges += frame_edges
        x += w + _GAP
        row_h = max(row_h, h)
    return {"nodes": nodes, "edges": edges}


def _pipeline_frame(act: str, desc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float]:
    """One pipeline's nodes (inside frame ``act``) and edges, and the frame's size."""
    known = {n["id"] for n in desc["nodes"]}
    edges_in = [e for e in desc["edges"] if e["source"] in known and e["target"] in known]
    layers = _pipe_layers(desc["nodes"], edges_in)
    heights = [max((_PH_HEAD + _PH_ROW * len(n["settings"]) + 8 for n in row), default=_PH_HEAD)
               for row in layers]
    widest = max((len(row) for row in layers), default=1)
    w = widest * _PW + (widest - 1) * 24 + 2 * _PAD
    h = _ACT_HEAD + sum(heights) + _GAP * max(len(layers) - 1, 0) + _PAD
    nodes = []
    y = _ACT_HEAD
    for row, row_h in zip(layers, heights):
        row_w = len(row) * _PW + (len(row) - 1) * 24
        for i, n in enumerate(row):
            nh = _PH_HEAD + _PH_ROW * len(n["settings"]) + 8
            nodes.append({
                "id": n["id"], "type": "pp", "parentNode": act,
                "position": {"x": (w - row_w) / 2 + i * (_PW + 24), "y": y + (row_h - nh) / 2},
                "data": {"label": n["name"], "type": n["type"], "kind": n["kind"],
                         "settings": n["settings"], "color": _PIPELINE.color(n["kind"])},
                "width": _PW, "height": nh, "style": {"width": f"{_PW}px", "height": f"{nh}px"},
                "draggable": False, "selectable": False, "connectable": False,
            })
        y += row_h + _GAP
    edges = [_edge(e["source"], e["target"], **({"label": e["label"]} if e.get("label") else {}))
             for e in edges_in]
    return nodes, edges, w, h
