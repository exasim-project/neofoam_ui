"""trame app: the DAG stepper (activity-diagram simulation of a solver run).

Launch: ``neofoam-ui [--solver module:Factory] [--port 8080]``. The solver runs
in a worker process (:mod:`neofoam_ui.worker`); this process only renders and
sends stepping commands.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import socket
import sys
import tempfile
from typing import Any, Optional

from trame.app import asynchronous, get_server
from trame.ui.vuetify3 import SinglePageWithDrawerLayout
from trame.widgets import client, html
from trame.widgets import vuetify3 as v3
from trame_flow.widgets.flow import Background, Controls, CustomNode, Handle, MiniMap, NodeEditor

from neofoam_ui.catalyst import ENV as CATALYST_ENV
from neofoam_ui.catalyst import OUT_ENV
from neofoam_ui.fieldview import FIELD_CSS, component_items, field_pane
from neofoam_ui.layout import build_flow, build_pipeline_flow, domain_color
from neofoam_ui.pipelineview import PIPELINE_CSS, pipeline_pane

__all__ = ["StepperApp", "main"]

DEFAULT_SOLVER = "neofoam_ui.toy.heat1d:Heat1D"
CONSOLE_MAX = 500

_CSS = r"""
.nf-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; grid-template-rows: minmax(0, 1fr) 230px;
  height: calc(100vh - 64px); }
.nf-canvas { grid-column: 1; grid-row: 1; min-height: 0; display: flex; }
.nf-diagram { flex: 1 1 0; min-width: 0; position: relative; }
.nf-field { flex: 1 1 0; }
.nf-side { grid-column: 2; grid-row: 1 / 3; overflow: auto; border-left: 1px solid rgba(128,128,128,.25); }
.nf-bottom { grid-column: 1; grid-row: 2; display: grid; grid-template-columns: 1fr 1fr; min-height: 0;
  border-top: 1px solid rgba(128,128,128,.25); }
.nf-bottom > div { overflow: auto; min-height: 0; }
.nf-bottom > div:first-child { border-right: 1px solid rgba(128,128,128,.25); }
.nf-status { position: absolute; left: 10px; top: 8px; z-index: 5; font-size: 13px; padding: 2px 10px;
  border-radius: 12px; background: rgba(var(--v-theme-surface), .9); box-shadow: 0 1px 3px rgba(0,0,0,.15); }
.nf-tools { display: flex; align-items: center; overflow-x: auto; flex: 1 1 auto; min-width: 0; scrollbar-width: none; }
.nf-tools .v-btn { flex: none; }
@media (max-width: 900px) {
  .nf-grid { grid-template-columns: 1fr; grid-template-rows: 58vh auto auto; height: auto; }
  .nf-canvas { grid-column: 1; grid-row: 1; flex-direction: column; }
  .nf-grid.fv { grid-template-rows: auto auto auto; }
  .nf-grid.fv .nf-diagram { flex: none; height: 52vh; }
  .nf-grid.fv .nf-field { flex: none; height: 62vh; border-left: 0; border-top: 1px solid rgba(128,128,128,.25); }
  .nf-grid.fv .nf-pp { height: 70vh; }
  .nf-bottom { grid-column: 1; grid-row: 2; grid-template-columns: 1fr; }
  .nf-bottom > div { max-height: 34vh; }
  .nf-bottom > div:first-child { border-right: 0; border-bottom: 1px solid rgba(128,128,128,.25); }
  .nf-side { grid-column: 1; grid-row: 3; border-left: 0; border-top: 1px solid rgba(128,128,128,.25); }
  .nf-btxt, .nf-wide { display: none; }
  .v-toolbar-title { flex: none !important; min-width: 0 !important; }
}
.nf-h { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .65; padding: 8px 12px 4px;
  position: sticky; top: 0; background: rgb(var(--v-theme-surface)); z-index: 1; }
