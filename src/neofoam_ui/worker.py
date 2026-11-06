"""Solver worker process: runs a :class:`~neofoam_ui.engine.Session`, controlled over stdio.

Protocol — one JSON object per line.

UI → worker (stdin)::

    {"cmd": "step", "mode": "into|over|out|time_step|skip_init|run|run_to", "node": "<id>"}
    {"cmd": "pause"}                      # honoured between two events
    {"cmd": "breakpoints", "ids": [...]}
    {"cmd": "delay", "seconds": 0.2}      # animation while running
    {"cmd": "stop"}

worker → UI (stdout): the engine messages (``graph``, ``log``, ``paused``,
``running``, ``breakpoint``, ``error``) plus ``stdout`` for anything the solver
prints. File descriptor 1 is redirected into a pipe, so neither Python ``print``
nor C++ output (OpenFOAM ``Info``) can corrupt the protocol stream, which uses a
duplicate of the original stdout.

Usage: ``python -m neofoam_ui.worker neofoam_ui.toy.heat1d:Heat1D``

Under ``mpirun`` only rank 0 speaks this protocol; see :mod:`neofoam_ui.parallel`.
"""

from __future__ import annotations

import importlib
import json
import os
import queue
import sys
import threading
from typing import Any, Callable, TextIO

from neofoam_ui.engine import Session
from neofoam_ui.parallel import Hub, finalize_mpi, follow, rank_info, relay

__all__ = ["load_solver", "main"]


def load_solver(spec: str) -> Any:
    """``"package.module:Factory"`` → ``Factory()``."""
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"solver spec must be 'module:Factory', got {spec!r}")
    return getattr(importlib.import_module(module_name), attr)()


class _Out:
    """Serialises protocol messages onto the real stdout."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def __call__(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, default=_jsonable)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


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
        with os.fdopen(read_fd, "r", errors="replace") as pipe:
            for line in pipe:
                text = line.rstrip("\n")
                if text.endswith(_SYNC):  # output without a trailing newline precedes it
                    text = text[: -len(_SYNC)]
                    if text:
                        out({"type": "stdout", "text": text})
                    synced.set()
                    continue
                out({"type": "stdout", "text": text})

    threading.Thread(target=pump, daemon=True).start()

    def sync() -> None:
        synced.clear()
        sys.stdout.flush()
        os.write(1, (_SYNC + "\n").encode())
        synced.wait(timeout=5)

    return sync


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    rank, size = rank_info()
    if rank > 0:
        os.dup2(2, 1)  # mpirun merges every rank's stdout into rank 0's protocol stream
        return follow(load_solver(argv[0]), rank)

    out = _Out(os.fdopen(os.dup(1), "w"))
    sync = _capture_fd1(out)

    def emit(message: dict[str, Any]) -> None:
        if message["type"] in ("paused", "error"):
            sync()
        out(message)

    session = Session(load_solver(argv[0]), emit)
    hub = None
    if size > 1:
        hub = Hub(size - 1)
        session._gen = relay(session._gen, hub)
        session.gather = hub.stats
    commands: queue.Queue[dict[str, Any]] = queue.Queue()

    def read_commands() -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            cmd = json.loads(line)
            # Pause / stop / breakpoints must reach a session that is busy running.
            if cmd["cmd"] == "pause":
                session.pause_requested = True
            elif cmd["cmd"] == "stop":
                session.stop_requested = True
                commands.put(cmd)
            elif cmd["cmd"] == "breakpoints":
                session.breakpoints = set(cmd["ids"])
            elif cmd["cmd"] == "delay":
                session.delay = float(cmd["seconds"])
            else:
                commands.put(cmd)
        commands.put({"cmd": "stop"})  # UI went away

    threading.Thread(target=read_commands, daemon=True).start()
    session.start()
    while True:
        cmd = commands.get()
        if cmd["cmd"] == "stop":
            if hub is not None:
                hub.stop()
                finalize_mpi()
            out({"type": "stopped"})
            return 0
        if cmd["cmd"] == "step":
            session.advance(cmd["mode"], cmd.get("node"))


if __name__ == "__main__":
    sys.exit(main())
