"""Post-processing pane: the pipelines as diagrams, beside the field view.

* **Catalyst/ParaView**: rank 0 reads the live pipeline back after each in-situ execute
  (:meth:`neofoam_ui.catalyst.InSitu.describe`) — sources, filters, displays, views
  and extractors with a few settings and the time each took — and the worker sends
  it with the frame. Another script (``NEOFOAM_UI_CATALYST_SCRIPT``) shows as itself.
* **NeoFOAM post-processing**: one frame per table the case declares (``pptables``,
  read off the node objects by :func:`neofoam_ui.pptables.describe_table`), its title
  carrying the table's last compute time; drawn also without Catalyst.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_flow.widgets.flow import Background, Controls, CustomNode, Handle, NodeEditor

from neofoam_ui._ui._format import _fmt_ms
from neofoam_ui._ui._layout._pipeline import _build_pipeline_flow
from neofoam_ui.extensions import BasePane, PaneContext, PaneToggle

__all__ = ()

_PIPELINE_CSS = r"""
.nf-pp { flex: 1 1 0; }
.nf-pp-flow { flex: 1 1 auto; min-height: 0; position: relative; }
.nf-pp-node { border: 1.4px solid #8a94a6; background: #f7f8fb; border-radius: 10px; display: flex;
  padding: 0 10px 0 8px; gap: 8px; font-size: 12px; color: #1d2430; }
.nf-pp-node .nf-stripe { margin: 7px 0; }
.nf-pp-body { flex: 1; min-width: 0; padding-top: 5px; }
.nf-pp-head { display: flex; gap: 6px; align-items: baseline; font-size: 13px; font-weight: 600; }
.nf-pp-type { font-size: 11px; opacity: .6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nf-pp-flow .nf-activity-title { right: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nf-pp-kv { display: grid; grid-template-columns: auto 1fr; gap: 0 8px; margin-top: 3px; line-height: 15px; }
.nf-pp-kv > span:nth-child(odd) { opacity: .6; }
.nf-pp-kv > span:nth-child(even) { font-family: ui-monospace, Menlo, monospace; font-size: 11px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
"""


def _pipeline_pane() -> Any:
    """The pane (header + diagram); shown while ``pp_on``. Returns its node editor."""
    with html.Div(classes="nf-field nf-pp", v_show="pp_on"):
        with html.Div(classes="nf-field-bar"):
            v3.VIcon("mdi-graph-outline", size="small")
            html.Span("Post-processing pipeline", classes="text-body-2 font-weight-medium")
            html.Span("{{ pp_msg }}", classes="nf-field-msg")
        with html.Div(classes="nf-pp-flow", v_show="pp_graph"):
            with NodeEditor(style="width: 100%; height: 100%;") as editor:
                Background(gap=16, size=1, pattern_color="#c3c8d2")
                Controls(show_interactive=False)
                with CustomNode("pp"):
                    with html.Div(classes="nf-node nf-pp-node"):
                        Handle(position="top", type="target", id="t")
                        Handle(position="bottom", type="source", id="b")
                        html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                        with html.Div(classes="nf-pp-body"):
                            with html.Div(classes="nf-pp-head"):
                                html.Span("{{ props.data.label }}", classes="nf-label")
                                html.Span("{{ pp_ms[props.id] }}", classes="nf-meta", v_if="pp_ms[props.id]",
                                          title="time of the last execute (rank 0)")
                            html.Div("{{ props.data.kind }} · {{ props.data.type }}", classes="nf-pp-type")
                            with html.Div(classes="nf-pp-kv"):
                                with html.Template(v_for="(r, i) in props.data.settings", key="i"):
                                    html.Span("{{ r[0] }}")
                                    html.Span("{{ r[1] }}", title=("r[1]",))
                with CustomNode("activity"):
                    with html.Div(classes="nf-node nf-activity"):
                        with html.Div(classes="nf-activity-title", title=("props.data.label",)):
                            html.Span("{{ props.data.label }}")
                            html.Span(" · {{ pp_ms[props.id] }}", classes="nf-meta", v_if="pp_ms[props.id]",
                                      title="time of the table's last compute (rank 0)")
            editor._attributes["min_zoom"] = ':min-zoom="0.1"'
        html.Div("{{ pp_msg }}", classes="nf-field-empty", v_show="!pp_graph")
    return editor


#: Seconds between two updates of the tables' compute times while running.
_TIMES_EVERY = 0.5


class _PipelinePane(BasePane):
    """Both kinds of post-processing as diagrams: ParaView's (with the frames) and NeoFOAM's tables."""

    name = "pipeline"
    toggle = PaneToggle("pp_on", "Pipeline", "mdi-graph-outline",
                        "show the post-processing pipelines (NeoFOAM tables, Catalyst/ParaView) as diagrams")
    css = _PIPELINE_CSS

    def __init__(self, ctx: PaneContext) -> None:
        super().__init__(ctx)
        self.editor: Any = None
        #: Nodes and edges of the diagram drawn last (refit only when they change).
        self._topology: Optional[tuple[Any, ...]] = None
        #: The ParaView pipeline of the last frame and NeoFOAM's tables (``pptables``), drawn together.
        self._desc: Optional[dict[str, Any]] = None
        self._tables: list[dict[str, Any]] = []
        #: Compute time per table since the last update (shown on its diagram frame).
        self._table_ms: dict[str, float] = {}
        self._timed = 0.0

    def reset(self) -> None:
        s = self.ctx.state
        s.pp_on = bool(s.pp_on)
        s.pp_graph = False
        s.pp_ms = {}
        s.pp_msg = "the pipelines appear after initialization (ParaView's with the first in-situ frame)"
        s.pp_cat_msg = ""
        self._topology = None
        self._desc, self._tables = None, []
        if self.editor is not None:
            self.editor.clear_graph()

    def build(self) -> None:
        self.editor = _pipeline_pane()

    def handlers(self) -> Mapping[str, Any]:
        return {"frame": self._on_frame, "pptables": self._on_tables, "pptable": self._on_row,
                "paused": lambda msg: self._show_times(force=True)}

    def layout_changed(self) -> None:
        """Refit when the pane opens or the panes beside it change its width."""
        if self.editor is not None and self.ctx.state.pp_on and self.ctx.state.pp_graph:
            self.editor.fit_view()

    def wants_frames(self) -> bool:
        return bool(self.ctx.state.pp_on)  # ParaView's diagram is read back from the frames

    # -- messages ------------------------------------------------------------------------

    def _on_frame(self, msg: dict[str, Any]) -> None:
        if msg.get("error"):
            if self._desc is not None:
                self._desc = None
                self._draw()
            self._status(msg["error"])
            return
        s = self.ctx.state
        desc = msg.get("pipeline")  # sent when it changed
        if desc is not None:
            self._desc = desc
            self._draw()
        ms = msg.get("pipeline_ms")
        if ms is None:
            if self._desc is None:
                self._status("this Catalyst implementation does not describe its pipeline")
            return
        tables = {k: v for k, v in s.pp_ms.items() if k.startswith("pp:act:")}
        s.pp_ms = {**tables, **{k: _fmt_ms(v) for k, v in ms.items() if k != "total"}}
        t = msg.get("time")
        parts = [f"t = {t:.6g}" if isinstance(t, (int, float)) else "",
                 f"last execute {_fmt_ms(ms['total'])}" if "total" in ms else "",
                 "times of rank 0" if self.ctx.np > 1 else ""]
        self._status("ParaView " + " · ".join(p for p in parts if p))

    def _on_tables(self, msg: dict[str, Any]) -> None:
        """NeoFOAM's post-processing tables, declared: a diagram frame each."""
        self._tables = msg["tables"]
        self._draw()
        self._status()

    def _on_row(self, msg: dict[str, Any]) -> None:
        if msg.get("ms") is not None:
            self._table_ms[msg["name"]] = msg["ms"]
        self._show_times()

    def _show_times(self, force: bool = False) -> None:
        """The tables' compute times on their frames: at pauses, at most every ``_TIMES_EVERY`` s running."""
        now = time.monotonic()
        if not self._table_ms or (not force and now - self._timed < _TIMES_EVERY):
            return
        self._timed = now
        s = self.ctx.state
        s.pp_ms = {**s.pp_ms, **{f"pp:act:{k}": _fmt_ms(v) for k, v in self._table_ms.items()}}
        self._table_ms = {}

    def _draw(self) -> None:
        """Both kinds of post-processing in the pane; refit when the topology changed."""
        flow = _build_pipeline_flow(self._desc, self._tables)
        self.editor.graph = flow
        topology = (tuple(n["id"] for n in flow["nodes"]),
                    tuple((e["source"], e["target"]) for e in flow["edges"]))
        if topology != self._topology:
            self._topology = topology
            self.editor.fit_view()
        self.ctx.state.pp_graph = bool(flow["nodes"])

    def _status(self, catalyst: Optional[str] = None) -> None:
        """The pane's message: what ParaView did last, and how many NeoFOAM tables."""
        s = self.ctx.state
        if catalyst is not None:
            s.pp_cat_msg = catalyst
        n = len(self._tables)
        tables = f"NeoFOAM: {n} table{'s' if n != 1 else ''}" if n else ""
        s.pp_msg = " · ".join(p for p in (tables, s.pp_cat_msg) if p) or s.pp_msg
