"""Bounded per-table history of ``pptable`` messages, as plotly chart dicts (no trame here)."""

from __future__ import annotations

from typing import Any, Optional

__all__ = ()


#: Time steps kept per table; older ones are dropped.
_HISTORY_MAX = 2000
#: Lines drawn per chart; a table with more rows × columns shows the first ones.
_MAX_LINES = 12
#: |value| at or beyond this is NeoFOAM's "no data" sentinel (``GREAT``, an empty bin): a gap.
_NO_DATA = 1.0e15


class _Table:
    """One table's history: the times and one value list per line, aligned."""

    def __init__(self) -> None:
        self.times: list[float] = []
        #: (group labels, column) → values, one per entry of ``times`` (None = no value then).
        self.series: dict[tuple[tuple[Any, ...], str], list[Optional[float]]] = {}
        self.group_columns: list[str] = []
        self.columns: list[str] = []
        self.ms: Optional[float] = None
        #: A region's table (its fields): drawn in the region's colour; and the x axis' name.
        self.region: Optional[str] = None
        self.x: Optional[str] = None


class _TableHistory:
    """Bounded per-table history of ``pptable`` messages, as plotly charts."""

    def __init__(self, max_points: int = _HISTORY_MAX, max_lines: int = _MAX_LINES) -> None:
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
        table.region, table.x = msg.get("region"), msg.get("x")
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
                values.append(None if value is None or abs(value) >= _NO_DATA else float(value))
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
        if table.region:  # a region's fields: its colour, in shades for further lines
            from neofoam_ui._ui._layout._palette import _REGION  # noqa: PLC0415

            color = _REGION.color(table.region)
            for i, line in enumerate(data):
                line["line"] = {"color": color, "dash": ("solid", "dash", "dot", "dashdot")[i % 4]}
                line["marker"] = {"size": 4, "color": color}
            layout["title"]["font"] = {"size": 13, "color": color}
        if table.x:
            layout["xaxis"]["title"] = {"text": table.x, "font": {"size": 10}, "standoff": 2}
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
