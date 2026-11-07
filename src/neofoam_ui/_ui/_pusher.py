"""State pushes to the browser at a capped rate, and the console lines buffered in between."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from neofoam_ui.protocol import UI_URGENT

__all__ = ()

#: Lines the console keeps (newest first).
_CONSOLE_MAX = 500
#: Seconds between two state pushes to the browser while messages stream in.
_PUSH_EVERY = 1 / 15


class _Pusher:
    """Applies what was buffered and flushes the state: at once for urgent messages, else
    at most every :data:`_PUSH_EVERY` s (a timer catches the last of a burst)."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._lines: list[dict[str, str]] = []
        #: Called at every push, before the flush (panes apply their own buffers).
        self.before_push: list[Callable[[], None]] = []
        self._pushed = 0.0
        self._later: Optional[asyncio.TimerHandle] = None

    def console(self, text: str, cls: str = "") -> None:
        """Add a console line (shown at the next :meth:`push`)."""
        self._lines.append({"t": text, "c": cls})
        if len(self._lines) > _CONSOLE_MAX:
            del self._lines[: len(self._lines) - _CONSOLE_MAX]

    def clear(self) -> None:
        self._lines = []

    def after(self, kind: str) -> None:
        """A message of type ``kind`` was handled: push now, or soon."""
        if kind in UI_URGENT or time.monotonic() - self._pushed >= _PUSH_EVERY:
            self.push()
        elif self._later is None:
            self._later = asyncio.get_running_loop().call_later(_PUSH_EVERY, self.push)

    def push(self) -> None:
        """Apply the buffered console lines (and the panes' buffers), and push the state."""
        if self._later is not None:
            self._later.cancel()
            self._later = None
        self._pushed = time.monotonic()
        s = self._state
        if self._lines:
            s.logs = (self._lines[::-1] + list(s.logs))[:_CONSOLE_MAX]
            self._lines = []
        for apply in self.before_push:
            apply()
        s.flush()
