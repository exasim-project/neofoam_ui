"""Solver worker process: runs a :class:`~neofoam_ui.engine.Session`, controlled over stdio.

Protocol — one JSON object per line.

UI → worker (stdin)::

    {"cmd": "step", "mode": "into|over|out|time_step|skip_init|run|run_to", "node": "<id>"}
    {"cmd": "pause"}                      # honoured between two events
    {"cmd": "breakpoints", "ids": [...]}
    {"cmd": "delay", "seconds": 0.2}      # animation while running
    {"cmd": "view", "field": "U", "component": "mag|0|1|2", "on": true}   # field view
    {"cmd": "live", "on": true}           # field view frames at every time step while running
    {"cmd": "stop"}

worker → UI (stdout): the engine messages (``graph``, ``log``, ``paused``,
``running``, ``breakpoint``, ``error``, and for an open field view a ``frame`` —
the name of the image Catalyst rendered in situ — at each pause where the values
changed; with ``pipeline_ms`` and, when it changed, the ``pipeline`` diagram; for NeoFOAM's
post-processing tables ``pptables`` once and a ``pptable`` row set per table and step, see
:mod:`neofoam_ui.pptables`) plus ``stdout`` for anything the solver
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
from neofoam_ui.parallel import Hub, finish, follow, rank_info, relay

#: Seconds after a ``stop`` before the worker exits even if the solver is stuck.
STOP_GRACE = 20.0

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


def _exit(code: int) -> None:
    """Leave without static destructors: after MPI_Finalize, ParaView's would still call MPI."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


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
        session.on_render = hub.render
    commands: queue.Queue[dict[str, Any]] = queue.Queue()

    def handle(cmd: dict[str, Any]) -> None:
        # Pause / stop / breakpoints must reach a session that is busy running.
        kind = cmd["cmd"]
        if kind == "pause":
            session.pause_requested = True
        elif kind == "stop":
            session.stop_requested = True
            commands.put(cmd)
            # A solver stuck inside an operation (e.g. a collective whose peer died)
            # never returns to the command loop: exit anyway.
            timer = threading.Timer(STOP_GRACE, os._exit, (1,))
            timer.daemon = True
            timer.start()
        elif kind == "breakpoints":
            session.breakpoints = {str(i) for i in cmd["ids"]}
        elif kind == "delay":
            session.delay = float(cmd["seconds"])
        elif kind == "step":
            commands.put({"cmd": "step", "mode": str(cmd["mode"]), "node": cmd.get("node")})
        elif kind == "view":
            # Takes effect at the next live frame of a run; the queued command renders it
            # right away when paused (and is a no-op after a run already showed it).
            session.request_view(cmd.get("field"), str(cmd.get("component", "mag")), bool(cmd.get("on", True)))
            commands.put({"cmd": "view"})
        elif kind == "live":
            session.live_view = bool(cmd["on"])
        else:
            raise ValueError(f"unknown command {kind!r}")

    def read_commands() -> None:
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    handle(json.loads(line))
                except (ValueError, KeyError, TypeError) as exc:  # a bad command, not a dead UI
                    out({"type": "log_error", "text": f"bad command {line.strip()!r}: {exc!r}"})
        finally:
            commands.put({"cmd": "stop"})  # UI went away (or the reader died)

    threading.Thread(target=read_commands, daemon=True).start()
    session.start()
    while True:
        cmd = commands.get()
        if cmd["cmd"] == "stop":
            if hub is not None:
                hub.stop()  # first: the followers finalize together with this rank
            session.finalize_view()
            finalized = finish(session)  # also when ParaView started MPI in a serial run
            out({"type": "stopped"})
            _exit(0 if finalized else 1)  # 1: stuck in a collective, let mpirun clean up
        if cmd["cmd"] == "step":
            try:
                session.advance(cmd["mode"], cmd.get("node"))
            except ValueError as exc:  # unknown step mode
                out({"type": "log_error", "text": str(exc)})
        elif cmd["cmd"] == "view":
            session.send_view()


if __name__ == "__main__":
    sys.exit(main())
