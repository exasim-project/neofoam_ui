"""The UI's commands (worker stdin): each handled on the reader thread at once, and the ones
that run the solver queued for the main loop.

Pause / stop / breakpoints / delay / ... must reach a session that is busy running, so they
are applied right here; ``step`` and ``view`` run on the main thread (:mod:`._main`).
A new command is one more ``@_command`` handler (and its name in :mod:`neofoam_ui.protocol`).
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

from neofoam_ui.engine import Session
from neofoam_ui.worker._acks import _Acks
from neofoam_ui.worker._out import _Out

__all__ = ()

#: Seconds after a ``stop`` before the worker exits even if the solver is stuck.
_STOP_GRACE = 20.0


@dataclass
class _Worker:
    """What a command acts on."""

    session: Session
    out: _Out
    acks: _Acks
    #: For the main loop: ``step``, ``view`` and ``stop``.
    queue: queue.Queue[dict[str, Any]]


_Handler = Callable[[_Worker, dict[str, Any]], None]
_COMMANDS: dict[str, _Handler] = {}


def _command(name: str) -> Callable[[_Handler], _Handler]:
    def register(fn: _Handler) -> _Handler:
        _COMMANDS[name] = fn
        return fn

    return register


def _handle(worker: _Worker, cmd: dict[str, Any]) -> None:
    """Apply one command; ``ValueError`` / ``KeyError`` / ``TypeError`` for a bad one."""
    kind = cmd["cmd"]
    handler = _COMMANDS.get(kind)
    if handler is None:
        raise ValueError(f"unknown command {kind!r}")
    handler(worker, cmd)


@_command("pause")
def _pause(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.pause_requested = True


@_command("ack")
def _ack(w: _Worker, cmd: dict[str, Any]) -> None:
    w.acks.ack(int(cmd["seq"]))


@_command("stop")
def _stop(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.stop_requested = True
    w.acks.wake()
    w.queue.put(cmd)
    # A solver stuck inside an operation (e.g. a collective whose peer died)
    # never returns to the command loop: exit anyway.
    timer = threading.Timer(_STOP_GRACE, os._exit, (1,))
    timer.daemon = True
    timer.start()


@_command("breakpoints")
def _breakpoints(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.breakpoints = {str(i) for i in cmd["ids"]}


@_command("every_pass")
def _every_pass(w: _Worker, cmd: dict[str, Any]) -> None:
    # At once, like breakpoints: each region's context gets a new set (the region's thread
    # only reads it), so a running region takes it from its next run of the op.
    w.session.set_every_pass(list(cmd["ids"]))


@_command("delay")
def _delay(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.delay = max(0.0, float(cmd["seconds"]))


@_command("watch")
def _watch(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.watch = bool(cmd["on"])


@_command("wait")
def _wait(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.wait_ui = bool(cmd["on"])
    w.out.thin = not w.session.wait_ui
    w.acks.wake()


@_command("frames")
def _frames(w: _Worker, cmd: dict[str, Any]) -> None:
    s = w.session
    s.frame_every = max(1, int(cmd.get("every", s.frame_every)))
    s.frame_seconds = max(0.0, float(cmd.get("seconds", s.frame_seconds)))


@_command("step")
def _step(w: _Worker, cmd: dict[str, Any]) -> None:
    w.queue.put({"cmd": "step", "mode": str(cmd["mode"]), "node": cmd.get("node")})


@_command("view")
def _view(w: _Worker, cmd: dict[str, Any]) -> None:
    # Takes effect at the next live frame of a run; the queued command renders it
    # right away when paused (and is a no-op after a run already showed it).
    w.session.request_view(cmd.get("field"), str(cmd.get("component", "mag")), bool(cmd.get("on", True)))
    w.queue.put({"cmd": "view"})


@_command("live")
def _live(w: _Worker, cmd: dict[str, Any]) -> None:
    w.session.live_view = bool(cmd["on"])
