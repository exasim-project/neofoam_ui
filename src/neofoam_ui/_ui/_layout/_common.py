"""What both layouts share: sizes, edges, activity frames."""

from __future__ import annotations

from typing import Any

__all__ = ()

# Sizes in px.
_AW, _AH = 240, 44  # action
_IW, _IH = 190, 40  # init step
_GAP = 36
_PAD = 26
_LOOP_HEAD = 104  # title + test text + diamond
_LOOP_RIGHT = 36  # lane for the back edge
_COLLAPSED_H = 64  # a collapsed loop: title + test line
_DIAMOND = 34
_ACT_HEAD = 40  # activity frame title
_TOP = 0
_INIT_PER_ROW = 4  # a dependency layer wider than this wraps onto further rows


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
