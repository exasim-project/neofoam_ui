"""Solver worker process: runs a :class:`~neofoam_ui.engine.Session`, controlled over stdio.

Protocol — one JSON object per line.

UI → worker (stdin)::

    {"cmd": "step", "mode": "into|over|out|time_step|skip_init|run|run_to", "node": "<id>"}
    {"cmd": "pause"}                      # honoured between two events
    {"cmd": "breakpoints", "ids": [...]}
    {"cmd": "delay", "seconds": 0.2}      # animation while running (0: none)
    {"cmd": "watch", "on": true}          # the DAG pane is open: report the events of a run
    {"cmd": "wait", "on": true}           # "wait for UI": block until the UI acknowledged
    {"cmd": "ack", "seq": 7}              # ... the ``sync`` message 7 (and all before it)
    {"cmd": "view", "field": "U", "component": "mag|0|1|2", "on": true}   # field view
    {"cmd": "live", "on": true}           # field view frames while running ...
    {"cmd": "frames", "every": 1, "seconds": 0.5}   # ... every N time steps, >= s apart
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

The solver never waits for the UI: messages go through a queue to a writer thread,
and while the UI lags behind, the writer thins out what only shows progress — the
``log`` / ``running`` / ``stdout`` messages of a batch beyond the newest few (a ``skipped``
message counts what was left out). Everything else (``paused``, ``frame``, ``pptable``,
errors, ``stopped``, …) always goes out, in order. With "wait for UI" on nothing is thinned,
and the session blocks at every time step and frame on a ``sync`` message until the UI
answers it with an ``ack``: every event and frame is shown, the solver runs at the UI's pace.

Usage: ``python -m neofoam_ui.worker neofoam_ui.toy.heat1d:Heat1D``

Under ``mpirun`` only rank 0 speaks this protocol; see :mod:`neofoam_ui.parallel`.
"""

from __future__ import annotations

import collections
import importlib
import json
import os
import queue
import sys
import threading
import time
from typing import Any, Callable, Optional, TextIO

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


#: Messages that only show progress: thinned out while the UI lags behind (see :class:`_Out`).
LOSSY = {"log": 40, "running": 1, "stdout": 200}  # kept per batch: the newest n
#: Messages built for sending only (not shared with the session): serialised by the writer.
_FRESH = {*LOSSY, "pptable"}
#: Messages written at once; the others wait up to :data:`BATCH` s to go out together.
URGENT = {"paused", "sync", "stopped", "error", "log_error", "breakpoint", "frame", "graph"}
#: Seconds the writer (and the fd 1 pump) collect before they wake up again: every wake-up
#: takes the GIL from the solver thread, so a few dozen a second, not one per message.
BATCH = 0.02


class _Out:
    """Protocol messages onto the real stdout, written by a background thread.

    The caller never blocks on the pipe. The writer takes whatever queued up within
    :data:`BATCH` s (or until an :data:`URGENT` message) as one batch; with :attr:`thin` it
    keeps of each :data:`LOSSY` kind only the newest few (and says how many it skipped),
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
            if message["type"] in URGENT:
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
                self._ready.wait_for(lambda: self._urgent, BATCH)
                batch = list(self._queue)
                self._queue.clear()
                self._urgent = False
                self._idle = False
            try:
                lines = [m if isinstance(m, str) else _dumps(m) for m in thin(batch, self.thin)]
                self._stream.write("".join(line + "\n" for line in lines))
                self._stream.flush()
            except (OSError, ValueError):  # the UI went away; the reader thread ends the worker
                pass
            with self._ready:
                self._idle = True
                self._ready.notify_all()


def thin(batch: list[Any], on: bool = True) -> list[Any]:
    """``batch`` with only the newest :data:`LOSSY` messages of each kind and a ``skipped``
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
        # Chunks, not lines, and a pause after each: one wake-up per BATCH, not per line.
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
                time.sleep(BATCH)

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
    acks = _Acks(out, session)
    session.barrier = acks.barrier
    hub = None
    if size > 1:
        hub = Hub(size - 1)
        session._gen = relay(session._gen, hub, session)
        session.gather = hub.stats
        session.on_render = hub.render
        session.on_resume = hub.free
    commands: queue.Queue[dict[str, Any]] = queue.Queue()

    def handle(cmd: dict[str, Any]) -> None:
        # Pause / stop / breakpoints must reach a session that is busy running.
        kind = cmd["cmd"]
        if kind == "pause":
            session.pause_requested = True
        elif kind == "ack":
            acks.ack(int(cmd["seq"]))
        elif kind == "stop":
            session.stop_requested = True
            acks.wake()
            commands.put(cmd)
            # A solver stuck inside an operation (e.g. a collective whose peer died)
            # never returns to the command loop: exit anyway.
            timer = threading.Timer(STOP_GRACE, os._exit, (1,))
            timer.daemon = True
            timer.start()
        elif kind == "breakpoints":
            session.breakpoints = {str(i) for i in cmd["ids"]}
        elif kind == "delay":
            session.delay = max(0.0, float(cmd["seconds"]))
        elif kind == "watch":
            session.watch = bool(cmd["on"])
        elif kind == "wait":
            session.wait_ui = bool(cmd["on"])
            out.thin = not session.wait_ui
            acks.wake()
        elif kind == "frames":
            session.frame_every = max(1, int(cmd.get("every", session.frame_every)))
            session.frame_seconds = max(0.0, float(cmd.get("seconds", session.frame_seconds)))
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
            acks.close()
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
            out.drain()
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
