"""Plots pane: NeoFOAM's post-processing tables as line charts over time (x axis: time).

The worker reports each table's newest rows after every ``post_process``
(``pptable`` messages, :mod:`neofoam_ui.pptables`); :class:`_TableHistory` keeps a
bounded history per table and turns it into one plotly chart per table — one line
per value column, and for a table with several rows (bins, residual labels, …) one
line per row and column, capped at ``_MAX_LINES``. Drawn with trame-plotly, whose
plotly.js is bundled (works offline).
"""

from __future__ import annotations

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_plotly.widgets import plotly

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
