"""MPI runs: rank 0 talks to the UI, every other rank follows it event by event.

``mpirun -np N python -m neofoam_ui.worker ...`` starts one worker per rank.
Rank 0 is the ordinary worker (stdin/stdout protocol to the UI). The others
are *followers*: they connect to rank 0 over a socket (``NEOFOAM_UI_SYNC=host:port``,
set by the UI) and run the same solver, but only advance when rank 0 does —
one ``go`` per event — so all ranks pause at the same event and a step never
leaves a rank behind in a collective. In a free run (Run, Run to, Next time step)
every rank gets the same parameters once (``free``) and passes the events in
between on its own; only the time steps' loop tests, the target and the
breakpoints still take a ``go``. At each pause rank 0 collects the
followers' field statistics, so the UI shows global min / max / mean.

Rank 0 → follower, one JSON object per line::

    {"op": "go", "track": false}   # resume past the current event (tracking writes or not)
    {"op": "stats"}   # reply with Session.field_stats()
    {"op": "render", "params": {...}}   # in-situ frame: Session.render (collective)
    {"op": "free", "params": {...}}     # a free run: Session.set_free (null: none)
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
import time
import traceback
from typing import Any, Iterator, Optional

from neofoam_ui.engine import Event, Session
from neofoam_ui.protocol import SYNC_ENV

__all__ = ["SYNC_ENV", "Hub", "finalize_mpi", "finish", "follow", "rank_info", "relay"]


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


#: Seconds rank 0 waits for a follower's reply before dropping it.
REPLY_TIMEOUT = 60.0
#: Seconds a rank waits for MPI_Finalize on stop before exiting regardless.
FINALIZE_TIMEOUT = 10.0


def finalize_mpi() -> None:
    """``MPI_Finalize`` if the solver initialised MPI, so ``mpirun`` sees a clean exit.

    OpenFOAM finalizes in the argList destructor, which never runs here: the
    session holds the context until the process ends. Collective, so every rank
    calls it on ``stop``. Call it on the thread that initialised MPI
    (:meth:`Session.in_solver_thread`), with a timeout: a rank stuck in a
    collective never returns from it.
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
    lib.MPI_Finalize()


def finish(session: Session) -> bool:
    """Finalize MPI on the solver's thread; False if it did not complete in time."""
    return session.in_solver_thread(finalize_mpi, FINALIZE_TIMEOUT)


class Hub:
    """Rank 0's end: the connections to all followers."""

    def __init__(self, followers: int, timeout: float = 300.0) -> None:
        server = socket.create_server(_address())
        server.settimeout(timeout)
        self._peers: list[tuple[int, Any]] = []
        try:
            for _ in range(followers):
                conn, _ = server.accept()
                conn.settimeout(REPLY_TIMEOUT)
                stream = conn.makefile("rw", encoding="utf-8")
                hello = json.loads(stream.readline())
                self._peers.append((hello["rank"], stream))
        finally:
            server.close()
        self._peers.sort(key=lambda p: p[0])

    def _send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg) + "\n"
        for peer in list(self._peers):
            try:
                peer[1].write(line)
                peer[1].flush()
            except OSError:
                self._drop(peer, "is gone")

    def _drop(self, peer: tuple[int, Any], why: str) -> None:
        print(f"neofoam_ui: rank {peer[0]} {why}; no longer followed", file=sys.stderr, flush=True)
        self._peers.remove(peer)

    def go(self, track: bool = True) -> None:
        self._send({"op": "go", "track": track})

    def stats(self) -> list[dict[str, dict[str, Any]]]:
        """Each follower's field stats; a rank that is gone or does not answer is dropped."""
        self._send({"op": "stats"})
        replies = []
        for peer in list(self._peers):
            try:
                line = peer[1].readline()
            except OSError:  # incl. the reply timeout: its stream is out of step from now on
                line = ""
            if line:
                replies.append(json.loads(line))
            else:
                self._drop(peer, "did not answer")
        return replies

    def free(self, params: Optional[dict[str, Any]]) -> None:
        """The free-run parameters of rank 0 (:meth:`Session.set_free`), for every follower."""
        self._send({"op": "free", "params": params})

    def render(self, params: dict[str, str]) -> None:
        """Make every follower render the frame rank 0 is about to render."""
        self._send({"op": "render", "params": params})

    def stop(self) -> None:
        try:
            self._send({"op": "stop"})
        except OSError:
            pass


def relay(events: Iterator[Event], hub: Hub, session: Optional[Session] = None) -> Iterator[Event]:
    """Rank 0's event generator: release the followers before each resume.

    The followers track writes when rank 0 does (``session.stepping``): a free run costs
    them no checksums either.
    """
    while True:
        hub.go(session.stepping if session is not None else True)
        try:
            yield next(events)
        except StopIteration:
            return


class _Follower(Session):
    def _paused(self) -> None:  # nobody is listening
        pass

    def _log(self, *args: Any, **kwargs: Any) -> None:
        pass

    def _arm(self, *args: Any) -> None:  # rank 0 sets the free run (``free``), not each ``go``
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
        msg = json.loads(line)
        op = msg["op"]
        if session is None:  # created on the first message: rank 0 is ready by then
            session = _Follower(solver, emit)
            session.watch = False
        if op == "go":
            session.advance("into", track=bool(msg.get("track", True)))
        elif op == "stats":
            try:
                stats = session.field_stats()
            except Exception:  # noqa: BLE001 - never leave rank 0 waiting
                traceback.print_exc()
                stats = {}
            stream.write(json.dumps(stats) + "\n")
            stream.flush()
        elif op == "render":
            session.render(msg["params"])
        elif op == "free":
            session.set_free(msg["params"])
        elif op == "stop":
            finalized = True
            if session is not None:
                session.finalize_view()
                finalized = finish(session)
            # No static destructors after MPI_Finalize (ParaView's would still call MPI);
            # 1: stuck in a collective, let mpirun clean up.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0 if finalized else 1)
    return 1  # rank 0 went away
