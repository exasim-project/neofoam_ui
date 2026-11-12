"""A rank other than 0: runs the same solver, advancing only when rank 0 says ``go``."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
from typing import Any, Optional

from neofoam_ui import catalyst
from neofoam_ui.engine import Session
from neofoam_ui.parallel._mpi import _finish
from neofoam_ui.parallel._ranks import _address

__all__ = ()


def _follow(solver: Any, rank: int, timeout: float = 300.0) -> int:
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

    session: Optional[Session] = None
    for line in stream:
        msg = json.loads(line)
        op = msg["op"]
        if session is None:  # created on the first message: rank 0 is ready by then
            session = Session(solver, emit, follower=True)
            session.watch = False
            catalyst.set_frame_driver(session.solver_frame)
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
                finalized = _finish(session)
            # No static destructors after MPI_Finalize (ParaView's would still call MPI);
            # 1: stuck in a collective, let mpirun clean up.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0 if finalized else 1)
    return 1  # rank 0 went away
