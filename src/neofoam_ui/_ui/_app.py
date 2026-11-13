"""The app: wires the worker link, the state pushes, the message dispatch and the panes.

The UI should cost the solver next to nothing: with the step view hidden (or no browser
watching) a run sends no per-event messages; the worker never waits for this process
unless "wait for UI" is on; and state reaches the browser at most every 1/15 s while running.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import Any, Optional

from trame.app import asynchronous

from neofoam_ui._ui import _shell
from neofoam_ui._ui._dispatch import _Dispatcher
from neofoam_ui._ui._format import _fmt
from neofoam_ui._ui._layout._palette import _REGION
from neofoam_ui._ui._model import _RunModel
from neofoam_ui._ui._pusher import _Pusher
from neofoam_ui._ui._worker_link import _WorkerLink
from neofoam_ui._ui.panes._dag import _DagPane
from neofoam_ui._ui.panes._field import _FieldPane
from neofoam_ui._ui.panes._plots import _PlotsPane
from neofoam_ui.extensions import ExtensionRegistry, Pane
from neofoam_ui.protocol import CATALYST_ENV

__all__ = ()


class StepperApp:
    """The DAG stepper on a trame ``server``, running ``solver_spec`` (``module:Factory``).

    ``registry``: extension panes (default: those of the installed ``neofoam_ui.extensions``
    entry points, see :mod:`neofoam_ui.extensions`).
    """

    def __init__(self, server: Any, solver_spec: str, case: Optional[str] = None,
                 python: Optional[str] = None, np: int = 1, mpirun: str = "mpirun",
                 catalyst: Optional[str] = None, delay_ms: float = 0.0, wait_ui: bool = False,
                 sync_host: str = "127.0.0.1", registry: Optional[ExtensionRegistry] = None) -> None:
        self.server = server
        self.state = server.state
        self.ctrl = server.controller
        self.solver_spec = solver_spec
        #: Defaults of the animation delay and of "wait for UI" (both changeable live).
        self.delay_ms = max(0.0, float(delay_ms))
        self.wait_ui = wait_ui
        #: MPI ranks; > 1 starts the worker under ``mpirun`` (see neofoam_ui.parallel).
        self.np = np
        #: The worker: its interpreter (e.g. a NeoFOAM venv), case, Catalyst prefix, rank 0's address.
        self.link = _WorkerLink(solver_spec, case, python or sys.executable, np, shlex.split(mpirun),
                                catalyst or os.environ.get(CATALYST_ENV), sync_host)
        self.link.bind(self._receive, self._on_stderr, self._on_exit)
        self.model = _RunModel()
        self.pusher = _Pusher(self.state)
        self._restarting = False

        registry = registry if registry is not None else ExtensionRegistry()
        registry.load_entry_points()
        self.dag = _DagPane(self)
        field = _FieldPane(self)
        self.link.data_url = field.data_url
        self.panes: list[Pane] = [self.dag, field, _PlotsPane(self)]
        self.panes += [factory(self) for factory in registry.panes]
        #: The panels of the layout (built with the page, :mod:`neofoam_ui._ui._panels`).
        self.panels: list[Any] = []
        self.dispatch = _Dispatcher()
        self.dispatch.add({"graph": self.model.graph, "log_error": self._on_log_error, "stdout": self._on_stdout,
                           "skipped": self._on_skipped, "breakpoint": self._on_breakpoint,
                           "running": self._on_running, "paused": self._on_paused, "error": self._on_error,
                           "stopped": self._on_stopped})
        for pane in self.panes:
            self.dispatch.add(pane.handlers())

        self._reset_state()
        _shell._build(self)
        self.dag.register_triggers()
        self.ctrl.on_server_ready.add(lambda **_: asynchronous.create_task(self.spawn()))
        self.server.trigger("nf_key")(self._on_key)
        self.server.trigger("nf_viewer")(self.viewer)
        toggles = [p.toggle.key for p in self.panes if p.toggle is not None]
        self.state.change(*toggles)(lambda **_: self._layout_changed())
        self.state.change("delay_ms")(self._on_delay)
        self.state.change("wait_ui")(lambda wait_ui, **_: self.send({"cmd": "wait", "on": bool(wait_ui)}))

    # -- what panes and scripts use ------------------------------------------------------

    def pane(self, name: str) -> Pane:
        return next(p for p in self.panes if p.name == name)

    @property
    def case(self) -> str:
        """The case directory (the worker's working directory)."""
        return os.path.abspath(self.link.case or ".")

    @property
    def proc(self) -> Any:
        """The worker process (``None`` while there is none)."""
        return self.link.proc

    @proc.setter
    def proc(self, proc: Any) -> None:
        self.link.proc = proc

    def send(self, cmd: dict[str, Any]) -> None:
        self.link.send(cmd)

    def console(self, text: str, cls: str = "") -> None:
        self.pusher.console(text, cls)

    def name(self, node_id: Optional[str]) -> str:
        return self.model.name(node_id)

    def wants_frames(self) -> bool:
        return any(p.wants_frames() for p in self.panes)

    def viewer(self, visible: bool) -> None:
        """A browser tab appeared / became visible (or the reverse): who watches the DAG."""
        self.dag.viewer(visible)

    def _layout_changed(self) -> None:
        for pane in self.panes:
            pane.layout_changed()

    # -- state ---------------------------------------------------------------------------

    def _reset_state(self) -> None:
        s = self.state
        s.solver_name = self.solver_spec.rsplit(":", 1)[-1] + (f" · np {self.np}" if self.np > 1 else "")
        s.connected = False
        s.running = False
        s.finished = False
        s.failed = False
        s.cur_node = None
        s.cur_kind = None
        s.depth = 0
        # every region's token (a multi-region solver; else the one): the actions / init steps
        # and the loop tests paused before, the nodes of the regions waiting for a partner,
        # what each waits for, and the regions done
        s.cur_nodes = []
        s.cur_tests = []
        s.wait_nodes = []
        s.waiting = {}
        s.done_regions = []
        self._region_events: dict[str, dict[str, Any]] = {}
        s.active_loops = []
        s.loop_iter = {}
        s.status = "starting solver worker…"
        s.logs = []
        s.scalars = []
        s.fields_rows = []
        s.callstack = []
        s.follow = True
        s.minimap_on = bool(s.minimap_on)  # off by default; the choice survives a restart
        if s.delay_ms is None:
            s.delay_ms = self.delay_ms
        if s.wait_ui is None:
            s.wait_ui = self.wait_ui
        self.pusher.clear()
        for pane in self.panes:
            pane.reset()

    # -- the worker ----------------------------------------------------------------------

    async def spawn(self) -> None:
        """Start a worker, send it the settings, and read its messages in the background."""
        await self.link.spawn()
        with self.state:
            self.state.connected = True
        self.send({"cmd": "delay", "seconds": float(self.state.delay_ms) / 1000})
        self.send({"cmd": "wait", "on": bool(self.state.wait_ui)})
        for pane in self.panes:
            pane.spawned()
        self.link.read(asynchronous.create_task)

    def _receive(self, msg: dict[str, Any]) -> None:
        self.dispatch(msg)
        self.pusher.after(msg["type"])
        if msg["type"] == "sync":  # "wait for UI": all before it is handled and pushed
            self.send({"cmd": "ack", "seq": msg["seq"]})

    def _on_stderr(self, line: str) -> None:
        self.console(line, "err")
        self.pusher.push()

    def _on_exit(self, current: bool) -> None:
        self.pusher.push()  # what was still buffered
        if current:
            with self.state:
                self.state.connected = False
                self.state.running = False
                self.state.status = "worker exited"

    # -- messages from the worker --------------------------------------------------------------

    def _on_log_error(self, msg: dict[str, Any]) -> None:
        self.console(f"✖ {msg['text']}", "err")

    def _on_stdout(self, msg: dict[str, Any]) -> None:
        self.console(msg["text"], "out")

    def _on_skipped(self, msg: dict[str, Any]) -> None:
        # the worker thinned out progress messages the UI was behind on
        what = ", ".join(f"{n} {k}" for k, n in msg.items() if k != "type")
        self.console(f"… not shown while running: {what}", "out")

    def _on_breakpoint(self, msg: dict[str, Any]) -> None:
        self.console(f"● breakpoint hit: {self.name(msg['node'])}", "bp")

    def _on_running(self, msg: dict[str, Any]) -> None:
        self._set_event(msg["event"])
        self.state.running = True
        self.state.status = f"running · {self._label(msg['event'])}"

    def _on_error(self, msg: dict[str, Any]) -> None:
        self.console(f"✖ error in {self.name(msg.get('node'))}", "err")
        for line in msg["traceback"].rstrip().splitlines():
            self.console(line, "err")

    def _on_stopped(self, msg: dict[str, Any]) -> None:
        self.state.status = "session stopped"
        self.state.running = False

    def _on_paused(self, msg: dict[str, Any]) -> None:
        ev = msg["event"]
        self._set_event(ev)
        self.model.books(msg)
        s = self.state
        s.running = False
        s.finished = msg["finished"]
        s.failed = msg["failed"]
        summary = msg["summary"]
        s.scalars = [{"k": k, "v": _fmt(v)} for k, v in summary["scalars"].items()]
        s.fields_rows = _by_region([
            {**r, "min": _fmt(r["min"]), "max": _fmt(r["max"]), "mean": _fmt(r["mean"])}
            for r in summary["fields"]
        ], self.dag.region_order())
        if s.failed:
            s.status = "✖ failed — see console; Restart to try again"
        elif s.finished:
            s.status = "finished"
            self.console("■ run finished", "")
        else:
            s.status = f"paused before {self._label(ev)}"

    def _regions_of(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        """Where each region is (a multi-region solver; ``[]`` for one region): a paused message
        tells all of them, a running token moves its own region's."""
        if ev.get("regions"):
            self._region_events = {r["region"]: r for r in ev["regions"]}
        elif ev.get("region") is not None and ev["region"] in self._region_events:
            self._region_events[ev["region"]] = {"region": ev["region"], "state": "running", "event": ev,
                                                 "waiting_for": None}
        elif ev.get("region") is None:  # one region (or the run's end)
            self._region_events = {}
        return list(self._region_events.values())

    def _label(self, ev: dict[str, Any]) -> str:
        """The status line's event: per region (``a: ▶ solve · b: waiting for a``) or the one."""
        regions = self._region_events if ev.get("regions") or ev.get("region") else {}
        if not regions:
            return self.model.event_label(ev)
        parts = []
        for r in regions.values():
            if r["state"] == "waiting":
                parts.append(f"{r['region']}: waiting for {r.get('waiting_for') or 'a partner'}")
            elif r["state"] in ("done", "failed"):
                parts.append(f"{r['region']}: {r['state']}")
            elif r.get("event"):
                parts.append(f"{r['region']}: {self.model.event_label(r['event'])}")
        return " · ".join(parts)

    def _set_event(self, ev: dict[str, Any]) -> None:
        """Where the run is: the tokens (one per region), the active loops, the call stack."""
        s, name = self.state, self.name
        s.cur_node = ev["node"] if ev["kind"] != "end" else "end"
        s.cur_kind = ev["kind"]
        s.depth = ev["depth"]
        regions = self._regions_of(ev)
        here = [r["event"] for r in regions if r["state"] in ("paused", "running") and r.get("event")] \
            if regions else ([ev] if ev["kind"] != "end" else [])
        s.cur_nodes = [e["node"] for e in here if e["kind"] != "test"]
        s.cur_tests = [e["node"] for e in here if e["kind"] == "test"]
        waits = [r for r in regions if r["state"] == "waiting" and r.get("event")]
        s.wait_nodes = [r["event"]["node"] for r in waits]
        s.waiting = {r["region"]: f"waiting for {r.get('waiting_for') or 'a partner'}" for r in waits}
        s.done_regions = [r["region"] for r in regions if r["state"] == "done"]
        shown = here + [r["event"] for r in waits]
        s.active_loops = [lid for e in shown for lid, _ in e["stack"]] + [e["node"] for e in shown if e["kind"] == "test"]
        # The pass each active loop is in (a loop test: the pass it decides on).
        s.loop_iter = {k: v for e in shown for k, v in
                       [*((lid, it) for lid, it in e["stack"]), *([(e["node"], e["iter"])] if e["kind"] == "test" else [])]}
        if ev["kind"] == "end":
            s.callstack = ["— finished —"]
            return
        rows: list[str] = []
        for r in regions or [{"region": None, "state": "paused", "event": ev}]:
            e = r.get("event")
            prefix = f"{r['region']}: " if r["region"] else ""
            if r["state"] in ("done", "failed") or e is None:
                rows.append(f"{prefix}{r['state']}")
                continue
            if r["state"] == "waiting":
                head = f"{prefix}⏳ in {name(e['node'])}, waiting for {r.get('waiting_for') or 'a partner'}"
            else:
                head = prefix + (f"◇ {name(e['node'])} test #{e['iter']}" if e["kind"] == "test"
                                 else ("init ▶ " if e["kind"] == "init" else "▶ ") + name(e["node"]))
            rows.append(head)
            for lid, it in reversed(e["stack"]):
                rows.append(f"{'  ' if prefix else ''}↺ {name(lid)}  [iteration {it}]")
            if e["kind"] == "init":
                rows.append(f"{'  ' if prefix else ''}Initialization")
        s.callstack = rows

    # -- user actions ----------------------------------------------------------------------

    def step(self, mode: str, node: Optional[str] = None) -> None:
        if self.state.finished or not self.state.connected:
            return
        self.state.running = True
        self.state.status = "running…"
        self.send({"cmd": "step", "mode": mode, **({"node": node} if node else {})})

    def _on_key(self, mode: str) -> None:
        s = self.state
        if s.running or s.finished or not s.connected:
            return
        if mode == "out" and s.cur_kind != "init" and s.depth == 0:
            return
        self.step(mode)

    def pause(self) -> None:
        self.send({"cmd": "pause"})

    def stop(self) -> None:
        self.send({"cmd": "stop"})

    async def restart(self) -> None:
        if self._restarting:  # e.g. a double click while the old worker shuts down
            return
        self._restarting = True
        try:
            await self.link.terminate()
            with self.state:
                self.model.clear()
                self._reset_state()
            await self.spawn()
        finally:
            self._restarting = False

    def _on_delay(self, delay_ms: float, **_: Any) -> None:
        self.send({"cmd": "delay", "seconds": float(delay_ms) / 1000})


def _by_region(rows: list[dict[str, Any]], regions: list[str]) -> list[dict[str, Any]]:
    """Field rows of a multi-region solver (``<region>.<field>``) grouped per region, in the
    regions' order, each with its region and colour; other rows first, as they came."""
    def region(row: dict[str, Any]) -> Optional[str]:
        head = str(row["name"]).split(".", 1)[0]
        return head if "." in str(row["name"]) and head in regions else None

    out = [{**r, "region": region(r), "color": _REGION.color(region(r)) if region(r) else None} for r in rows]
    return sorted(out, key=lambda r: -1 if r["region"] is None else regions.index(r["region"]))
