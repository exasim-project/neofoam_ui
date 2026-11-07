"""File descriptor 1 as ``stdout`` messages, and leaving without static destructors."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable

from neofoam_ui.worker._out import _BATCH, _Out

__all__ = ()

_SYNC = "\x00neofoam_ui-sync\x00"


def _capture_fd1(out: _Out) -> Callable[[], None]:
    """Route everything written to fd 1 into ``stdout`` messages.

    Returns ``sync()``: blocks until everything written to fd 1 so far has been
    forwarded, so solver output precedes the ``paused`` message that follows it.
    """
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 1)
    os.close(write_fd)
    sys.stdout = os.fdopen(1, "w", buffering=1, closefd=False)
    synced = threading.Event()

    def pump() -> None:
        # Chunks, not lines, and a pause after each: one wake-up per _BATCH, not per line.
        pending = b""
        while True:
            chunk = os.read(read_fd, 1 << 16)
            if not chunk:
                return
            *lines, pending = (pending + chunk).split(b"\n")
            for raw in lines:
                text = raw.decode(errors="replace")
                if text.endswith(_SYNC):  # output without a trailing newline precedes it
                    text = text[: -len(_SYNC)]
                    if text:
                        out({"type": "stdout", "text": text})
                    synced.set()
                    continue
                out({"type": "stdout", "text": text})
            if not synced.is_set():
                time.sleep(_BATCH)

    threading.Thread(target=pump, daemon=True).start()

    def sync() -> None:
        synced.clear()
        sys.stdout.flush()
        os.write(1, (_SYNC + "\n").encode())
        synced.wait(timeout=5)

    return sync


def _exit(code: int) -> None:
    """Leave without static destructors: after MPI_Finalize, ParaView's would still call MPI."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
