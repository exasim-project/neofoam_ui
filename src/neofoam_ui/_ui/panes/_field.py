"""Field view pane: the image Catalyst rendered in situ at the last pause.

The solver ranks hand their cells and fields to ParaView through Catalyst 2
(:mod:`neofoam_ui.catalyst`); ParaView writes one PNG per frame into a directory
the UI serves as ``insitu/``. No field data passes through the UI.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3

from neofoam_ui.extensions import BasePane, PaneContext, PaneToggle
from neofoam_ui.protocol import CATALYST_LIVE_ENV

__all__ = ()

_FIELD_CSS = r"""
.nf-field { display: flex; flex-direction: column; min-width: 0; min-height: 0;
  border-left: 1px solid rgba(128,128,128,.25); background: rgb(var(--v-theme-surface)); }
.nf-field-bar { display: flex; align-items: center; gap: 8px; padding: 4px 10px; flex-wrap: wrap;
  border-bottom: 1px solid rgba(128,128,128,.2); }
.nf-field-bar .v-select { flex: 0 1 150px; min-width: 110px; }
.nf-field-bar .nf-num { flex: 0 0 112px; }
.nf-field-msg { font-size: 12px; opacity: .7; flex: 1 1 160px; min-width: 0; }
.nf-field-img { flex: 1 1 auto; min-height: 0; width: 100%; object-fit: contain; display: block; }
.nf-field-empty { flex: 1 1 auto; display: flex; align-items: center; justify-content: center;
  font-size: 13px; opacity: .6; padding: 16px; text-align: center; }
.nf-field-regions { padding: 8px 12px 0; display: flex; flex-direction: column; gap: 4px; }
.nf-field-region { display: flex; justify-content: space-between; gap: 12px; border-left: 4px solid;
  padding: 2px 8px; font-size: 13px; background: rgba(128,128,128,.06); border-radius: 4px; }
