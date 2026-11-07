"""\"Wait for UI\": the session blocks on a ``sync`` message until the UI's ``ack``."""

from __future__ import annotations

import threading
from typing import Optional

from neofoam_ui.engine import Session
from neofoam_ui.worker._out import _Out

__all__ = ()


class _Acks:
    """"Wait for UI": :meth:`barrier` sends ``sync`` and blocks until the UI's ``ack``."""

    def __init__(self, out: _Out, session: Optional[Session] = None) -> None:
        self._out = out
        self.session = session
        self._sent = 0
        self._acked = 0
        self._closed = False
        self._cond = threading.Condition()

    def barrier(self) -> None:
        with self._cond:
            self._sent += 1
            seq = self._sent
        self._out({"type": "sync", "seq": seq})
        with self._cond:
            # Not past a stop, nor when "wait" was switched off meanwhile.
            self._cond.wait_for(lambda: self._acked >= seq or self._closed or self.session is None
                                or self.session.stop_requested or not self.session.wait_ui)

    def ack(self, seq: int) -> None:
        with self._cond:
            self._acked = max(self._acked, seq)
            self._cond.notify_all()

    def wake(self) -> None:
        """Re-check the conditions (stop or "wait" off)."""
        with self._cond:
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
