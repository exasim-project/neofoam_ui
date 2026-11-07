"""Rank 0's end of an MPI run: the socket connections to every follower."""

from __future__ import annotations

import json
import socket
import sys
from typing import Any, Optional

from neofoam_ui.parallel._ranks import _address

__all__ = ()

#: Seconds rank 0 waits for a follower's reply before dropping it.
_REPLY_TIMEOUT = 60.0


class _Hub:
    """Rank 0's end: the connections to all followers (the session's :class:`~neofoam_ui.engine.Peers`)."""

    def __init__(self, followers: int, timeout: float = 300.0) -> None:
        server = socket.create_server(_address())
        server.settimeout(timeout)
        self._peers: list[tuple[int, Any]] = []
        try:
            for _ in range(followers):
                conn, _ = server.accept()
                conn.settimeout(_REPLY_TIMEOUT)
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

    # -- engine.Peers ----------------------------------------------------------------

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