@media (max-width: 760px) { .nf-field { border-left: 0; border-top: 1px solid rgba(128,128,128,.25); } }
"""


def _component_items(ncomp: int) -> list[dict[str, Any]]:
    if ncomp <= 1:
        return []
    names = "xyz" if ncomp == 3 else [str(i) for i in range(ncomp)]
    return [{"title": "magnitude", "value": "mag"}] + [
        {"title": n, "value": str(i)} for i, n in enumerate(names)]


def _field_pane() -> None:
    """The pane (header + rendered image); shown while ``fv_on``."""
    with html.Div(classes="nf-field", v_show="fv_on"):
        with html.Div(classes="nf-field-bar"):
            v3.VSelect(v_model=("fv_field",), items=("fv_fields",), label="field", density="compact",
                       hide_details=True, variant="outlined", disabled=("!fv_fields.length",))
            v3.VSelect(v_model=("fv_comp",), items=("fv_comps",), label="component", density="compact",
                       hide_details=True, variant="outlined", v_show="fv_comps.length")
            v3.VCheckbox(v_model=("fv_live",), label="live", hide_details=True, density="compact",
                         style="flex: none;", title="update while running (off: only at pauses)")
            # Live frames: every N time steps, at least s seconds apart (each costs a render).
            v3.VTextField(v_model_number=("fv_every",), type="number", min=1, label="every n steps",
                          density="compact", hide_details=True, variant="outlined", v_show="fv_live",
                          classes="nf-num", title="a live frame at most every n time steps")
            v3.VTextField(v_model_number=("fv_secs",), type="number", min=0, step=0.1, label="≥ s apart",
                          density="compact", hide_details=True, variant="outlined", v_show="fv_live",
                          classes="nf-num", title="and at least this many seconds apart")
            html.Span("{{ fv_msg }}", classes="nf-field-msg")
        html.Img(src=("fv_src",), classes="nf-field-img", v_show="fv_src", alt="in-situ view")
        # a multi-region solver without an image (no mesh, or no Catalyst): each region's fields,
        # grouped in the boxes' order, in the region's colour (the variables table's rows)
        with html.Div(classes="nf-field-regions", v_if="!fv_src && fields_rows.some(f => f.region)"):
            with html.Div(v_for="f in fields_rows.filter(f => f.region)", key="f.name",
                          classes="nf-field-region", style=("`border-left-color:${f.color}`",)):
                html.Span("{{ f.name }}", classes="nf-mono", style=("`color:${f.color}`",))
                html.Span("{{ f.min === f.max ? f.mean : f.min + ' … ' + f.max + ' (mean ' + f.mean + ')' }}")
        html.Div("{{ fv_msg }}", classes="nf-field-empty", v_show="!fv_src")


class _FieldPane(BasePane):
    """The field view; it also asks the worker for the frames every pane needs (``view``)."""

    name = "field"
    toggle = PaneToggle("fv_on", "CFD 3D", "mdi-cube-outline",
                        "a solver field on the mesh (Catalyst), refreshed at every pause")
    css = _FIELD_CSS

    def __init__(self, ctx: PaneContext) -> None:
        super().__init__(ctx)
        #: Last view request sent to the worker (so the worker's own choice is not echoed back).
        self._view_req: Optional[tuple[Any, ...]] = None
        #: Render at every pause even with the panes closed (a ParaView is attached via Live).
        self.live = os.environ.get(CATALYST_LIVE_ENV, "off") != "off"
        state = ctx.state
        state.change("fv_field", "fv_comp")(lambda **_: self.send_view())
        state.change("fv_live")(lambda fv_live, **_: ctx.send({"cmd": "live", "on": bool(fv_live)}))
        state.change("fv_every", "fv_secs")(lambda **_: self._send_frames())

    def reset(self) -> None:
        s = self.ctx.state
        # The toggle and the choice survive a restart.
        s.fv_on = bool(s.fv_on)
        s.fv_field = s.fv_field or None
        s.fv_comp = s.fv_comp or "mag"
        s.fv_fields = []
        s.fv_comps = []
        s.fv_src = None
        s.fv_live = True if s.fv_live is None else bool(s.fv_live)
        # Live frames at most every fv_every time steps and fv_secs seconds apart.
        s.fv_every = s.fv_every if s.fv_every is not None else 1
        s.fv_secs = s.fv_secs if s.fv_secs is not None else 0.5
        s.fv_msg = "the field view starts once the solver context exists (after initialization)"

    def build(self) -> None:
        _field_pane()

    def handlers(self) -> Mapping[str, Any]:
        return {"frame": self._on_frame}

    def spawned(self) -> None:
        self.ctx.send({"cmd": "live", "on": bool(self.ctx.state.fv_live)})
        self._send_frames()
        self._view_req = None  # a new worker has none
        self.send_view()

    def layout_changed(self) -> None:
        self.send_view()

    def wants_frames(self) -> bool:
        return bool(self.ctx.state.fv_on)

    def send_view(self) -> None:
        """Tell the worker what to render; nothing at pauses when no pane wants frames."""
        s = self.ctx.state
        on = bool(self.ctx.wants_frames() or self.live)
        req = (True, s.fv_field, s.fv_comp) if on else (False,)
        if req == self._view_req:
            return
        self._view_req = req
        self.ctx.send({"cmd": "view", "on": on, "field": s.fv_field, "component": s.fv_comp})

    def _send_frames(self) -> None:
        s = self.ctx.state
        try:
            every, secs = max(1, int(s.fv_every)), max(0.0, float(s.fv_secs))
        except (TypeError, ValueError):  # a half-typed number
            return
        self.ctx.send({"cmd": "frames", "every": every, "seconds": secs})

    def _on_frame(self, msg: dict[str, Any]) -> None:
        s = self.ctx.state
        if msg.get("error"):
            s.fv_msg = msg["error"]
            s.fv_src = None
            return
        if self._view_req != (True, *msg.get("view", (msg["field"], msg["component"]))):
            return  # rendered for an earlier choice (a live frame in flight): keep the user's
        fields = msg["fields"]
        s.fv_fields = [{"title": f["name"] + (" (vector)" if f["ncomp"] == 3 else f" ({f['ncomp']} comp.)" if f["ncomp"] > 1 else ""),
                        "value": f["name"]} for f in fields]
        ncomp = next((f["ncomp"] for f in fields if f["name"] == msg["field"]), 1)
        s.fv_comps = _component_items(ncomp)
        # The worker may have picked the field (none chosen yet / not cell-sized): adopt it.
        # Echoing it back is harmless (the same frame is not rendered twice).
        s.fv_field, s.fv_comp = msg["field"], msg["component"]
        s.fv_src = f"insitu/{self.ctx.run_dir}/{msg['file']}"
        t = msg.get("time")
        ranks = f"{self.ctx.np} ranks" if self.ctx.np > 1 else ""
        parts = [f"t = {t:.6g}" if isinstance(t, (int, float)) else "", "rendered in situ (Catalyst)", ranks]
        s.fv_msg = " · ".join(p for p in parts if p)