.nf-mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.nf-console div { padding: 0 12px; white-space: pre; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.nf-console .test { color: #5b6fb5; } .nf-console .bp { color: #d22; font-weight: 600; }
.nf-console .out { opacity: .7; } .nf-console .err { color: #d22; } .nf-console .init { color: #2e86c1; }
.nf-vars td, .nf-vars th { padding: 2px 10px !important; height: 22px !important; font-size: 12px; }
.nf-vars tr.changed td { background: rgba(255, 200, 0, .22); }
.nf-tree div { padding: 2px 12px; cursor: pointer; white-space: nowrap; display: flex; align-items: center; gap: 6px; font-size: 13px; }
.nf-tree div:hover { background: rgba(128,128,128,.12); }
.nf-tree .sel { outline: 1px solid #2b6fd6; background: rgba(43,111,214,.08); }
.nf-tree .cur { color: #e0362c; font-weight: 600; } .nf-tree .done { color: #2f9e5b; }
.nf-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.nf-bpmark { width: 10px; color: #d22; font-size: 10px; }
.nf-kv { display: grid; grid-template-columns: 92px 1fr; gap: 2px 8px; font-size: 13px; padding: 0 12px; }
.nf-kv > span:nth-child(odd) { opacity: .6; }
.nf-sect { padding: 4px 0 8px; border-bottom: 1px solid rgba(128,128,128,.2); }
.nf-stack div { padding: 1px 12px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.nf-stack div:first-child { color: #e0362c; font-weight: 600; }

/* --- activity diagram nodes --- */
.vue-flow__handle { opacity: 0; width: 4px; height: 4px; min-width: 0; min-height: 0; border: 0; }
.vue-flow__edge-text { font-size: 11px; }
.nf-node { position: relative; width: 100%; height: 100%; box-sizing: border-box; }
.nf-action, .nf-init { border: 1.4px solid #b9a24a; background: #fffbe6; border-radius: 14px; display: flex;
  align-items: center; padding: 0 10px 0 14px; gap: 8px; font-size: 13px; color: #1d2430; cursor: pointer; }
.nf-init { border-color: #7a9cc6; background: #eef4fb; border-radius: 8px; }
.nf-action.inj { border-style: dashed; }
.nf-stripe { width: 4px; align-self: stretch; margin: 9px 0; border-radius: 2px; flex: none; }
.nf-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nf-meta { font-size: 11px; opacity: .6; white-space: nowrap; }
.nf-badge { position: absolute; top: -9px; right: 10px; font-size: 10px; background: #8e5bb5; color: #fff;
  border-radius: 8px; padding: 0 6px; line-height: 16px; }
.nf-node.done { background: #e6f6ec; border-color: #2f9e5b; }
.nf-node.sel { box-shadow: 0 0 0 2.5px #2b6fd6; }
.nf-node.cur { background: #ffe9e7; border-color: #e0362c; border-width: 2.5px; }
.nf-node.cur::after { content: ""; position: absolute; right: -16px; top: calc(50% - 6px); width: 12px; height: 12px;
  border-radius: 50%; background: #e0362c; box-shadow: 0 0 0 4px rgba(224,54,44,.25); animation: nfpulse 1s infinite; }
@keyframes nfpulse { 50% { box-shadow: 0 0 0 8px rgba(224,54,44,.05); } }
.nf-bp { position: absolute; left: -22px; top: calc(50% - 6px); width: 12px; height: 12px; border-radius: 50%;
  border: 1.5px dashed rgba(128,128,128,.5); cursor: pointer; }
.nf-bp.on { background: #d22; border: 1.5px solid #d22; }
.nf-loop { border: 1.4px solid #7b8fc7; background: rgba(123,143,199,.07); border-radius: 8px; }
.nf-loop.active { border-color: #e0362c; border-width: 2px; }
.nf-loop.collapsed { background: #f3f6ff; border-radius: 12px; cursor: pointer; }
.nf-loop.collapsed.active { background: #ffe9e7; }
.nf-tog { display: inline-block; width: 16px; cursor: pointer; font-size: 13px; opacity: .9; }
.nf-loop-size { position: absolute; right: 10px; bottom: 6px; font-size: 11px; opacity: .6; }
.nf-node.hlw { box-shadow: 0 0 0 3px #f08c00; }
.nf-node.hlr { box-shadow: 0 0 0 3px #4d8fe0; }
.nf-vars tr.hl td { outline: 1px solid #f08c00; background: rgba(240,140,0,.12); }
.nf-loop-title { position: absolute; left: 10px; top: 6px; font-size: 12px; opacity: .8; }
.nf-loop-test { position: absolute; left: 26px; top: 24px; font-size: 11px; opacity: .65; font-family: ui-monospace, Menlo, monospace; }
.nf-decision { width: 100%; height: 100%; position: relative; cursor: pointer; }
.nf-decision .d { position: absolute; inset: 5px; transform: rotate(45deg); border: 1.6px solid #7b8fc7; background: #f3f6ff; }
.nf-decision.cur .d { border-color: #e0362c; border-width: 2.5px; background: #ffe9e7; }
.nf-decision.cur::after { content: ""; position: absolute; left: calc(50% - 5px); top: calc(50% - 5px); width: 10px;
  height: 10px; border-radius: 50%; background: #e0362c; }
.nf-decision.sel .d { box-shadow: 0 0 0 2.5px #2b6fd6; }
.nf-decision .nf-bp { left: -26px; }
.nf-activity { border: 1.6px solid #8a94a6; border-radius: 18px; background: rgba(138,148,166,.05); }
.nf-activity-title { position: absolute; left: 14px; top: 9px; font-size: 13px; font-weight: 600; opacity: .75; }
.nf-initial { width: 100%; height: 100%; border-radius: 50%; background: #1d2430; }
.nf-final { width: 100%; height: 100%; border-radius: 50%; border: 2px solid #1d2430; position: relative; }
.nf-final::after { content: ""; position: absolute; inset: 4px; border-radius: 50%; background: #1d2430; }
.nf-final.cur::after { background: #e0362c; }
.nf-placeholder { border: 1.4px dashed rgba(128,128,128,.6); border-radius: 12px; display: flex; align-items: center;
  justify-content: center; text-align: center; font-size: 12px; opacity: .7; padding: 6px; }
"""


class StepperApp:
    def __init__(self, server: Any, solver_spec: str, case: Optional[str] = None,
                 python: Optional[str] = None, np: int = 1, mpirun: str = "mpirun",
                 catalyst: Optional[str] = None) -> None:
        self.server = server
        self.case = case
        #: Interpreter for the worker, e.g. a NeoFOAM venv; neofoam_ui is put on its PYTHONPATH.
        self.python = python or sys.executable
        #: MPI ranks; > 1 starts the worker under ``mpirun`` (see neofoam_ui.parallel).
        self.np = np
        self.mpirun = shlex.split(mpirun)
        #: Catalyst 2 + ParaView install prefix for the in-situ view (neofoam_ui.catalyst).
        self.catalyst = catalyst or os.environ.get(CATALYST_ENV)
        #: Images the solver renders in situ, served as ``insitu/<run>/view_*.png``.
        self.insitu_dir = tempfile.mkdtemp(prefix="neofoam_ui_insitu_")
        self.server.enable_module({"serve": {"insitu": self.insitu_dir}})
        self._runs = 0
        self._run_dir = ""
        #: Render at every pause even with the pane closed (a ParaView is attached via Live).
        self.live = os.environ.get("NEOFOAM_UI_CATALYST_LIVE", "off") != "off"
        self.state = server.state
        self.ctrl = server.controller
        self.solver_spec = solver_spec
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._restarting = False
        self.init_steps: list[dict[str, Any]] = []
        self.ops: list[dict[str, Any]] = []
        self.index: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, int] = {}
        self.last_ms: dict[str, float] = {}
        self.total_ms: dict[str, float] = {}
        #: Fields each action was observed to change.
        self.writes_seen: dict[str, list[str]] = {}
        #: Last field-view request sent to the worker (so the worker's own choice is not echoed back).
        self._view_req: Optional[tuple[Any, ...]] = None
        #: Nodes and edges of the pipeline diagram drawn last (refit only when they change).
        self._pp_topology: Optional[tuple[Any, ...]] = None
        self._reset_state()
        self.editor: Any = None
        self.pp_editor: Any = None
        self._build_ui()

        self.ctrl.on_server_ready.add(lambda **_: asynchronous.create_task(self.spawn()))
        self.server.trigger("nf_toggle_bp")(self.toggle_bp)
        self.server.trigger("nf_select")(self.select)
        self.server.trigger("nf_key")(self._on_key)
        self.server.trigger("nf_collapse")(self.toggle_collapse)
        self.server.trigger("nf_field")(self.highlight_field)
        self.state.change("fv_on", "fv_field", "fv_comp", "pp_on")(lambda **_: self._send_view())
        self.state.change("fv_on", "pp_on")(lambda **_: self._fit_pipeline())
        self.state.change("fv_live")(lambda fv_live, **_: self._send({"cmd": "live", "on": bool(fv_live)}))
        self.state.change("delay_ms")(self._on_delay)
        self.state.change("heat_on")(lambda **_: self._refresh_heat())

    # -- state ------------------------------------------------------------------------

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
        s.visited = []
        s.bps = s.bps if isinstance(s.bps, list) else []
        s.collapsed = s.collapsed if isinstance(s.collapsed, list) else []
        s.heat_on = bool(s.heat_on)
        s.heat = {}
        s.heat_ms = {}
        s.hl_field = None
        s.hl = {}
        s.selected = None
        s.status = "starting solver worker…"
        s.logs = []
        s.scalars = []
        s.fields_rows = []
        s.callstack = []
        s.tree = []
        s.insp = None
        s.bp_list = []
        s.follow = True
        if s.is_mobile is None:
            s.is_mobile = False
        if s.delay_ms is None:
            s.delay_ms = 150
        # Field view: the toggle and the choice survive a restart.
        s.fv_on = bool(s.fv_on)
        s.fv_field = s.fv_field or None
        s.fv_comp = s.fv_comp or "mag"
        s.fv_fields = []
        s.fv_comps = []
        s.fv_src = None
        s.fv_live = True if s.fv_live is None else bool(s.fv_live)
        s.fv_msg = "the field view starts once the solver context exists (after initialization)"
        # Post-processing diagram (drawn from the pipeline Catalyst runs for the field view).
        s.pp_on = bool(s.pp_on)
        s.pp_graph = False
        s.pp_ms = {}
        s.pp_msg = "the pipeline appears with the first in-situ frame (after initialization)"
        self._pp_topology = None

    # -- worker -------------------------------------------------------------------------

    async def spawn(self) -> None:
        """Start a worker and read its messages in the background."""
        env = dict(os.environ)
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH")) if p)
        cmd = [self.python, "-u", "-m", "neofoam_ui.worker", self.solver_spec]
        if self.catalyst:
            self._runs += 1
            self._run_dir = f"run{self._runs}"
            env[CATALYST_ENV] = self.catalyst
            env[OUT_ENV] = os.path.join(self.insitu_dir, self._run_dir)
        if self.np > 1:
            from neofoam_ui.parallel import SYNC_ENV  # noqa: PLC0415

            env[SYNC_ENV] = f"127.0.0.1:{_free_port()}"
            cmd = [*self.mpirun, "-np", str(self.np), *cmd]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.case,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1 << 24,
        )
        with self.state:
            self.state.connected = True
        self._send({"cmd": "delay", "seconds": self.state.delay_ms / 1000})
        self._send({"cmd": "breakpoints", "ids": list(self.state.bps)})
        self._send({"cmd": "live", "on": bool(self.state.fv_live)})
        self._view_req = None
        self._send_view()
        asynchronous.create_task(self._read_stderr(self.proc))
        asynchronous.create_task(self._read_stdout(self.proc))

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        async for line in proc.stdout:
            if self.proc is not proc:  # replaced by a restart: drop the old worker's messages
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.state:
                self._handle(msg)
        with self.state:
            if self.proc is proc:
                self.state.connected = False
                self.state.running = False
                self.state.status = "worker exited"

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            if self.proc is not proc:
                break
            with self.state:
                self._console(line.decode(errors="replace").rstrip(), "err")

    def _send(self, cmd: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            return
        self.proc.stdin.write((json.dumps(cmd) + "\n").encode())

    # -- messages from the worker ------------------------------------------------------------

    def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg["type"]
        if kind == "graph":
            if msg["activity"] == "init":
                self.init_steps = msg["steps"]
            else:
                self.ops = msg["ops"]
            self._rebuild_graph()
        elif kind == "log":
            self._on_log(msg)
        elif kind == "log_error":
            self._console(f"✖ {msg['text']}", "err")
        elif kind == "stdout":
            self._console(msg["text"], "out")
        elif kind == "breakpoint":
            self._console(f"● breakpoint hit: {self._name(msg['node'])}", "bp")
        elif kind == "running":
            self._set_event(msg["event"])
            self.state.running = True
            self.state.status = f"running · {self._event_label(msg['event'])}"
        elif kind == "paused":
            self._on_paused(msg)
        elif kind == "error":
            self._console(f"✖ error in {self._name(msg.get('node'))}", "err")
            for line in msg["traceback"].rstrip().splitlines():
                self._console(line, "err")
        elif kind == "frame":
            self._on_frame(msg)
        elif kind == "stopped":
            self.state.status = "session stopped"
            self.state.running = False

    def _on_log(self, msg: dict[str, Any]) -> None:
        scope = " › ".join(f"{self._name(lid)}#{it}" for lid, it in msg["stack"]) or "—"
        t = msg.get("time")
        prefix = f"[t={t:.4g}] " if isinstance(t, (int, float)) else ""
        visited = list(self.state.visited)
        if msg["kind"] == "init":
            self._console(f"{prefix}init ▶ {msg['name']}  ({msg['ms']:.2f} ms)", "init")
            visited.append(msg["node"])
        elif msg["kind"] == "exec":
            self._console(f"{prefix}{scope}  ▶ {msg['name']}  ({msg['ms']:.2f} ms)", "")
            visited.append(msg["node"])
        else:
            self._console(f"{prefix}{scope}  ◇ {msg['name']} test #{msg['iter']} → {msg['result']}", "test")
            if not msg["stack"]:  # new time step: forget what ran in the previous one
                visited = [v for v in visited if v.startswith("init:")]
        self.state.visited = visited

    def _on_paused(self, msg: dict[str, Any]) -> None:
        ev = msg["event"]
        self._set_event(ev)
        self.calls, self.last_ms = msg["calls"], msg["last_ms"]
        self.total_ms = msg.get("total_ms", {})
        self.writes_seen = msg.get("writes_seen", {})
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
        self._refresh_heat()
        self._refresh_highlight()
        if s.failed:
            s.status = "✖ failed — see console; Restart to try again"
        elif s.finished:
            s.status = "finished"
            self._console("■ run finished", "")
        else:
            s.status = f"paused before {self._event_label(ev)}"
        if s.follow and ev["node"]:
            self.select(ev["node"], pan=True)
        else:
            self._refresh_inspector()

    def _send_view(self) -> None:
        """Tell the worker what the field view shows; closed = nothing is rendered at pauses.

        The pipeline pane needs frames too: its diagram is read back from the executed pipeline.
        """
        s = self.state
        on = bool(s.fv_on or s.pp_on or self.live)
        req = (True, s.fv_field, s.fv_comp) if on else (False,)
        if req == self._view_req or self.proc is None:
            return
        self._view_req = req
        self._send({"cmd": "view", "on": on, "field": s.fv_field, "component": s.fv_comp})

    def _on_frame(self, msg: dict[str, Any]) -> None:
        s = self.state
        if msg.get("error"):
            s.fv_msg = s.pp_msg = msg["error"]
            s.fv_src = None
            s.pp_graph = False
            return
        self._on_pipeline(msg)
        if self._view_req != (True, *msg.get("view", (msg["field"], msg["component"]))):
            return  # rendered for an earlier choice (a live frame in flight): keep the user's
        fields = msg["fields"]
        s.fv_fields = [{"title": f["name"] + (" (vector)" if f["ncomp"] == 3 else f" ({f['ncomp']} comp.)" if f["ncomp"] > 1 else ""),
                        "value": f["name"]} for f in fields]
        ncomp = next((f["ncomp"] for f in fields if f["name"] == msg["field"]), 1)
        s.fv_comps = component_items(ncomp)
        # The worker may have picked the field (none chosen yet / not cell-sized): adopt it.
        # Echoing it back is harmless (the same frame is not rendered twice).
        s.fv_field, s.fv_comp = msg["field"], msg["component"]
        s.fv_src = f"insitu/{self._run_dir}/{msg['file']}"
        t = msg.get("time")
        ranks = f"{self.np} ranks" if self.np > 1 else ""
        parts = [f"t = {t:.6g}" if isinstance(t, (int, float)) else "", "rendered in situ (Catalyst)", ranks]
        s.fv_msg = " · ".join(p for p in parts if p)

    def _on_pipeline(self, msg: dict[str, Any]) -> None:
        """The frame's pipeline diagram (sent when it changed) and last execute times."""
        s = self.state
        desc = msg.get("pipeline")
        if desc is not None:
            self.pp_editor.graph = build_pipeline_flow(desc)
            topology = (tuple(n["id"] for n in desc["nodes"]),
                        tuple((e["source"], e["target"]) for e in desc["edges"]))
            if topology != self._pp_topology:
                self._pp_topology = topology
                self.pp_editor.fit_view()
            s.pp_graph = True
        ms = msg.get("pipeline_ms")
        if ms is None:
            if not s.pp_graph:
                s.pp_msg = "this Catalyst implementation does not describe its pipeline"
            return
        s.pp_ms = {k: _fmt_ms(v) for k, v in ms.items() if k != "total"}
        t = msg.get("time")
        parts = [f"t = {t:.6g}" if isinstance(t, (int, float)) else "",
                 f"last execute {_fmt_ms(ms['total'])}" if "total" in ms else "",
                 "times of rank 0" if self.np > 1 else ""]
        s.pp_msg = " · ".join(p for p in parts if p)

    def _fit_pipeline(self) -> None:
        """Refit the diagram when its pane opens or the field pane next to it changes its width."""
        if self.pp_editor is not None and self.state.pp_on and self.state.pp_graph:
            self.pp_editor.fit_view()

    def _set_event(self, ev: dict[str, Any]) -> None:
        s = self.state
        s.cur_node = ev["node"] if ev["kind"] != "end" else "end"
        s.cur_kind = ev["kind"]
        s.depth = ev["depth"]
        s.active_loops = [lid for lid, _ in ev["stack"]] + ([ev["node"]] if ev["kind"] == "test" else [])
        if ev["kind"] == "end":
            s.callstack = ["— finished —"]
            return
        head = (f"◇ {self._name(ev['node'])} test #{ev['iter']}" if ev["kind"] == "test"
                else ("init ▶ " if ev["kind"] == "init" else "▶ ") + self._name(ev["node"]))
        rows = [head]
        for lid, it in reversed(ev["stack"]):
            rows.append(f"↺ {self._name(lid)}  [iteration {it}]")
        if ev["kind"] == "init":
            rows.append("Initialization")
        s.callstack = rows

    def _event_label(self, ev: dict[str, Any]) -> str:
        if ev["kind"] == "end":
            return "end"
        name = self._name(ev["node"])
        return f"◇ {name} test #{ev['iter']}" if ev["kind"] == "test" else name

    def _console(self, text: str, cls: str) -> None:
        self.state.logs = [{"t": text, "c": cls}] + self.state.logs[: CONSOLE_MAX - 1]

    # -- graph ------------------------------------------------------------------------------

    def _rebuild_graph(self, fit: bool = True) -> None:
        self.index = {}
        tree: list[dict[str, Any]] = [{"id": "act:init", "name": "Initialization", "depth": 0,
                                       "color": "#8a94a6", "kind": "activity"}]
        for s in self.init_steps:
            self.index[s["id"]] = {**s, "kind": "init",
                                   "preds": s["depends_on"],
                                   "succs": [t["id"] for t in self.init_steps if s["id"] in t["depends_on"]]}
            tree.append({"id": s["id"], "name": s["name"], "depth": 1, "kind": "init",
                         "color": domain_color(s.get("category"))})
        tree.append({"id": "act:exec", "name": f"Execution · {self.state.solver_name}", "depth": 0,
                     "color": "#8a94a6", "kind": "activity"})

        def walk(ops: list[dict[str, Any]], parent: Optional[str], depth: int) -> None:
            for i, op in enumerate(ops):
                prev = ops[i - 1]["id"] if i > 0 else parent
                nxt = ops[i + 1]["id"] if i < len(ops) - 1 else parent
                self.index[op["id"]] = {**op, "parent": parent, "prev": prev, "next": nxt,
                                        "child_ids": [c["id"] for c in op["children"]]}
                tree.append({"id": op["id"], "name": op["name"], "depth": depth, "kind": op["kind"],
                             "color": domain_color(op.get("domain"))})
                walk(op["children"], op["id"], depth + 1)

        walk(self.ops, None, 1)
        self.state.tree = tree
        collapsed = frozenset(self.state.collapsed or ())
        flow = build_flow(self.init_steps, self.ops or None, self.state.solver_name, collapsed)
        self.editor.graph = flow
        if fit:
            self.editor.fit_view()
        self._refresh_bp_list()

    def _name(self, node_id: Optional[str]) -> str:
        if not node_id:
            return "—"
        info = self.index.get(node_id)
        return info["name"] if info else node_id

    # -- user actions ------------------------------------------------------------------------

    def step(self, mode: str, node: Optional[str] = None) -> None:
        if self.state.finished or not self.state.connected:
            return
        self.state.running = True
        self.state.status = "running…"
        self._send({"cmd": "step", "mode": mode, **({"node": node} if node else {})})

    def _on_key(self, mode: str) -> None:
        s = self.state
        if s.running or s.finished or not s.connected:
            return
        if mode == "out" and s.cur_kind != "init" and s.depth == 0:
            return
        self.step(mode)

    def pause(self) -> None:
        self._send({"cmd": "pause"})

    def stop(self) -> None:
        self._send({"cmd": "stop"})

    async def restart(self) -> None:
        if self._restarting:  # e.g. a double click while the old worker shuts down
            return
        self._restarting = True
        try:
            await self._restart()
        finally:
            self._restarting = False

    async def _restart(self) -> None:
        old = self.proc
        self.proc = None
        if old is not None and old.returncode is None:
            # SIGTERM first: mpirun forwards it to the ranks, SIGKILL would orphan them.
            old.terminate()
            try:
                await asyncio.wait_for(old.wait(), timeout=10)
            except asyncio.TimeoutError:
                old.kill()
                await old.wait()
        with self.state:
            self.init_steps, self.ops, self.index = [], [], {}
            self.calls, self.last_ms, self.total_ms, self.writes_seen = {}, {}, {}, {}
            self._reset_state()
            self.editor.clear_graph()
            self.pp_editor.clear_graph()
        await self.spawn()

    def toggle_bp(self, node_id: str) -> None:
        node_id = node_id.removesuffix("::test")
        bps = list(self.state.bps)
        if node_id in bps:
            bps.remove(node_id)
        else:
            bps.append(node_id)
        self.state.bps = bps
        self._send({"cmd": "breakpoints", "ids": bps})
        self._refresh_bp_list()
        self._refresh_inspector()

    def select(self, node_id: Optional[str], pan: bool = False) -> None:
        if not node_id:
            return
        node_id = node_id.removesuffix("::test")
        if node_id.startswith("act:") or node_id in ("start", "exec:placeholder"):
            return
        self.state.selected = node_id
        self._refresh_inspector()
        if pan and node_id in self.index:
            shown = self._shown(node_id)
            target = (f"{shown}::test" if self.index[shown]["kind"] == "loop"
                      and shown not in self.state.collapsed else shown)
            self.server.js_call(getattr(self.editor, "_NodeEditor__ref"), "fitView",
                                {"nodes": [target], "duration": 250, "maxZoom": 1.0, "minZoom": 0.5, "padding": 1.2})

    def toggle_collapse(self, loop_id: str) -> None:
        collapsed = list(self.state.collapsed)
        if loop_id in collapsed:
            collapsed.remove(loop_id)
        else:
            collapsed.append(loop_id)
        self.state.collapsed = collapsed
        self._rebuild_graph(fit=False)
        self._refresh_heat()

    def _shown(self, node_id: str) -> str:
        """``node_id``, or the outermost collapsed loop hiding it."""
        shown, cur = node_id, self.index.get(node_id, {}).get("parent")
        while cur:
            if cur in self.state.collapsed:
                shown = cur
            cur = self.index[cur]["parent"]
        return shown

    def _subtree_ms(self, op_id: str) -> float:
        info = self.index[op_id]
        return self.total_ms.get(op_id, 0.0) + sum(self._subtree_ms(c) for c in info["child_ids"])

    def _refresh_heat(self) -> None:
        """Tint actions, init steps and collapsed loops by their share of the summed time."""
        s = self.state
        if not s.heat_on:
            s.heat, s.heat_ms = {}, {}
            return
        times = {k: v for k, v in self.total_ms.items()
                 if k in self.index and self.index[k]["kind"] in ("action", "init")}
        times.update({lid: self._subtree_ms(lid) for lid in s.collapsed if lid in self.index})
        top = max(times.values(), default=0.0) or 1.0
        s.heat = {k: f"hsl(28, 95%, {96 - 42 * (v / top):.0f}%)" for k, v in times.items()}
        s.heat_ms = {k: _fmt_ms(v) for k, v in times.items()}

    def highlight_field(self, field: str) -> None:
        self.state.hl_field = None if self.state.hl_field == field else field
        self._refresh_highlight()

    def _refresh_highlight(self) -> None:
        """Ops that wrote (observed or declared) or read (declared) the highlighted field."""
        field, hl = self.state.hl_field, {}
        if field:
            for op_id, info in self.index.items():
                if info["kind"] == "init":
                    continue
                if field in self.writes_seen.get(op_id, ()) or field in (info.get("writes") or ()):
                    hl[op_id] = "w"
                elif field in (info.get("reads") or ()):
                    hl[op_id] = "r"
            for op_id in list(hl):  # a collapsed loop stands in for its body
                shown = self._shown(op_id)
                if shown != op_id and hl.get(shown) != "w":
                    hl[shown] = hl[op_id]
        self.state.hl = hl

    def _on_delay(self, delay_ms: float, **_: Any) -> None:
        self._send({"cmd": "delay", "seconds": float(delay_ms) / 1000})

    def _refresh_bp_list(self) -> None:
        self.state.bp_list = [{"id": b, "name": self._name(b)} for b in self.state.bps]

    def _refresh_inspector(self) -> None:
        sel = self.state.selected
        info = self.index.get(sel) if sel else None
        if info is None:
            self.state.insp = None
            return
        chip = lambda i, label=None: {"id": i, "name": label or self._name(i)}  # noqa: E731
        rows: list[dict[str, str]] = []
        nav: list[dict[str, Any]] = []
        if info["kind"] == "init":
            kind_label = "Init step"
            rows += [{"k": "category", "v": info.get("category") or "—"}]
            nav += [{"dir": "⬅", **chip(p)} for p in info["preds"]]
            nav += [{"dir": "➡", **chip(c)} for c in info["succs"]]
        else:
            kind_label = "Loop (IterativeOp)" if info["kind"] == "loop" else "Action (SequentialOp)"
            rows += [{"k": "position", "v": info.get("pos") or "—"},
                     {"k": "domain", "v": info.get("domain") or "—"}]
            if info.get("number") and info.get("number") != info.get("pos"):
                rows.append({"k": "declared no.", "v": info["number"]})
            if info.get("test"):
                rows.append({"k": "loop test", "v": info["test"]})
            if info.get("guard"):
                rows.append({"k": "guard", "v": f"[{info['guard']}]"})
            if info.get("injected_by"):
                rows.append({"k": "injected by", "v": f"⊕ model {info['injected_by']}"})
            if info["prev"]:
                is_loop_test = info["prev"] == info["parent"]
                nav.append({"dir": "⬅", **chip(info["prev"], f"↺ {self._name(info['prev'])} test"
                                                   if is_loop_test else None)})
            if info["next"]:
                is_loop_test = info["next"] == info["parent"]
                nav.append({"dir": "➡", **chip(info["next"], f"↺ {self._name(info['next'])} test"
                                                   if is_loop_test else None)})
            if info["parent"]:
                nav.append({"dir": "⬆", **chip(info["parent"])})
            nav += [{"dir": "⬇", **chip(c)} for c in info["child_ids"]]
        rows.append({"k": "source", "v": _short_path(info.get("source"))})
        rows.append({"k": "calls", "v": str(self.calls.get(sel, 0))})
        if sel in self.last_ms:
            rows.append({"k": "last time", "v": f"{self.last_ms[sel]:.3f} ms"})
        if sel in self.total_ms:
            rows.append({"k": "total time", "v": _fmt_ms(self.total_ms[sel])})
        self.state.insp = {
            "id": sel,
            "name": info["name"],
            "type": kind_label,
            "color": domain_color(info.get("domain") or info.get("category")),
            "rows": rows,
            "doc": info.get("doc") or "",
            "nav": nav,
            "reads": info.get("reads") or [],
            "writes": info.get("writes") or [],
            "seen": self.writes_seen.get(sel, []),
            "bp": sel in self.state.bps,
        }

    # -- UI -------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        state, ctrl = self.state, self.ctrl
        idle = "!connected || running || finished"
        with SinglePageWithDrawerLayout(self.server) as layout:
            layout.title.set_text("DAG stepper")
            client.Style(_CSS + FIELD_CSS + PIPELINE_CSS)
            # Phones: drawer closed and overlaying instead of pushing the content.
            client.ClientTriggers(mounted=(
                "set('is_mobile', window.innerWidth < 900);"
                " if (window.innerWidth < 900) set('main_drawer', false)"
            ))
            layout.title.classes = "nf-btxt"
            # Debugger keys: F11 into, F10 over, Shift+F11 out, F8 run.
            client.ClientTriggers(mounted=(
                "window.addEventListener('keydown', (e) => {"
                " const k = {F11: e.shiftKey ? 'out' : 'into', F10: 'over', F8: 'run'}[e.key];"
                " if (k) { e.preventDefault(); trigger('nf_key', [k]); } })"
            ))

            with layout.toolbar as tb:
                tb.density = "compact"
                v3.VChip("{{ solver_name }}", size="small", variant="tonal", classes="mr-2 nf-wide",
                         prepend_icon="mdi-function-variant")
                with html.Div(classes="nf-tools"):
                    self._button("Run", "mdi-play", "run", idle, color="primary", variant="flat",
                                 title="Run until breakpoint or end (F8)")
                    self._button("Pause", "mdi-pause", None, "!running", click=self.pause)
                    self._button("Stop", "mdi-stop", None, "!connected || finished", click=self.stop)
                    self._button("Restart", "mdi-restart", None, "false", click=self.restart)
                    v3.VDivider(vertical=True, classes="mx-1")
                    self._button("Into", "mdi-debug-step-into", "into", idle, title="Step into (F11)")
                    self._button("Over", "mdi-debug-step-over", "over", idle, title="Step over (F10)")
                    self._button("Out", "mdi-debug-step-out", "out",
                                 f"{idle} || (cur_kind !== 'init' && depth === 0)",
                                 title="Step out (Shift+F11)")
                    self._button("Next time step", "mdi-skip-next", "time_step", idle)
                    self._button("Skip init", "mdi-fast-forward", "skip_init",
                                 f"{idle} || cur_kind !== 'init'")
                    v3.VDivider(vertical=True, classes="mx-1")
                    with v3.VBtn(size="small", classes="mx-1", click="fv_on = !fv_on",
                                 variant=("fv_on ? 'tonal' : 'text'",),
                                 color=("fv_on ? 'primary' : undefined",),
                                 title="show a solver field on the mesh, refreshed at every pause"):
                        v3.VIcon("mdi-palette", size="small")
                        html.Span("Field", classes="nf-btxt ml-1")
                    with v3.VBtn(size="small", classes="mx-1", click="pp_on = !pp_on",
                                 variant=("pp_on ? 'tonal' : 'text'",),
                                 color=("pp_on ? 'primary' : undefined",),
                                 title="show the post-processing (Catalyst/ParaView) pipeline as a diagram"):
                        v3.VIcon("mdi-graph-outline", size="small")
                        html.Span("Pipeline", classes="nf-btxt ml-1")
                    v3.VDivider(vertical=True, classes="mx-1")
                    html.Span("animation", classes="text-caption mx-2 nf-btxt")
                    v3.VSlider(v_model=("delay_ms",), min=0, max=600, step=10, hide_details=True,
                               density="compact", style="min-width: 90px; max-width: 110px;",
                               title="animation delay while running")
                    v3.VCheckbox(v_model=("follow",), label="follow", hide_details=True,
                                 density="compact", classes="ml-2", style="flex: none;")
                    v3.VCheckbox(v_model=("heat_on",), label="timing", hide_details=True,
                                 density="compact", classes="ml-1", style="flex: none;",
                                 title="tint nodes by their summed run time")

            with layout.drawer as drawer:
                drawer.width = 250
                drawer.temporary = ("is_mobile",)
                html.Div("Containment", classes="nf-h")
                with html.Div(classes="nf-tree"):
                    with html.Div(
                        v_for="n in tree", key="n.id",
                        classes=("{ sel: n.id === selected, cur: n.id === cur_node, done: visited.includes(n.id) && n.id !== cur_node }",),
                        style=("`padding-left: ${12 + n.depth * 14}px`",),
                        click="trigger('nf_select', [n.id])",
                    ):
                        html.Span("{{ bps.includes(n.id) ? '●' : '' }}", classes="nf-bpmark")
                        html.Span(classes="nf-dot", style=("`background:${n.color}`",),
                                  v_if="n.kind !== 'activity'")
                        html.Span("{{ n.kind === 'loop' ? '↺ ' : '' }}{{ n.name }}",
                                  style=("n.kind === 'activity' ? 'font-weight:600; opacity:.75' : ''",))
                html.Div("Legend", classes="nf-h mt-2")
                with html.Div(classes="text-caption px-3", style="line-height: 1.9; opacity: .8;"):
                    html.Div("▭ action (SequentialOp)")
                    html.Div("▢ loop node (IterativeOp), ◇ loop test")
                    html.Div("dashed ▭ ⊕ injected by an optional model")
                    html.Div("red = next to execute (token)")
                    html.Div("green = executed this time step")
                    html.Div("● breakpoint — click the gutter left of a node")
                    html.Div("▾ / ▸ on a loop: collapse / expand its body")
                    html.Div("click a field: ops that wrote (orange) / read (blue) it")

            with layout.content:
                with html.Div(classes=("['nf-grid', { fv: fv_on || pp_on }]",)):
                    with html.Div(classes="nf-canvas"):
                        with html.Div(classes="nf-diagram"):
                            self._canvas()
                        field_pane()
                        self.pp_editor = pipeline_pane()
                    with html.Div(classes="nf-side"):
                        self._inspector()
                    with html.Div(classes="nf-bottom"):
                        with html.Div():
                            html.Div("Simulation console (newest first)", classes="nf-h")
                            with html.Div(classes="nf-console"):
                                html.Div("{{ l.t }}", v_for="(l, i) in logs", key="i", classes=("l.c",))
                        with html.Div():
                            html.Div("Variables (ctx)", classes="nf-h")
                            with html.Div(classes="px-3 py-1 nf-mono"):
                                html.Span("{{ s.k }}={{ s.v }}  ", v_for="s in scalars", key="s.k",
                                          classes="mr-3")
                            with v3.VTable(density="compact", classes="nf-vars"):
                                with html.Thead():
                                    with html.Tr():
                                        for h in ("field", "min", "max", "mean", "last written by"):
                                            html.Th(h)
                                with html.Tbody():
                                    with html.Tr(v_for="f in fields_rows", key="f.name",
                                                 classes=("{ changed: f.changed, hl: f.field === hl_field }",),
                                                 click="trigger('nf_field', [f.field])",
                                                 style="cursor: pointer;",
                                                 title="highlight the ops that write / read this field"):
                                        html.Td("{{ f.name }}", classes="nf-mono")
                                        html.Td("{{ f.min }}")
                                        html.Td("{{ f.max }}")
                                        html.Td("{{ f.mean }}")
                                        html.Td("{{ f.written_by }}", style="opacity:.7")
        _ = state, ctrl

    def _button(self, text: str, icon: str, mode: Optional[str], disabled: str,
                click: Any = None, **kw: Any) -> None:
        """Toolbar button; the label hides on narrow screens, leaving the icon."""
        kw.setdefault("variant", "text")
        with v3.VBtn(size="small", classes="mx-1", disabled=(disabled,), title=kw.pop("title", text),
                     click=click or (lambda: self.step(mode)), **kw):
            v3.VIcon(icon, size="small")
            html.Span(text, classes="nf-btxt ml-1")

    def _canvas(self) -> None:
        html.Div("{{ status }}", classes="nf-status",
                 style=("failed ? 'color:#d22' : running ? '' : 'color:#e0362c'",))
        cls_node = "props.id === cur_node && cur_kind !== 'test'"
        with NodeEditor(style="width: 100%; height: 100%;",
                        node_click=(self.select, "[$event.node.id]")) as editor:
            self.editor = editor
            Background(gap=16, size=1, pattern_color="#c3c8d2")
            Controls(show_interactive=False)
            MiniMap(pannable=True, zoomable=True, v_show="!is_mobile && !fv_on && !pp_on")

            with CustomNode("action"):
                with html.Div(classes=(
                    "['nf-node', 'nf-action', { cur: " + cls_node + ", sel: props.id === selected,"
                    " done: visited.includes(props.id) && props.id !== cur_node, inj: !!props.data.injected_by,"
                    " hlw: hl[props.id] === 'w', hlr: hl[props.id] === 'r' }]",
                ), style=(_HEAT,)):
                    Handle(position="top", type="target", id="t")
                    Handle(position="bottom", type="source", id="b")
                    Handle(position="right", type="source", id="r")
                    html.Span(classes=("['nf-bp', { on: bps.includes(props.id) }]",),
                              raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.id])"'])
                    html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                    html.Span("{{ props.data.label }}", classes="nf-label")
                    html.Span("{{ heat_ms[props.id] || (props.data.guard ? '[' + props.data.guard + '] '"
                              " : '') + props.data.number }}", classes="nf-meta")
                    html.Span("⊕ {{ props.data.injected_by }}", classes="nf-badge",
                              v_if="props.data.injected_by")

            with CustomNode("init"):
                with html.Div(classes=(
                    "['nf-node', 'nf-init', { cur: " + cls_node + ", sel: props.id === selected,"
                    " done: visited.includes(props.id) && props.id !== cur_node }]",
                ), style=(_HEAT,)):
                    Handle(position="top", type="target", id="t")
                    Handle(position="bottom", type="source", id="b")
                    html.Span(classes=("['nf-bp', { on: bps.includes(props.id) }]",),
                              raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.id])"'])
                    html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                    html.Span("{{ props.data.label }}", classes="nf-label")
                    html.Span("{{ heat_ms[props.id] }}", classes="nf-meta", v_if="heat_ms[props.id]")

            with CustomNode("loop"):
                with html.Div(classes=(
                    "['nf-node', 'nf-loop', { active: active_loops.includes(props.id),"
                    " collapsed: props.data.collapsed, sel: props.data.collapsed && props.id === selected,"
                    " hlw: hl[props.id] === 'w', hlr: hl[props.id] === 'r' }]",
                ), style=("props.data.collapsed ? " + _HEAT + " : ''",)):
                    Handle(position="top", type="target", id="t")
                    Handle(position="bottom", type="source", id="b")
                    with html.Div(classes="nf-loop-title"):
                        html.Span("{{ props.data.collapsed ? '▸' : '▾' }}", classes="nf-tog",
                                  title="collapse / expand",
                                  raw_attrs=['@click.stop="trigger(\'nf_collapse\', [props.id])"'])
                        html.Span("«loop» {{ props.data.number }} {{ props.data.label }}")
                    html.Div("test: {{ props.data.test }}", classes="nf-loop-test")
                    html.Div("{{ heat_ms[props.id] ? heat_ms[props.id] + ' · ' : '' }}"
                             "{{ props.data.size }} actions", classes="nf-loop-size",
                             v_if="props.data.collapsed")
                    html.Span(classes=("['nf-bp', { on: bps.includes(props.id) }]",),
                              v_if="props.data.collapsed",
                              raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.id])"'])

            with CustomNode("decision"):
                with html.Div(classes=(
                    "['nf-decision', { cur: props.data.loop === cur_node && cur_kind === 'test',"
                    " sel: props.data.loop === selected }]",
                )):
                    Handle(position="top", type="target", id="t")
                    Handle(position="bottom", type="source", id="b")
                    Handle(position="right", type="target", id="r")
                    html.Div(classes="d")
                    html.Span(classes=("['nf-bp', { on: bps.includes(props.data.loop) }]",),
                              raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.data.loop])"'])

            with CustomNode("activity"):
                with html.Div(classes="nf-node nf-activity"):
                    Handle(position="top", type="target", id="t")
                    Handle(position="bottom", type="source", id="b")
                    html.Div("{{ props.data.label }}", classes="nf-activity-title")

            with CustomNode("initial"):
                with html.Div(classes="nf-initial"):
                    Handle(position="bottom", type="source", id="b")

            with CustomNode("final"):
                with html.Div(classes=("['nf-final', { cur: cur_node === 'end' }]",)):
                    Handle(position="top", type="target", id="t")

            with CustomNode("placeholder"):
                with html.Div(classes="nf-node nf-placeholder"):
                    html.Span("execution graph appears after initialization (it needs the run context)")

        editor._attributes["min_zoom"] = ':min-zoom="0.1"'

    def _inspector(self) -> None:
        html.Div("Inspector", classes="nf-h")
        with html.Div(v_if="insp"):
            with html.Div(classes="nf-sect"):
                with html.Div(classes="px-3 pb-1 d-flex align-center", style="gap: 8px;"):
                    html.Span(classes="nf-dot", style=("`background:${insp.color}`",))
                    html.Span("{{ insp.name }}", classes="text-subtitle-1 font-weight-bold")
                html.Div("{{ insp.type }}", classes="px-3 text-caption", style="opacity:.7")
                with html.Div(classes="nf-kv mt-1"):
                    with html.Template(v_for="r in insp.rows", key="r.k"):
                        html.Span("{{ r.k }}")
                        html.Span("{{ r.v }}", classes="nf-mono", style="word-break: break-all;")
                html.Div("{{ insp.doc }}", v_if="insp.doc", classes="px-3 mt-2 text-body-2",
                         style="opacity:.8")
            with html.Div(classes="nf-sect"):
                html.Div("Navigate", classes="nf-h")
                with html.Div(classes="px-3"):
                    v3.VChip("{{ n.dir }} {{ n.name }}", v_for="n in insp.nav", key="n.dir + n.id",
                             size="small", classes="mr-1 mb-1", variant="tonal",
                             click="trigger('nf_select', [n.id])")
            with html.Div(classes="nf-sect", v_if="insp.seen.length"):
                html.Div("Observed writes", classes="nf-h")
                with html.Div(classes="px-3"):
                    v3.VChip("{{ f }}", v_for="f in insp.seen", key="'s' + f", size="x-small",
                             color="warning", variant="tonal", classes="mr-1 mb-1",
                             prepend_icon="mdi-pencil", click="trigger('nf_field', [f])")
            with html.Div(classes="nf-sect", v_if="insp.reads.length || insp.writes.length"):
                html.Div("Reads / writes (declared)", classes="nf-h")
                with html.Div(classes="px-3"):
                    v3.VChip("{{ f }}", v_for="f in insp.reads", key="'r' + f", size="x-small",
                             classes="mr-1 mb-1", prepend_icon="mdi-arrow-down")
                    v3.VChip("{{ f }}", v_for="f in insp.writes", key="'w' + f", size="x-small",
                             color="warning", classes="mr-1 mb-1", prepend_icon="mdi-pencil")
            with html.Div(classes="nf-sect px-3 pt-2"):
                v3.VBtn("{{ insp.bp ? 'Remove breakpoint' : 'Add breakpoint' }}", size="small",
                        variant="tonal", classes="mr-2", click="trigger('nf_toggle_bp', [insp.id])")
                v3.VBtn("Run to here", size="small", variant="tonal",
                        disabled=("!connected || running || finished",),
                        click=(lambda node: self.step("run_to", node), "[insp.id]"))
        html.Div("Select a node.", v_if="!insp", classes="px-3 text-caption")
        html.Div("Call stack", classes="nf-h mt-2")
        with html.Div(classes="nf-stack"):
            html.Div("{{ c }}", v_for="(c, i) in callstack", key="i")
        html.Div("Breakpoints", classes="nf-h mt-2")
        with html.Div(classes="nf-tree"):
            with html.Div(v_for="b in bp_list", key="b.id", click="trigger('nf_select', [b.id])"):
                html.Span("●", classes="nf-bpmark")
                html.Span("{{ b.name }}")
                v3.VSpacer()
                v3.VIcon("mdi-close", size="x-small", raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [b.id])"'])
        html.Div("Click the gutter left of a node to add one.", v_if="!bp_list.length",
                 classes="px-3 text-caption", style="opacity:.6")


#: Background of a node in the timing heat map (``heat`` holds a CSS colour per node id).
_HEAT = "heat[props.id] ? `background:${heat[props.id]}` : ''"


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms:.1f} ms"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _short_path(path: Optional[str]) -> str:
    if not path:
        return "—"
    for root in (os.getcwd(), os.path.dirname(os.path.dirname(__file__))):
        if path.startswith(root + os.sep):
            return os.path.relpath(path, root)
    return path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--solver", default=DEFAULT_SOLVER, help="module:Factory of the solver")
    parser.add_argument("--case", help="case directory the worker runs in (default: cwd)")
    parser.add_argument("--python", help="interpreter for the worker, e.g. a NeoFOAM venv")
    parser.add_argument("--np", type=int, default=1, help="MPI ranks (decomposed case)")
    parser.add_argument("--mpirun", default="mpirun", help="MPI launcher command")
    parser.add_argument("--catalyst", metavar="PREFIX",
                        help=f"Catalyst 2 + ParaView install for the in-situ field view "
                             f"(default ${CATALYST_ENV}); ParaView must be built for the worker's Python")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--no-browser", action="store_true")
    args, _ = parser.parse_known_args(argv)
    server = get_server("neofoam_ui", client_type="vue3")
    StepperApp(server, args.solver, case=args.case, python=args.python, np=args.np,
               mpirun=args.mpirun, catalyst=args.catalyst)
    # timeout=0: keep serving when no browser is connected (wslink's default exits after 300 s).
    server.start(port=args.port, host=args.host, open_browser=not args.no_browser, timeout=0)


if __name__ == "__main__":
    main()
