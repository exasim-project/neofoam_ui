"""The DAG pane: the activity diagram of the run, its token, breakpoints, timing and highlights.

Closed (or no visible browser tab) the worker is told nobody watches (``watch``): a run then
sends no per-event messages at all. The inspector and the containment tree (drawer) belong
to it too; the shell places them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

from neofoam_ui._ui._format import _fmt_ms
from neofoam_ui._ui._layout._dag import _build_flow
from neofoam_ui._ui.panes import _dag_view
from neofoam_ui._ui.panes._inspector import _inspector_model
from neofoam_ui.extensions import BasePane, PaneToggle

if TYPE_CHECKING:
    from neofoam_ui._ui._app import StepperApp

__all__ = ()


class _DagPane(BasePane):
    name = "dag"
    toggle = PaneToggle("dag_on", "DAG", "mdi-sitemap-outline",
                        "the activity diagram; closed, a run reports no events and the solver runs as without the UI")
    placement = "main"

    def __init__(self, app: StepperApp) -> None:
        super().__init__(app)
        self.app = app
        self.model = app.model
        self.editor: Any = None
        #: Visited nodes, applied to the state at the next push.
        self._visited: list[str] = []
        self._visited_dirty = False
        #: Browser tabs showing the app (visible ones); none = nobody watches the DAG.
        self.viewers = 0
        self._watching: Optional[bool] = None
        app.pusher.before_push.append(self._apply_visited)
        state = app.state
        state.change("dag_on")(lambda **_: self.send_watch())
        state.change("dag_on")(lambda **_: self._fit())
        state.change("heat_on")(lambda **_: self._refresh_heat())

    # -- pane -----------------------------------------------------------------------------

    def reset(self) -> None:
        s = self.app.state
        s.visited = []
        self._visited, self._visited_dirty = [], False
        s.bps = s.bps if isinstance(s.bps, list) else []
        s.collapsed = s.collapsed if isinstance(s.collapsed, list) else []
        s.heat_on = bool(s.heat_on)
        s.heat = {}
        s.heat_ms = {}
        s.hl_field = None
        s.hl = {}
        s.selected = None
        s.tree = []
        s.regions = []
        s.insp = None
        s.bp_list = []
        # Closed, a run reports no events (the solver runs as without the UI).
        s.dag_on = True if s.dag_on is None else bool(s.dag_on)
        if self.editor is not None:
            self.editor.clear_graph()

    def build(self) -> None:
        sides = [p.toggle.key for p in self.app.panes if p.placement == "side" and p.toggle]
        _dag_view._canvas(self, " && ".join(["!is_mobile", *(f"!{k}" for k in sides)]))
        self.editor._attributes["min_zoom"] = ':min-zoom="0.1"'

    def register_triggers(self) -> None:
        """The templates call these by name; after the page is built (the diagram's own click
        handler keeps its generated name)."""
        server = self.app.server
        server.trigger("nf_toggle_bp")(self.toggle_bp)
        server.trigger("nf_select")(self.select)
        server.trigger("nf_collapse")(self.toggle_collapse)
        server.trigger("nf_field")(self.highlight_field)

    def build_inspector(self) -> None:
        _dag_view._inspector(self.app.step)

    def build_drawer(self, drawer: Any) -> None:
        _dag_view._drawer(drawer)

    def handlers(self) -> Mapping[str, Any]:
        return {"graph": self._on_graph, "log": self._on_log, "paused": self._on_paused}

    def spawned(self) -> None:
        self.app.send({"cmd": "breakpoints", "ids": list(self.app.state.bps)})
        self.send_watch(force=True)

    # -- who watches -------------------------------------------------------------------

    def viewer(self, visible: bool) -> None:
        """A browser tab appeared / became visible (or the reverse)."""
        self.viewers = max(0, self.viewers + (1 if visible else -1))
        self.send_watch()

    def send_watch(self, force: bool = False) -> None:
        on = bool(self.app.state.dag_on) and self.viewers > 0
        if force or on != self._watching:
            self._watching = on
            self.app.send({"cmd": "watch", "on": on})

    def _fit(self) -> None:
        if self.editor is not None and self.app.state.dag_on:
            self.editor.fit_view()

    # -- messages ------------------------------------------------------------------------

    def _on_graph(self, msg: dict[str, Any]) -> None:
        self.rebuild()

    def _on_log(self, msg: dict[str, Any]) -> None:
        name, console = self.model.name, self.app.console
        scope = " › ".join(f"{name(lid)}#{it}" for lid, it in msg["stack"]) or "—"
        t = msg.get("time")
        prefix = f"[t={t:.4g}] " if isinstance(t, (int, float)) else ""
        visited = self._visited
        if msg["kind"] == "init":
            console(f"{prefix}init ▶ {msg['name']}  ({msg['ms']:.2f} ms)", "init")
            visited.append(msg["node"])
        elif msg["kind"] == "exec":
            console(f"{prefix}{scope}  ▶ {msg['name']}  ({msg['ms']:.2f} ms)", "")
            visited.append(msg["node"])
        else:
            console(f"{prefix}{scope}  ◇ {msg['name']} test #{msg['iter']} → {msg['result']}", "test")
            if not msg["stack"]:  # new time step: forget what ran in the previous one
                self._visited = [v for v in visited if v.startswith("init:")]
        self._visited_dirty = True

    def _apply_visited(self) -> None:
        if self._visited_dirty:
            self.app.state.visited = list(self._visited)
            self._visited_dirty = False

    def _on_paused(self, msg: dict[str, Any]) -> None:
        s, ev = self.app.state, msg["event"]
        self._refresh_heat()
        self._refresh_highlight()
        if s.follow and ev["node"]:
            self.select(ev["node"], pan=True)
        else:
            self._refresh_inspector()

    # -- the diagram -----------------------------------------------------------------------

    def rebuild(self, fit: bool = True) -> None:
        s = self.app.state
        s.tree = self.model.reindex(s.solver_name)
        s.regions = self.model.regions()
        collapsed = frozenset(s.collapsed or ())
        self.editor.graph = _build_flow(self.model.init_steps, self.model.ops or None, s.solver_name, collapsed)
        if fit:
            self.editor.fit_view()
        self._refresh_bp_list()

    def toggle_bp(self, node_id: str) -> None:
        node_id = node_id.removesuffix("::test")
        bps = list(self.app.state.bps)
        if node_id in bps:
            bps.remove(node_id)
        else:
            bps.append(node_id)
        self.app.state.bps = bps
        self.app.send({"cmd": "breakpoints", "ids": bps})
        self._refresh_bp_list()
        self._refresh_inspector()

    def select(self, node_id: Optional[str], pan: bool = False) -> None:
        if not node_id:
            return
        node_id = node_id.removesuffix("::test")
        if node_id.startswith("act:") or node_id in ("start", "exec:placeholder"):
            return
        s, index = self.app.state, self.model.index
        s.selected = node_id
        self._refresh_inspector()
        if pan and node_id in index:
            shown = self.model.shown(node_id, s.collapsed)
            target = (f"{shown}::test" if index[shown]["kind"] == "loop"
                      and shown not in s.collapsed else shown)
            self.app.server.js_call(getattr(self.editor, "_NodeEditor__ref"), "fitView",
                                    {"nodes": [target], "duration": 250, "maxZoom": 1.0, "minZoom": 0.5,
                                     "padding": 1.2})

    def toggle_collapse(self, loop_id: str) -> None:
        collapsed = list(self.app.state.collapsed)
        if loop_id in collapsed:
            collapsed.remove(loop_id)
        else:
            collapsed.append(loop_id)
        self.app.state.collapsed = collapsed
        self.rebuild(fit=False)
        self._refresh_heat()

    def _refresh_heat(self) -> None:
        """Tint actions, init steps and collapsed loops by their share of the summed time."""
        s, m = self.app.state, self.model
        if not s.heat_on:
            s.heat, s.heat_ms = {}, {}
            return
        times = {k: v for k, v in m.total_ms.items()
                 if k in m.index and m.index[k]["kind"] in ("action", "init")}
        times.update({lid: m.subtree_ms(lid) for lid in s.collapsed if lid in m.index})
        top = max(times.values(), default=0.0) or 1.0
        s.heat = {k: f"hsl(28, 95%, {96 - 42 * (v / top):.0f}%)" for k, v in times.items()}
        s.heat_ms = {k: _fmt_ms(v) for k, v in times.items()}

    def highlight_field(self, field: str) -> None:
        s = self.app.state
        s.hl_field = None if s.hl_field == field else field
        self._refresh_highlight()

    def _refresh_highlight(self) -> None:
        """Ops that wrote (observed or declared) or read (declared) the highlighted field."""
        s, m = self.app.state, self.model
        field, hl = s.hl_field, {}
        if field:
            for op_id, info in m.index.items():
                if info["kind"] == "init":
                    continue
                if field in m.writes_seen.get(op_id, ()) or field in (info.get("writes") or ()):
                    hl[op_id] = "w"
                elif field in (info.get("reads") or ()):
                    hl[op_id] = "r"
            for op_id in list(hl):  # a collapsed loop stands in for its body
                shown = m.shown(op_id, s.collapsed)
                if shown != op_id and hl.get(shown) != "w":
                    hl[shown] = hl[op_id]
        s.hl = hl

    def _refresh_bp_list(self) -> None:
        s = self.app.state
        s.bp_list = [{"id": b, "name": self.model.name(b)} for b in s.bps]

    def _refresh_inspector(self) -> None:
        s = self.app.state
        s.insp = _inspector_model(self.model, s.selected, s.bps)
