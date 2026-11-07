"""Field statistics: per rank (mergeable), merged over the MPI ranks, and as table rows."""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from neofoam_ui.engine._tracking import _Tracker

__all__ = ()

_Stats = dict[str, dict[str, Any]]


def _field_stats(fields: dict[str, Any], tracker: _Tracker) -> _Stats:
    """Per field: min / max / sum / cell count and who last wrote it (mergeable across ranks)."""
    stats: _Stats = {}
    for name, arr in fields.items():
        a = np.asarray(arr, dtype=float)
        label = name
        if a.ndim == 2:  # vector/tensor field: summarise the magnitude
            a = np.linalg.norm(a, axis=1)
            label = f"|{name}|"
        stats[name] = {
            "label": label,
            "min": float(a.min()) if a.size else None,
            "max": float(a.max()) if a.size else None,
            "sum": float(a.sum()),
            "n": int(a.size),
            "written_by": tracker.written_by(name),
            "changed": name in tracker.last_writes,
        }
    return stats


def _merge(stats: _Stats, others: Iterable[_Stats]) -> _Stats:
    """``stats`` (this rank's, updated in place) merged with the other ranks' stats."""
    for other in others:
        for name, theirs in other.items():
            if name not in stats:
                stats[name] = dict(theirs)
                continue
            mine = stats[name]
            values = [v for v in (mine["min"], theirs["min"]) if v is not None]
            mine["min"] = min(values) if values else None
            values = [v for v in (mine["max"], theirs["max"]) if v is not None]
            mine["max"] = max(values) if values else None
            mine["sum"] += theirs["sum"]
            mine["n"] += theirs["n"]
            mine["changed"] = mine["changed"] or theirs["changed"]
    return stats


def _rows(stats: _Stats) -> list[dict[str, Any]]:
    """The variables table of the ``paused`` message."""
    return [
        {
            "field": name,
            "name": st["label"],
            "min": st["min"] if st["min"] is not None else float("nan"),
            "max": st["max"] if st["max"] is not None else float("nan"),
            "mean": st["sum"] / st["n"] if st["n"] else float("nan"),
            "written_by": st["written_by"],
            "changed": st["changed"],
        }
        for name, st in stats.items()
    ]
