"""What ran, how long, and which fields each action wrote."""

from __future__ import annotations

import time
import zlib
from typing import Any, Callable

import numpy as np

__all__ = ()

#: ``written_by`` of a field changed by a free run (its writer was not tracked).
UNTRACKED = "not tracked while running"


def _fingerprint(arr: np.ndarray) -> int:
    return zlib.crc32(np.ascontiguousarray(arr).tobytes())


class _Tracker:
    """Timing and call counts per node; while single-stepping, the fields each action changed.

    Changes are seen by a checksum of every field after every action (:meth:`track_writes`),
    so a free run does not track (:meth:`stop`): its writers are :data:`UNTRACKED`, and the
    checksums are refreshed when tracking starts again (:meth:`start`).
    """

    def __init__(self, fields: Callable[[Any], dict[str, np.ndarray]]) -> None:
        self._fields = fields
        self.calls: dict[str, int] = {}
        self.last_ms: dict[str, float] = {}
        #: Summed time per node over the whole run (timing heat map).
        self.total_ms: dict[str, float] = {}
        #: Fields each action was seen to change (a field fingerprint differed afterwards).
        self.writes_seen: dict[str, set[str]] = {}
        self.last_written_by: dict[str, str] = {}
        self.last_writes: list[str] = []
        #: A free run changed fields without tracking: their writer is :data:`UNTRACKED`.
        self.untracked = False
        self._prints: dict[str, int] = {}
        self._stale = False

    # -- timing ---------------------------------------------------------------------------

    def timed(self, node: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        out = fn()
        self.count(node, (time.perf_counter() - t0) * 1e3)
        return out

    def count(self, node: str, ms: float) -> None:
        self.last_ms[node] = ms
        self.total_ms[node] = self.total_ms.get(node, 0.0) + ms
        self.calls[node] = self.calls.get(node, 0) + 1

    # -- writes ---------------------------------------------------------------------------

    def _field_prints(self, ctx: Any) -> dict[str, int]:
        return {k: _fingerprint(v) for k, v in self._fields(ctx).items()}

    def new_context(self, ctx: Any, tracking: bool) -> None:
        """The run context exists (or changed): checksum it now, or once tracking starts."""
        if tracking:
            self._prints = self._field_prints(ctx)
        else:
            self._stale = True

    def track_writes(self, ctx: Any, node: str, op_name: str) -> None:
        """After action ``node`` ran: which fields it changed."""
        now = self._field_prints(ctx)
        self.last_writes = [k for k, v in now.items() if self._prints.get(k) != v]
        for k in self.last_writes:
            self.last_written_by[k] = op_name
        if self.last_writes:
            self.writes_seen.setdefault(node, set()).update(self.last_writes)
        self._prints = now

    def stop(self) -> None:
        """A free run starts: no checksums, and what it writes is untracked."""
        self._stale = True
        self.untracked = True
        self.last_writes = []
        self.last_written_by.clear()  # stale: the run will overwrite fields untracked

    def start(self, ctx: Any) -> None:
        """Single steps again: fresh checksums after a free run."""
        if self._stale and ctx is not None:
            self._prints = self._field_prints(ctx)
            self._stale = False

    def written_by(self, name: str) -> str:
        return self.last_written_by.get(name, UNTRACKED if self.untracked else "init")
