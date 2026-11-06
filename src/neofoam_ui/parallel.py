"""MPI runs: rank 0 talks to the UI, every other rank follows it event by event.

``mpirun -np N python -m neofoam_ui.worker ...`` starts one worker per rank.
Rank 0 is the ordinary worker (stdin/stdout protocol to the UI). The others
are *followers*: they connect to rank 0 over a socket (``NEOFOAM_UI_SYNC=host:port``,
set by the UI) and run the same solver, but only advance when rank 0 does —
one ``go`` per event — so all ranks pause at the same event and a step never
leaves a rank behind in a collective. At each pause rank 0 collects the
followers' field statistics, so the UI shows global min / max / mean.

Rank 0 → follower, one JSON object per line::

    {"op": "go"}      # resume past the current event
    {"op": "stats"}   # reply with Session.field_stats()
    {"op": "stop"}

The rank is read from the launcher's environment (Open MPI, MPICH / PMI);
nothing here calls MPI.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import socket
import sys
import threading
import time
import traceback
from typing import Any, Iterator, Optional

from neofoam_ui.engine import Event, Session

__all__ = ["SYNC_ENV", "Hub", "finalize_mpi", "follow", "rank_info", "relay"]

SYNC_ENV = "NEOFOAM_UI_SYNC"

_RANK_ENV = (
    ("OMPI_COMM_WORLD_RANK", "OMPI_COMM_WORLD_SIZE"),
    ("PMIX_RANK", "PMIX_SIZE"),
    ("PMI_RANK", "PMI_SIZE"),
)


def rank_info() -> tuple[int, int]:
    """``(rank, size)`` of this process; ``(0, 1)`` outside MPI."""
    for rank, size in _RANK_ENV:
        if rank in os.environ and size in os.environ:
            return int(os.environ[rank]), int(os.environ[size])
    return 0, 1


def _address() -> tuple[str, int]:
    value = os.environ.get(SYNC_ENV)
    if not value:
        raise RuntimeError(f"an MPI run of the stepper needs {SYNC_ENV}=host:port")
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)


def finalize_mpi(timeout: float = 10.0) -> None:
    """``MPI_Finalize`` if the solver initialised MPI, so ``mpirun`` sees a clean exit.

    OpenFOAM finalizes in the argList destructor, which never runs here: the
    session holds the context until the process ends. Collective, so every rank
    calls it on ``stop``; a rank stuck in a collective must not hang the exit.
    """
    name = ctypes.util.find_library("mpi")
    if name is None:
        return
    try:
        lib = ctypes.CDLL(name)  # the copy OpenFOAM's Pstream already loaded
    except OSError:
        return
    flag = ctypes.c_int(0)
    lib.MPI_Initialized(ctypes.byref(flag))
    if not flag.value:
        return
    lib.MPI_Finalized(ctypes.byref(flag))
    if flag.value:
        return
    done = threading.Thread(target=lib.MPI_Finalize, daemon=True)
    done.start()
    done.join(timeout)


class Hub:
    """Rank 0's end: the connections to all followers."""

    def __init__(self, followers: int, timeout: float = 300.0) -> None:
        server = socket.create_server(_address())
        server.settimeout(timeout)
        self._peers: list[tuple[int, Any]] = []
        try:
            for _ in range(followers):
                conn, _ = server.accept()
                stream = conn.makefile("rw", encoding="utf-8")
                hello = json.loads(stream.readline())
                self._peers.append((hello["rank"], stream))
        finally:
            server.close()
        self._peers.sort(key=lambda p: p[0])

    def _send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg) + "\n"
        for _, stream in self._peers:
            stream.write(line)
            stream.flush()

    def go(self) -> None:
        self._send({"op": "go"})

    def stats(self) -> list[dict[str, dict[str, Any]]]:
        self._send({"op": "stats"})
        lines = [stream.readline() for _, stream in self._peers]
        return [json.loads(line) for line in lines if line]  # "" = that rank is gone

    def stop(self) -> None:
        try:
            self._send({"op": "stop"})
        except OSError:
            pass


def relay(events: Iterator[Event], hub: Hub) -> Iterator[Event]:
    """Rank 0's event generator: release the followers before each resume."""
    while True:
        hub.go()
        try:
            yield next(events)
        except StopIteration:
            return


class _Follower(Session):
    def _paused(self) -> None:  # nobody is listening
        pass


def follow(solver: Any, rank: int, timeout: float = 300.0) -> int:
    """Run ``solver`` as follower ``rank`` until rank 0 says stop."""
    address = _address()
    deadline = time.monotonic() + timeout
    while True:  # rank 0 may not be listening yet
        try:
            conn = socket.create_connection(address, timeout=5)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)
    conn.settimeout(None)
    stream = conn.makefile("rw", encoding="utf-8")
    stream.write(json.dumps({"rank": rank}) + "\n")
    stream.flush()

    def emit(msg: dict[str, Any]) -> None:
        if msg["type"] == "error":
            print(f"[rank {rank}] {msg['traceback']}", file=sys.stderr, flush=True)

    session: Optional[_Follower] = None
    for line in stream:
        op = json.loads(line)["op"]
        if session is None:  # created on the first message: rank 0 is ready by then
            session = _Follower(solver, emit)
        if op == "go":
            session.advance("into")
        elif op == "stats":
            try:
                stats = session.field_stats()
            except Exception:  # noqa: BLE001 - never leave rank 0 waiting
                traceback.print_exc()
                stats = {}
            stream.write(json.dumps(stats) + "\n")
            stream.flush()
        elif op == "stop":
            finalize_mpi()
            return 0
    return 1  # rank 0 went away
