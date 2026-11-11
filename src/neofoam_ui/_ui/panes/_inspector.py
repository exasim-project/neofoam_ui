"""What the inspector shows of the selected node (no trame)."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from neofoam_ui._ui._format import _fmt_ms, _short_path
from neofoam_ui._ui._layout._palette import _DOMAIN
from neofoam_ui._ui._model import _RunModel

__all__ = ()


def _inspector_model(model: _RunModel, sel: Optional[str], bps: Iterable[str]) -> Optional[dict[str, Any]]:
    """The inspector's state (``insp``) for node ``sel``; ``None`` = nothing selected."""
    info = model.index.get(sel) if sel else None
    if info is None:
        return None
    chip = lambda i, label=None: {"id": i, "name": label or model.name(i)}  # noqa: E731
    rows: list[dict[str, str]] = []
    nav: list[dict[str, Any]] = []
    if info["kind"] == "init":
        kind_label = "Init step"
        rows += [{"k": "category", "v": info.get("category") or "—"}]
        nav += [{"dir": "⬅", **chip(p)} for p in info["preds"]]
        nav += [{"dir": "➡", **chip(c)} for c in info["succs"]]
    else:
        kind_label = "Loop (IterativeOp)" if info["kind"] == "loop" else "Action (SequentialOp)"
        rows += [{"k": "position", "v": info.get("pos") or "—"},
                 {"k": "domain", "v": info.get("domain") or "—"}]
        if info.get("number") and info.get("number") != info.get("pos"):
            rows.append({"k": "declared no.", "v": info["number"]})
        if info.get("test"):
            rows.append({"k": "loop test", "v": info["test"]})
        if info.get("guard"):
            rows.append({"k": "guard", "v": f"[{info['guard']}]"})
        if info.get("injected_by"):
            rows.append({"k": "injected by", "v": f"⊕ model {info['injected_by']}"})
        if info.get("tag"):
            rows.append({"k": "tag", "v": info["tag"]})
        if info.get("final"):
            rows.append({"k": "final", "v": "runs in the final pass of its coupling loops (toggle on its box)"})
        for ex in info.get("exchanges") or ([info["exchange"]] if info.get("exchange") else []):
            rows.append({"k": "exchange", "v": f"{', '.join(ex.get('data') or ())} ← "
                         f"{ex.get('partner') or '—'} ({ex['source']})"
                         + (f", exchange {ex['name']}" if ex.get("name") else "")})
            if ex.get("mapping"):
                rows.append({"k": "mapping", "v": _mapping_text(ex["mapping"], ex.get("data") or ())})
        if info["prev"]:
            is_loop_test = info["prev"] == info["parent"]
            nav.append({"dir": "⬅", **chip(info["prev"], f"↺ {model.name(info['prev'])} test"
                                               if is_loop_test else None)})
        if info["next"]:
            is_loop_test = info["next"] == info["parent"]
            nav.append({"dir": "➡", **chip(info["next"], f"↺ {model.name(info['next'])} test"
                                               if is_loop_test else None)})
        if info["parent"]:
            nav.append({"dir": "⬆", **chip(info["parent"])})
        nav += [{"dir": "⬇", **chip(c)} for c in info["child_ids"]]
    if sel in model.residuals:
        rows.append({"k": "residuals", "v": "  ".join(f"{k} {v:.2e}" for k, v in model.residuals[sel].items())})
    rows.append({"k": "source", "v": _short_path(info.get("source"))})
    rows.append({"k": "calls", "v": str(model.calls.get(sel, 0))})
    if sel in model.last_ms:
        rows.append({"k": "last time", "v": f"{model.last_ms[sel]:.3f} ms"})
    if sel in model.total_ms:
        rows.append({"k": "total time", "v": _fmt_ms(model.total_ms[sel])})
    return {
        "id": sel,
        "name": info["name"],
        "type": kind_label,
        "color": _DOMAIN.color(info.get("domain") or info.get("category")),
        "rows": rows,
        "doc": info.get("doc") or "",
        "nav": nav,
        "reads": info.get("reads") or [],
        "writes": info.get("writes") or [],
        "seen": model.writes_seen.get(sel, []),
        "bp": sel in bps,
    }


def _mapping_text(mapping: dict[str, Any], data: Iterable[str]) -> str:
    """A coupling op's mapping, e.g. ``nearest-neighbor, 4 → 3 points; mapped T, as is h``."""
    pts = lambda n: "?" if n is None else str(n)  # noqa: E731
    text = f"{mapping.get('kind') or 'unknown'}, {pts(mapping.get('source_points'))} → " \
           f"{pts(mapping.get('target_points'))} points"
    mapped = mapping.get("mapped")
    if mapped is None:
        return text + "; mapped fields unknown"
    plain = [n for n in data if n not in mapped]
    return text + "; mapped " + (", ".join(mapped) or "—") + (f", as is {', '.join(plain)}" if plain else "")
