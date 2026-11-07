"""Hook a ``PostProcessor`` from the outside: tee every table's rows into ``pptable`` messages."""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable

from neofoam_ui.pptables._describe import describe_table
from neofoam_ui.pptables._rows import rows_message

__all__ = ()

_Emit = Callable[[dict[str, Any]], None]


def observe(processor: Any, emit: _Emit) -> bool:
    """Tee the rows ``processor`` writes into ``emit``; False if it is not a PostProcessor.

    The processor opens a private ``(write_control, writer)`` per table on its first
    write (``_open``); the copies are wrapped there, so every table — whoever declared
    it — is seen, and the CSV files are written exactly as before. The time between the
    write control saying "due" and the writer receiving the result is the table's
    compute time.
    """
    tables = getattr(processor, "_tables", None)
    opener = getattr(processor, "_open", None)
    if tables is None or opener is None:
        return False
    try:
        emit({"type": "pptables", "tables": [describe_table(t) for t in tables]})
    except Exception:  # noqa: BLE001 - no diagram rather than no solver
        emit({"type": "stdout", "text": "post-processing tables: " + traceback.format_exc()})
    started: dict[str, float] = {}

    def wrap(runtime: dict[str, Any]) -> dict[str, Any]:
        return {name: rt._replace(write_control=_Due(rt.write_control, name, started),
                                  writer=_Tee(rt.writer, name, started, emit))
                for name, rt in runtime.items()}

    def opened(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return wrap(opener(*args, **kwargs))

    processor._open = opened
    if getattr(processor, "_runtime", None):  # already opened (observed late)
        processor._runtime = wrap(processor._runtime)
    return True


class _Due:
    """A table's write control; notes when it fires, so the compute can be timed."""

    def __init__(self, inner: Any, name: str, started: dict[str, float]) -> None:
        self._inner, self._name, self._started = inner, name, started

    def should_write(self, *args: Any, **kwargs: Any) -> Any:
        due = self._inner.should_write(*args, **kwargs)
        if due:
            self._started[self._name] = time.perf_counter()
        return due

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)


class _Tee:
    """A table's writer: writes as before, then emits the rows it was given."""

    def __init__(self, inner: Any, name: str, started: dict[str, float], emit: _Emit) -> None:
        self._inner, self._name, self._started, self._emit = inner, name, started, emit
        self._failed = False

    def write(self, time_value: float, result: Any) -> Any:
        t0 = self._started.pop(self._name, None)
        ms = (time.perf_counter() - t0) * 1e3 if t0 is not None else None
        out = self._inner.write(time_value, result)
        if not self._failed:
            try:
                message = rows_message(self._name, time_value, result, ms)
                if message is not None:
                    self._emit(message)
            except Exception:  # noqa: BLE001 - reported once; the CSV is still written
                self._failed = True
                self._emit({"type": "stdout", "text": f"post-processing table {self._name!r}: "
                                                      + traceback.format_exc()})
        return out

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)
