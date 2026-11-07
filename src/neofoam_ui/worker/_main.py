"""The worker process: wiring, the command reader, and the main loop that runs the solver."""

from __future__ import annotations

import importlib
import json
import os
import queue
import sys
import threading
from typing import Any

from neofoam_ui.engine import Session
from neofoam_ui.parallel import rank_info
from neofoam_ui.parallel._follower import _follow
from neofoam_ui.parallel._hub import _Hub
from neofoam_ui.parallel._mpi import _finish
from neofoam_ui.worker._acks import _Acks
from neofoam_ui.worker._commands import _handle, _Worker
from neofoam_ui.worker._out import _Out
from neofoam_ui.worker._stdio import _capture_fd1, _exit

__all__ = ()


def load_solver(spec: str) -> Any:
    """``"package.module:Factory"`` → ``Factory()``."""
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"solver spec must be 'module:Factory', got {spec!r}")
    return getattr(importlib.import_module(module_name), attr)()


def main(argv: list[str] | None = None) -> int:
    """Run the solver ``argv[0]`` (``module:Factory``) under the stdio protocol."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        import neofoam_ui.worker  # noqa: PLC0415

        print(neofoam_ui.worker.__doc__, file=sys.stderr)
        return 2
    rank, size = rank_info()
    if rank > 0:
        os.dup2(2, 1)  # mpirun merges every rank's stdout into rank 0's protocol stream
        return _follow(load_solver(argv[0]), rank)

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
        hub = _Hub(size - 1)
        session.set_peers(hub)
    worker = _Worker(session, out, acks, queue.Queue())

    def read_commands() -> None:
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    _handle(worker, json.loads(line))
                except (ValueError, KeyError, TypeError) as exc:  # a bad command, not a dead UI
                    out({"type": "log_error", "text": f"bad command {line.strip()!r}: {exc!r}"})
        finally:
            acks.close()
            worker.queue.put({"cmd": "stop"})  # UI went away (or the reader died)

    threading.Thread(target=read_commands, daemon=True).start()
    session.start()
    while True:
        cmd = worker.queue.get()
        if cmd["cmd"] == "stop":
            if hub is not None:
                hub.stop()  # first: the followers finalize together with this rank
            session.finalize_view()
            finalized = _finish(session)  # also when ParaView started MPI in a serial run
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
