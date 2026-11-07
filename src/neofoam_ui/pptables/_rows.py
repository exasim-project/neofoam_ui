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
    message: dict[str, Any] = {
        "type": "pptable", "name": name, "time": float(time_value),
        "columns": headers[:n_values], "group_columns": headers[n_values:],
        "values": values, "groups": groups,
    }
    if ms is not None:
        message["ms"] = round(ms, 3)
    return message


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
