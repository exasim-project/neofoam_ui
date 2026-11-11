"""An evaluated table (``AggregatedDataSet``) as a ``pptable`` message."""

from __future__ import annotations

from typing import Any, Optional

__all__ = ()


def rows_message(name: str, time_value: float, result: Any, ms: Optional[float] = None
                 ) -> Optional[dict[str, Any]]:
    """An ``AggregatedDataSet`` as a ``pptable`` message; ``None`` for one without rows.

    Its ``headers`` / ``grouped_values`` put the value columns first and the group labels
    (a bin index, a residual's field and solver, …) after them; the message keeps the two
    apart, so the UI can draw one line per value column and group.
    """
    rows = list(getattr(result, "values", None) or ())
    if not rows:
        return None
    headers = list(result.headers)
    labels = list(getattr(rows[0], "group_name", None) or ())
    n_values = len(headers) - len(labels)
    values, groups = [], []
    for row in result.grouped_values:
        values.append([_number(v) for v in row[:n_values]])
        groups.append([_label(v) for v in row[n_values:]])
    message = _message(name, time_value, headers[:n_values], values, headers[n_values:], groups)
    if ms is not None:
        message["ms"] = round(ms, 3)
    return message


def table_row(name: str, time_value: float, columns: list[str], row: list[Optional[float]],
              **extra: Any) -> dict[str, Any]:
    """One ungrouped row of the stepper's own table ``name`` as a ``pptable`` message;
    ``extra`` adds keys (``region``; ``x``: the x axis's label when it is not the time)."""
    return {**_message(name, time_value, columns, [row], [], [[]]), **extra}


def _message(name: str, time_value: float, columns: list[str], values: list[list[Any]],
             group_columns: list[str], groups: list[list[Any]]) -> dict[str, Any]:
    return {"type": "pptable", "name": name, "time": float(time_value), "columns": columns,
            "group_columns": group_columns, "values": values, "groups": groups}


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None  # NaN/inf: no JSON


def _label(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value if isinstance(value, (int, float, str)) else str(value)
