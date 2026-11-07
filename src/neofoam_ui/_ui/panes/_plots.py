"""Plots pane: NeoFOAM's post-processing tables as line charts over time (x axis: time).

The worker reports each table's newest rows after every ``post_process``
(``pptable`` messages, :mod:`neofoam_ui.pptables`); :class:`_TableHistory` keeps a
bounded history per table and turns it into one plotly chart per table — one line
per value column, and for a table with several rows (bins, residual labels, …) one
line per row and column, capped at ``_MAX_LINES``. Drawn with trame-plotly, whose
plotly.js is bundled (works offline).
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_plotly.widgets import plotly

from neofoam_ui._ui.panes._table_history import _TableHistory
from neofoam_ui.extensions import BasePane, PaneContext, PaneToggle

__all__ = ()

_PLOT_CSS = r"""
.nf-pt { flex: 1 1 0; }
.nf-pt-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4px 6px; }
.nf-pt-chart { border-bottom: 1px solid rgba(128,128,128,.15); padding: 2px 0 4px; }
.nf-field-bar .v-select.nf-pt-sel { flex: 1 1 260px; max-width: 100%; }
.nf-pt-note { font-size: 11px; opacity: .65; padding: 0 8px 2px; }
"""


def _plot_pane() -> None:
    """The pane (header + one chart per table); shown while ``pt_on``."""
    with html.Div(classes="nf-field nf-pt", v_show="pt_on"):
        with html.Div(classes="nf-field-bar"):
            v3.VIcon("mdi-chart-line", size="small")
            html.Span("Post-processing tables", classes="text-body-2 font-weight-medium")
            v3.VSelect(v_model=("pt_sel",), items=("pt_names",), label="tables", multiple=True,
                       chips=True, closable_chips=True, density="compact", hide_details=True,
                       variant="outlined", disabled=("!pt_names.length",), classes="nf-pt-sel")
            html.Span("{{ pt_msg }}", classes="nf-field-msg")
        # v_if: the charts are created when the pane opens, so plotly sizes them to it.
        with html.Div(classes="nf-pt-list", v_if="pt_on && pt_charts.length"):
            with html.Div(v_for="ptc in pt_charts", key="ptc.name", classes="nf-pt-chart"):
                # One figure per table: named after the loop variable, the figure binds
                # ``ptc.data`` / ``ptc.layout`` of its table (the state key it also creates is shadowed).
                plotly.Figure(state_variable_name="ptc", display_mode_bar=False, responsive=True,
                              style=("`height: ${ptc.layout.height}px`",))
                html.Div("{{ ptc.note }}", v_if="ptc.note", classes="nf-pt-note")
        html.Div("{{ pt_names.length && !pt_sel.length ? 'pick tables to plot' : pt_msg }}",
                 classes="nf-field-empty", v_show="!pt_charts.length")


#: Seconds between chart updates while running (each step brings a row per table).
_PLOT_EVERY = 0.5


class _PlotsPane(BasePane):
    """NeoFOAM's post-processing tables plotted over time; the chosen tables survive a restart."""

    name = "plots"
    toggle = PaneToggle("pt_on", "Plots", "mdi-chart-line", "plot NeoFOAM's post-processing tables over time")
    css = _PLOT_CSS

    def __init__(self, ctx: PaneContext) -> None:
        super().__init__(ctx)
        #: Rows of NeoFOAM's post-processing tables (``pptable``).
        self.history = _TableHistory()
        self.plotted = 0.0
        ctx.state.change("pt_on", "pt_sel")(lambda **_: self.plot(force=True))

    def reset(self) -> None:
        s = self.ctx.state
        # The toggle survives a restart, the history not.
        s.pt_on = bool(s.pt_on)
        s.pt_charts = []
        s.pt_names = []
        s.pt_sel = list(s.pt_sel or [])  # the chosen tables survive a restart
        s.pt_msg = "the tables' rows appear after the first post_process (once initialized)"
        self.history.clear()

    def build(self) -> None:
        _plot_pane()

    def handlers(self) -> Mapping[str, Any]:
        return {"pptables": self._on_tables, "pptable": self._on_row,
                "paused": lambda msg: self.plot(force=True)}

    def _on_tables(self, msg: dict[str, Any]) -> None:
        tables = msg["tables"]
        self.history.declare([t["name"] for t in tables])
        self._table_names()
        self.ctx.state.pt_msg = (f"{len(tables)} tables declared · rows after each post_process"
                                 if tables else
                                 "this case declares no post-processing tables "
                                 "(system/postProcess.py / postProcess.yaml)")
        self.plot(force=True)

    def _on_row(self, msg: dict[str, Any]) -> None:
        self.history.add(msg)
        self.plot()

    def plot(self, force: bool = False) -> None:
        """Hand the tables' history to the charts: at pauses, at most every ``_PLOT_EVERY`` s running."""
        s = self.ctx.state
        now = time.monotonic()
        if not force and now - self.plotted < _PLOT_EVERY:
            return
        self.plotted = now
        if len(self.history.names()) != len(s.pt_names):
            self._table_names()
        if not s.pt_on or not self.history.names():
            return
        s.pt_charts = self.history.charts(s.pt_sel)
        t = self.history.time
        ranks = f"reduced over {self.ctx.np} ranks" if self.ctx.np > 1 else ""
        parts = [f"{len(s.pt_charts)} of {len(s.pt_names)} tables",
                 f"t = {t:.6g}" if t is not None else "no rows yet", ranks]
        s.pt_msg = " · ".join(p for p in parts if p)

    def _table_names(self) -> None:
        """The select box's tables; keeps the choice that still exists, else the first table."""
        s = self.ctx.state
        s.pt_names = self.history.names()
        kept = [n for n in s.pt_sel if n in s.pt_names]
        s.pt_sel = kept or s.pt_names[:1]
