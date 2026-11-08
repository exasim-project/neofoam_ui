"""The regions of a multi-region solver, each on its own thread, stepped all together.

A multi-region solver (NeoFOAM's multiRun) runs every region's solver on its own thread;
each region pauses before its own events (:meth:`_Lanes.pause`, ``Event.region`` set).
The regions talk to each other only through blocking receives, so a region the UI holds
stalls its partners at their next receive. Stepping is therefore defined per region and
the step ends when the regions *rest*:

* a step command (into, over, …) applies to **every** region, from the event it is held
  at: a paused region is released and runs until the command's stop condition, evaluated
  on its own events, holds (into: its next event; over: past the op or loop it is at; …);
  a region that is *waiting* (blocked in a receive) keeps its place and takes the command
  from the event it was released from;
* a breakpoint, a target (run to) or a Pause in one region stops every other region at its
  next event (all-stop);
* the step ends once no region is running: each is paused, waiting for a partner, or done.
  A waiting region never holds the step up, and a paused region never holds up a waiting
  one for longer than the step: the next command releases it again. No region is ever held
  while another region the step depends on runs, so stepping cannot deadlock.

The solver's adapter reports the receives (:meth:`_Lanes.waiting`, :meth:`_Lanes.delivered`)
and the regions that finished their run (:meth:`_Lanes.region_done`); a region thread that
ends without finishing failed, and the other regions are then unwound (:class:`_Stopped`),
so the run ends with that region's error.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from neofoam_ui.engine._graph import Event, RegionState
from neofoam_ui.engine._stepping import _until

__all__ = ()

#: Seconds without a new region before the first rest: every region has shown up.
_SETTLE = 0.25
#: Seconds a receive must have blocked before its region counts as waiting (a receive whose
#: message is already there returns sooner).
_STEADY = 0.03
#: Seconds between two looks at the regions' threads while the session waits for them to rest.
_POLL = 0.02


class _Stopped(BaseException):
    """Unwinds a solver thread whose program was closed (or whose run failed elsewhere)."""


def _stop_at_once(ev: Event) -> bool:
    return True


@dataclass
class _Lane:
    """One region's thread, as the gate sees it."""

    region: str
    thread: threading.Thread
    #: ``paused`` (held before :attr:`event`), ``running``, ``waiting`` (in a receive),
    #: ``done`` (finished its run) or ``failed`` (its thread ended early).
    state: str = "running"
    #: The event it is paused before, else the last event it passed or was released from.
    event: Optional[Event] = None
    #: Where it stops next (the command's condition, from its own start event).
    until: Callable[[Event], bool] = _stop_at_once
    go: bool = False
    #: In a receive: ``(source region, tag)``, and since when.
    waiting_for: Optional[tuple[str, str]] = None
    waiting_since: float = 0.0
    #: The adapter said the region finished its run (its thread ends cleanly).
    completed: bool = False
    #: Calls to run on this thread while it is paused (``ThreadedProgram.call``).
    calls: list[Callable[[], None]] = field(default_factory=list)


