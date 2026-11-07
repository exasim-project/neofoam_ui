"""Protocol messages onto the real stdout, written by a background thread (never blocking)."""

from __future__ import annotations

import collections
import json
import threading
from typing import Any, TextIO

from neofoam_ui.protocol import LOSSY, WORKER_URGENT

__all__ = ()

#: Messages built for sending only (not shared with the session): serialised by the writer.
_FRESH = {*LOSSY, "pptable"}
#: Seconds the writer (and the fd 1 pump) collect before they wake up again: every wake-up
#: takes the GIL from the solver thread, so a few dozen a second, not one per message.
_BATCH = 0.02


class _Out:
    """Protocol messages onto the real stdout, written by a background thread.

    The caller never blocks on the pipe. The writer takes whatever queued up within
    :data:`_BATCH` s (or until a :data:`~neofoam_ui.protocol.WORKER_URGENT` message) as one batch;
    with :attr:`thin` it
    keeps of each :data:`~neofoam_ui.protocol.LOSSY` kind only the newest few (and says how many it
    skipped),
    all other messages always.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._queue: collections.deque[Any] = collections.deque()
        self._ready = threading.Condition()
        self._idle = True
        self._urgent = False
        #: Thin out progress messages of a batch (off: "wait for UI", every message goes out).
        self.thin = True
        threading.Thread(target=self._write, name="protocol-out", daemon=True).start()

    def __call__(self, message: dict[str, Any]) -> None:
        # Lossy messages and table rows are fresh dicts: serialised by the writer (or never).
        # The others may hold the session's live dicts (``calls``): serialised now, as they are.
        item = message if message["type"] in _FRESH else _dumps(message)
        with self._ready:
            self._queue.append(item)
            if message["type"] in WORKER_URGENT:
                self._urgent = True
                self._ready.notify()
            elif len(self._queue) == 1:  # wakes the writer, which then collects for a moment
                self._ready.notify()

    def drain(self, timeout: float = 10.0) -> None:
        """Block until everything queued so far is written (before the process exits)."""
        with self._ready:
            self._urgent = True
            self._ready.notify_all()
            self._ready.wait_for(lambda: not self._queue and self._idle, timeout)

    def _write(self) -> None:
        while True:
            with self._ready:
                self._ready.wait_for(lambda: self._queue)
                self._ready.wait_for(lambda: self._urgent, _BATCH)
                batch = list(self._queue)
                self._queue.clear()
                self._urgent = False
                self._idle = False
            try:
                lines = [m if isinstance(m, str) else _dumps(m) for m in _thin(batch, self.thin)]
                self._stream.write("".join(line + "\n" for line in lines))
                self._stream.flush()
            except (OSError, ValueError):  # the UI went away; the reader thread ends the worker
                pass
            with self._ready:
                self._idle = True
                self._ready.notify_all()


def _thin(batch: list[Any], on: bool = True) -> list[Any]:
    """``batch`` with only the newest :data:`~neofoam_ui.protocol.LOSSY` messages of each kind and a ``skipped``
    count (of ``log`` / ``stdout``) where the first was left out; all other messages are
    always kept."""
    if not on:
        return batch
    counts = collections.Counter(m["type"] for m in batch if isinstance(m, dict) and m["type"] in LOSSY)
    if all(n <= LOSSY[k] for k, n in counts.items()):
        return batch
    skip = {k: n - LOSSY[k] for k, n in counts.items() if n > LOSSY[k]}
    told = {k: n for k, n in skip.items() if k != "running"}  # an older token position: no news
    out: list[Any] = []
    left = dict(skip)
    for m in batch:
        if isinstance(m, dict) and left.get(m["type"], 0) > 0:
            if told and left == skip:
                out.append({"type": "skipped", **told})
            left[m["type"]] -= 1
            continue
        out.append(m)
    return out


def _dumps(message: dict[str, Any]) -> str:
    return json.dumps(message, default=_jsonable)



def _jsonable(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)
