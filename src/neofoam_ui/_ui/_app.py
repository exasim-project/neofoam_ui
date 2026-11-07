"""The app: wires the worker link, the state pushes, the message dispatch and the panes.

The UI should cost the solver next to nothing: with the DAG pane closed (or no browser
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
from neofoam_ui._ui._model import _RunModel
from neofoam_ui._ui._pusher import _Pusher
from neofoam_ui._ui._worker_link import _WorkerLink
from neofoam_ui._ui.panes._dag import _DagPane
from neofoam_ui._ui.panes._field import _FieldPane
from neofoam_ui._ui.panes._pipeline import _PipelinePane
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
        self.server.enable_module({"serve": {"insitu": self.link.insitu_dir}})
        self.model = _RunModel()
        self.pusher = _Pusher(self.state)
        self._restarting = False

        registry = registry if registry is not None else ExtensionRegistry()
        registry.load_entry_points()
        self.dag = _DagPane(self)
        self.panes: list[Pane] = [self.dag, _FieldPane(self), _PipelinePane(self), _PlotsPane(self)]
        self.panes += [factory(self) for factory in registry.panes]
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
    def proc(self) -> Any:
        """The worker process (``None`` while there is none)."""
        return self.link.proc

    @proc.setter
    def proc(self, proc: Any) -> None:
        self.link.proc = proc

    @property
    def insitu_dir(self) -> str:
        """Served as ``insitu/``: one directory of in-situ images per run."""
        return self.link.insitu_dir

    @property
    def run_dir(self) -> str:
        """The current run's directory below :attr:`insitu_dir` (``""`` without Catalyst)."""
        return self.link.run_dir

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
        s.active_loops = []
        s.status = "starting solver worker…"
        s.logs = []
        s.scalars = []
        s.fields_rows = []
        s.callstack = []
        s.follow = True
        if s.is_mobile is None:
            s.is_mobile = False
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
        self.state.status = f"running · {self.model.event_label(msg['event'])}"

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
        s.fields_rows = [
            {**r, "min": _fmt(r["min"]), "max": _fmt(r["max"]), "mean": _fmt(r["mean"])}
            for r in summary["fields"]
        ]
        if s.failed:
            s.status = "✖ failed — see console; Restart to try again"
        elif s.finished:
            s.status = "finished"
            self.console("■ run finished", "")
        else:
            s.status = f"paused before {self.model.event_label(ev)}"

    def _set_event(self, ev: dict[str, Any]) -> None:
        """Where the run is: the token, the active loops, the call stack."""
        s, name = self.state, self.name
        s.cur_node = ev["node"] if ev["kind"] != "end" else "end"
        s.cur_kind = ev["kind"]
        s.depth = ev["depth"]
        s.active_loops = [lid for lid, _ in ev["stack"]] + ([ev["node"]] if ev["kind"] == "test" else [])
        if ev["kind"] == "end":
            s.callstack = ["— finished —"]
            return
        head = (f"◇ {name(ev['node'])} test #{ev['iter']}" if ev["kind"] == "test"
                else ("init ▶ " if ev["kind"] == "init" else "▶ ") + name(ev["node"]))
        rows = [head]
        for lid, it in reversed(ev["stack"]):
            rows.append(f"↺ {name(lid)}  [iteration {it}]")
        if ev["kind"] == "init":
            rows.append("Initialization")
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