class _Lanes:
    """The pause gate of every region's thread, and the rest the session waits for."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._lanes: dict[str, _Lane] = {}
        #: Regions in their stepping order, if the adapter knows it (else by name).
        self.order: list[str] = []
        self._new_at = 0.0
        #: A step runs (the session waits for the regions to rest).
        self._active = False
        #: The session has seen the regions rest once (later pauses outside a step are news).
        self._started = False
        #: Every region stops at its next event (a breakpoint or a Pause in one of them).
        self._halt = False
        #: Unwind every region (the program closed, or a region failed).
        self._stop = False
        self._bps: frozenset[str] = frozenset()
        self._check: Callable[[], bool] = lambda: False
        self._passed: Optional[Callable[[Event], None]] = None
        #: The breakpoint the last step stopped at, if one did.
        self.hit: Optional[str] = None
        #: Called (on a region thread) when a region stops outside a step.
        self.on_change: Optional[Callable[[], None]] = None

    # -- region threads ----------------------------------------------------------------------

    def seen(self) -> bool:
        """A region paused already: the run is a multi-region one."""
        return bool(self._lanes)

    def _lane(self, region: str) -> _Lane:
        lane = self._lanes.get(region)
        if lane is None:
            lane = self._lanes[region] = _Lane(region, threading.current_thread())
            self._new_at = time.monotonic()
        return lane

    def pause(self, ev: Event) -> None:
        """Called from region ``ev.region``'s thread before ``ev``: stop here, or pass it."""
        assert ev.region is not None
        late = False
        with self._cv:
            if self._stop:
                raise _Stopped
            lane = self._lane(ev.region)
            lane.state, lane.event = "running", ev
            if not self._stops(lane, ev):
                passed = self._passed
            else:
                passed = None
                lane.state, lane.go = "paused", False
                late = self._started and not self._active
                self._cv.notify_all()
        if passed is not None:
            passed(ev)  # the token of a run (may sleep: the animation delay)
            return
        if late and self.on_change is not None:
            self.on_change()
        with self._cv:
            while True:
                if self._stop:
                    raise _Stopped
                if lane.go:
                    break
                if lane.calls:
                    call = lane.calls.pop(0)
                    self._cv.release()
                    try:
                        call()
                    finally:
                        self._cv.acquire()
                    continue
                self._cv.wait()
            lane.state = "running"

    def _stops(self, lane: _Lane, ev: Event) -> bool:
        if not self._active or self._halt or self._check():
            return True
        if lane.until(ev):
            return True
        if ev.node in self._bps:
            self.hit = ev.node
            self._halt = True  # all-stop: the others stop at their next event
            return True
        return False

    @contextmanager
    def waiting(self, region: str, source: str, tag: str) -> Iterator[None]:
        """Region ``region`` blocks in a receive of ``tag`` from region ``source``."""
        with self._cv:
            lane = self._lanes.get(region)
            if lane is not None and lane.state == "running":
                lane.state, lane.waiting_for, lane.waiting_since = "waiting", (source, tag), time.monotonic()
                self._cv.notify_all()
        try:
            yield
        finally:
            with self._cv:
                if lane is not None and lane.state == "waiting":
                    lane.state = "running"
                if lane is not None:
                    lane.waiting_for = None
                self._cv.notify_all()

    def delivered(self, source: str, target: str, tag: str) -> None:
        """Region ``source`` is about to send ``tag`` to ``target``: a receive waiting for it
        runs again (before the sender can pause, so the step does not end in between)."""
        with self._cv:
            lane = self._lanes.get(target)
            if lane is not None and lane.state == "waiting" and lane.waiting_for == (source, tag):
                lane.state = "running"
                self._cv.notify_all()

    def region_done(self, region: str) -> None:
        """Region ``region`` finished its run: its thread ends cleanly."""
        with self._cv:
            lane = self._lanes.get(region)
            if lane is not None:
                lane.completed = True

    def call(self, fn: Callable[[], None], timeout: Optional[float]) -> Optional[bool]:
        """Run ``fn`` on a paused region's thread; ``None``: no region is paused."""
        done = threading.Event()

        def run() -> None:
            try:
                fn()
            finally:
                done.set()

        with self._cv:
            lane = next((lane for lane in self._sorted() if lane.state == "paused"), None)
            if lane is None:
                return None
            lane.calls.append(run)
            self._cv.notify_all()
        return done.wait(timeout)

    # -- the session --------------------------------------------------------------------------

    def begin(self, mode: Any, target: Optional[str], bps: frozenset[str], check: Callable[[], bool],
              passed: Optional[Callable[[Event], None]]) -> None:
        """Start a step: every region runs until ``mode`` (:func:`_until`) says stop, from its
        own event; ``check()`` true (a Pause) or a breakpoint in ``bps`` stops them all."""
        with self._cv:
            self.hit, self._halt = None, False
            self._bps, self._check, self._passed = bps, check, passed
            for lane in self._lanes.values():
                if lane.state in ("done", "failed"):
                    continue
                lane.until = _until(mode, lane.event or Event("init"), target)
                if lane.state == "paused":
                    lane.go, lane.state = True, "running"
            self._active = True
            self._cv.notify_all()

    def rest(self, finished: Callable[[], bool]) -> None:
        """Block until the regions rest (each paused, waiting on another region, or done), or
        ``finished()`` (the run ended). A region that failed unwinds all the others first."""
        with self._cv:
            while not finished():
                self._reap()
                if any(lane.state == "failed" for lane in self._lanes.values()) and not self._stop:
                    self._stop = True  # the run ends with the failed region's error
                    self._cv.notify_all()
                if not self._stop and self._quiet():
                    break
                self._cv.wait(_POLL)
            self._active = False
            self._started = True

    def _reap(self) -> None:
        for lane in self._lanes.values():
            if lane.state not in ("done", "failed") and not lane.thread.is_alive():
                lane.state = "done" if lane.completed else "failed"

    def _quiet(self) -> bool:
        lanes = list(self._lanes.values())
        if not lanes or time.monotonic() - self._new_at < _SETTLE:
            return False
        if all(lane.state == "done" for lane in lanes):
            return False  # the run is about to end: wait for it
        now = time.monotonic()
        return all(lane.state in ("paused", "done")
                   or (lane.state == "waiting" and now - lane.waiting_since >= _STEADY) for lane in lanes)

    def failed(self) -> list[str]:
        """The regions whose thread ended without finishing their run."""
        return [lane.region for lane in self._lanes.values() if lane.state == "failed"]

    def stop(self) -> None:
        """Unwind every region thread (the program is closed)."""
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def _sorted(self) -> list[_Lane]:
        rank = {region: i for i, region in enumerate(self.order)}
        return sorted(self._lanes.values(), key=lambda lane: (rank.get(lane.region, len(rank)), lane.region))

    def snapshot(self) -> Event:
        """Where the regions are, as one event: the one that hit a breakpoint (else the first
        paused region, else the first region) with every region's state in ``regions``."""
        with self._cv:
            lanes = self._sorted()
            states = tuple(RegionState(lane.region, lane.state, lane.event,
                                       lane.waiting_for[0] if lane.state == "waiting" and lane.waiting_for else None)
                           for lane in lanes)
            focus = next((lane for lane in lanes if lane.state == "paused" and lane.event is not None
                          and self.hit is not None and lane.event.node == self.hit), None) \
                or next((lane for lane in lanes if lane.state == "paused" and lane.event is not None), None) \
                or next((lane for lane in lanes if lane.event is not None), None)
        ev = focus.event if focus is not None and focus.event is not None else Event("init")
        return Event(ev.kind, ev.node, ev.depth, ev.iter, ev.stack, region=ev.region, regions=states)
