"""Plots pane: NeoFOAM's post-processing tables as line charts over time (x axis: time).

The worker reports each table's newest rows after every ``post_process``
(``pptable`` messages, :mod:`neofoam_ui.pptables`); :class:`TableHistory` keeps a
bounded history per table and turns it into one plotly chart per table — one line
per value column, and for a table with several rows (bins, residual labels, …) one
line per row and column, capped at ``MAX_LINES``. Drawn with trame-plotly, whose
plotly.js is bundled (works offline).
"""

from __future__ import annotations

from typing import Any, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_plotly.widgets import plotly

__all__ = ["HISTORY_MAX", "MAX_LINES", "PLOT_CSS", "TableHistory", "plot_pane"]

#: Time steps kept per table; older ones are dropped.
HISTORY_MAX = 2000
#: Lines drawn per chart; a table with more rows × columns shows the first ones.
MAX_LINES = 12
#: |value| at or beyond this is NeoFOAM's "no data" sentinel (``GREAT``, an empty bin): a gap.
NO_DATA = 1.0e15

PLOT_CSS = r"""
.nf-pt { flex: 1 1 0; }
.nf-pt-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4px 6px; }
.nf-pt-chart { border-bottom: 1px solid rgba(128,128,128,.15); padding: 2px 0 4px; }
.nf-field-bar .v-select.nf-pt-sel { flex: 1 1 260px; max-width: 100%; }
.nf-pt-note { font-size: 11px; opacity: .65; padding: 0 8px 2px; }
"""


class _Table:
    """One table's history: the times and one value list per line, aligned."""

    def __init__(self) -> None:
        self.times: list[float] = []
        #: (group labels, column) → values, one per entry of ``times`` (None = no value then).
        self.series: dict[tuple[tuple[Any, ...], str], list[Optional[float]]] = {}
        self.group_columns: list[str] = []
        self.columns: list[str] = []
        self.ms: Optional[float] = None


class TableHistory:
    """Bounded per-table history of ``pptable`` messages, as plotly charts."""

    def __init__(self, max_points: int = HISTORY_MAX, max_lines: int = MAX_LINES) -> None:
        self.max_points = max_points
        self.max_lines = max_lines
        #: Declared table names in their order (from ``pptables``), so empty ones show too.
        self.declared: list[str] = []
        self.tables: dict[str, _Table] = {}
        self.time: Optional[float] = None

    def clear(self) -> None:
        self.declared, self.tables, self.time = [], {}, None

    def declare(self, names: list[str]) -> None:
        self.declared = list(names)

    def add(self, msg: dict[str, Any]) -> None:
        """Append one ``pptable`` message (one time step of one table)."""
        table = self.tables.setdefault(msg["name"], _Table())
        table.columns, table.group_columns = list(msg["columns"]), list(msg.get("group_columns") or [])
        table.ms = msg.get("ms")
        t = float(msg["time"])
        self.time = t
        if table.times and t <= table.times[-1]:  # the same step again (a re-evaluation): replace it
            n = next(i for i in range(len(table.times)) if table.times[i] >= t)
            del table.times[n:]
            for values in table.series.values():
                del values[n:]
        table.times.append(t)
        n = len(table.times)
        groups = msg.get("groups") or [[] for _ in msg["values"]]
        for labels, row in zip(groups, msg["values"]):
            for column, value in zip(table.columns, row):
                values = table.series.setdefault((tuple(labels), column), [None] * (n - 1))
                values.extend([None] * (n - 1 - len(values)))  # a line that skipped steps
                values.append(None if value is None or abs(value) >= NO_DATA else float(value))
        for values in table.series.values():  # lines this step did not report
            values.extend([None] * (n - len(values)))
        if n > self.max_points:
            drop = n - self.max_points
            del table.times[:drop]
            for values in table.series.values():
                del values[:drop]

    def names(self) -> list[str]:
        return self.declared + [n for n in self.tables if n not in self.declared]

    def charts(self, names: Optional[list[str]] = None) -> list[dict[str, Any]]:
        """One ``{"name", "data", "layout", "note", "ms"}`` per table (``names`` only), in declaration order."""
        chosen = self.names() if names is None else [n for n in self.names() if n in names]
        return [self._chart(name, self.tables.get(name)) for name in chosen]

    def _chart(self, name: str, table: Optional[_Table]) -> dict[str, Any]:
        layout = {
            "height": 210, "margin": {"l": 56, "r": 12, "t": 26, "b": 30},
            "title": {"text": name, "font": {"size": 13}, "x": 0.01, "xanchor": "left"},
            "xaxis": {"tickfont": {"size": 10}, "hoverformat": ".6g"},  # time; the pane header says so
            "yaxis": {"tickfont": {"size": 10}, "exponentformat": "power"},
            "font": {"size": 11}, "uirevision": name,  # keeps zoom/pan across updates
            "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
        }
        if table is None or not table.times:
            return {"name": name, "data": [], "layout": layout, "note": "no rows yet", "ms": None}
        keys = list(table.series)
        rows = list(dict.fromkeys(k[0] for k in keys))
        shown = keys[: self.max_lines]
        mode = "lines+markers" if len(table.times) <= 40 else "lines"
        data = [{"type": "scatter", "mode": mode, "x": table.times, "y": table.series[key],
                 "name": _line_name(key, table, len(rows)), "connectgaps": False,
                 "marker": {"size": 4}} for key in shown]
        layout["showlegend"] = len(shown) > 1
        if len(shown) > 1:  # the legend below the axis
            layout["legend"] = {"font": {"size": 10}, "orientation": "h", "y": -0.16, "yanchor": "top"}
            layout["height"] = 210 + 20 * ((len(shown) + 2) // 3)
            layout["margin"] = {**layout["margin"], "b": 30 + 20 * ((len(shown) + 2) // 3)}
        notes = []
        if len(rows) > 1:
            label = ", ".join(table.group_columns) or "row"
            notes.append(f"{len(rows)} rows per step (by {label}): one line per row"
                         + (" and column" if len(table.columns) > 1 else ""))
        if len(keys) > len(shown):
            notes.append(f"showing the first {len(shown)} of {len(keys)} lines")
        return {"name": name, "data": data, "layout": layout, "note": " · ".join(notes),
                "ms": table.ms}


def _line_name(key: tuple[tuple[Any, ...], str], table: _Table, n_rows: int) -> str:
    labels, column = key
    if n_rows <= 1 or not labels:
        return column
    group = ", ".join(f"{c}={v}" for c, v in zip(table.group_columns, labels))
    return group if len(table.columns) == 1 else f"{column} · {group}"


def plot_pane() -> None:
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
