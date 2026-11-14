"""The worker process: wiring, the command reader, and the main loop that runs the solver."""

from __future__ import annotations

import importlib
import json
import os
import queue
import sys
import threading
from typing import Any, Optional

import click

import neofoam_ui.worker
from neofoam_ui import catalyst
from neofoam_ui.config import CatalystConfig, load_config
from neofoam_ui.engine import Session
from neofoam_ui.parallel import rank_info
from neofoam_ui.parallel._follower import _follow
from neofoam_ui.parallel._hub import _Hub
from neofoam_ui.parallel._mpi import _finish
from neofoam_ui.parallel._ranks import _address
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


@click.command(help="The stepper's solver worker: runs SOLVER (module:Factory) under the stdio protocol.")
@click.argument("solver")
@click.option("--sync", metavar="HOST:PORT", help="where rank 0 listens for the other MPI ranks")
@click.option("--catalyst", "prefix", metavar="PREFIX", help="Catalyst 2 + ParaView install (over catalyst.prefix)")
@click.option("--data", metavar="HOST:PORT", help="Catalyst Live listener of the UI's data mode")
def _command(solver: str, sync: Optional[str], prefix: Optional[str], data: Optional[str]) -> int:
    settings = load_config().catalyst
    if prefix:
        settings = CatalystConfig.model_validate({**settings.model_dump(), "prefix": prefix})
    catalyst.use(settings, data)
    return _run(solver, sync)


def main(argv: list[str] | None = None) -> int:
    """Run the worker with the command line ``argv`` (``SOLVER [--sync] [--catalyst] [--data]``)."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(neofoam_ui.worker.__doc__, file=sys.stderr)
        return 2
    try:
        return _command.main(argv, prog_name="python -m neofoam_ui.worker", standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code


def _run(solver: str, sync_address: Optional[str]) -> int:
    rank, size = rank_info()
    if rank > 0:
        os.dup2(2, 1)  # mpirun merges every rank's stdout into rank 0's protocol stream
        return _follow(load_solver(solver), rank, _address(sync_address))

    out = _Out(os.fdopen(os.dup(1), "w"))
    sync = _capture_fd1(out)

    def emit(message: dict[str, Any]) -> None:
        if message["type"] in ("paused", "error"):
            sync()
        out(message)

    session = Session(load_solver(solver), emit)
    catalyst.set_frame_driver(session.solver_frame)
    acks = _Acks(out, session)
    session.barrier = acks.barrier
    hub = None
    if size > 1:
        hub = _Hub(size - 1, _address(sync_address))
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
