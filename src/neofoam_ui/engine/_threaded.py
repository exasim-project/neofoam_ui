"""A solver that calls back into the stepper, as an event generator on its own thread."""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable, Iterator, Optional

from neofoam_ui.engine._graph import Event
from neofoam_ui.engine._lanes import _POLL, _Lanes, _Stopped

__all__ = ()


class ThreadedProgram:
    """Turns a solver that calls back into the stepper into an event generator.

    ``target()`` runs in a thread; whenever it calls :meth:`pause` with an
    :class:`Event`, that event is yielded and the thread blocks until the
    session asks for the next one. An exception in ``target`` is re-raised
    from the generator; ``target`` returning ends it with an ``end`` event.
    Closing the generator unwinds the blocked thread.

    The thread outlives ``target`` so that :meth:`call` can still run code on
    it — MPI must be finalized on the thread that initialised it.

    A multi-region solver's ``target`` runs each region on a thread of its own, whose events
    carry their region (``Event.region``): they pause in :attr:`lanes`, every region on its
    own, and each event yielded is the regions at rest (``Event.regions``); the session
    starts each step with :meth:`_Lanes.begin` before it asks for the next one.
    """

    def __init__(self, target: Callable[[], Any]) -> None:
        self._target = target
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        #: True = resume, False = unwind / end, a callable = run it here and keep waiting.
        self._resume: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._main, name="solver", daemon=True)
        #: What ``target`` returned.
        self.result: Any = None
        #: ``skip(event)`` true: :meth:`pause` returns at once, the event is not handed over.
        self.skip: Optional[Callable[[Event], bool]] = None
        #: The regions' pause gates (a multi-region solver).
        self.lanes = _Lanes()
        #: A region-less event came: one region, the events come one by one.
        self._plain = False
        #: ``target`` ended; the thread serves :meth:`call` until closed.
        self._serving = False

    def _main(self) -> None:
        try:
            self.result = self._target()
        except _Stopped:
            failed = self.lanes.failed()
            if not failed:  # closed
                return
            # (a region failed without its error reaching here, e.g. its thread was killed)
            self._events.put(("error", RuntimeError(f"region {', '.join(failed)} ended before its run finished")))
        except BaseException as exc:  # noqa: BLE001 - handed to the session
            self._events.put(("error", exc))
        else:
            self._events.put(("done", None))
        self._serving = True
        while self._wait() is True:  # serve call() until closed
            pass

    def _wait(self) -> bool:
        """Block for the session's word, running any calls handed over meanwhile."""
        while True:
            cmd = self._resume.get()
            if isinstance(cmd, bool):
                return cmd
            cmd()

    def pause(self, event: Event) -> None:
        """Called from the solver thread (a region's thread): stop here until the session moves on."""
        if event.region is not None:
            self.lanes.pause(event)
            return
        skip = self.skip
        if skip is not None and skip(event):
            return
        self._events.put(("event", event))
        if not self._wait():
            raise _Stopped

    def call(self, fn: Callable[[], Any], timeout: Optional[float] = None) -> bool:
        """Run ``fn`` on the solver thread while it is paused or finished.

        Returns False if it did not complete within ``timeout`` (e.g. the solver
        thread is stuck inside an operation).
        """
        if not self._thread.is_alive():
            return False
        if self.lanes.seen() and not self._serving:  # the regions are held, not this thread
            on_region = self.lanes.call(fn, timeout)
            return bool(on_region)
        done = threading.Event()

        def run() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001 - must not kill the solver thread
                traceback.print_exc()
            finally:
                done.set()

        self._resume.put(run)
        return done.wait(timeout)

    def _next(self) -> Optional[tuple[str, Any]]:
        """The next item of the solver thread, or ``None``: the regions rest."""
        if self._plain:
            return self._events.get()
        while not self.lanes.seen():  # which kind of run is not known yet
            try:
                item = self._events.get(timeout=_POLL)
            except queue.Empty:
                continue
            self._plain = item[0] == "event"
            return item
        self.lanes.rest(lambda: not self._events.empty())
        return None if self._events.empty() else self._events.get()

    def __iter__(self) -> Iterator[Event]:
        self._thread.start()
        while True:
            item = self._next()
            if item is None:
                try:
                    yield self.lanes.snapshot()
                except GeneratorExit:
                    self.lanes.stop()
                    raise
                continue  # the session began the next step (lanes.begin)
            kind, value = item
            if kind == "error":
                raise value
            if kind == "done":
                yield Event("end")
                return
            try:
                yield value
            except GeneratorExit:
                self._resume.put(False)
                raise
            self._resume.put(True)
