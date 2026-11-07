"""Worker messages to their handlers: the app's first, then each pane's, in pane order."""

from __future__ import annotations

from typing import Any, Callable, Mapping

__all__ = ()

_Handler = Callable[[dict[str, Any]], None]


class _Dispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, list[_Handler]] = {}

    def add(self, handlers: Mapping[str, _Handler]) -> None:
        for kind, handler in handlers.items():
            self._handlers.setdefault(kind, []).append(handler)

    def __call__(self, msg: dict[str, Any]) -> None:
        for handler in self._handlers.get(msg["type"], ()):
            handler(msg)
