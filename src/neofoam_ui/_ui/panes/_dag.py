"""The DAG pane: the activity diagram of the run, its token, breakpoints, timing and highlights.

The step view: it shows one graph at a time, the initialization or the execution (``dag_view``: ``"init"`` /
``"exec"``, a toolbar toggle). The view follows the run's phase: it switches to ``init`` when
an init step runs and to ``exec`` when the first execution op does; a choice of the user holds
until the next change of phase. ``dag_view`` is UI state only, the worker never sees it.

Hidden (``dag_on`` false: the step view closed or behind another tab, or no visible browser
tab) the worker is told nobody watches (``watch``): a run then sends no per-event messages at
all. The inspector, call stack, breakpoints, containment tree and legend belong to it too:
side panels (:meth:`side_panels`), the shell places them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional

from trame.widgets import client

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
    toggle = PaneToggle("dag_on", "Step view", "mdi-sitemap-outline",
                        "the activity diagram; hidden, a run reports no events and the solver runs as without the UI",
                        short="Step")
    placement = "main"

    def __init__(self, app: StepperApp) -> None:
        super().__init__(app)
        self.app = app
        self.model = app.model
        self.editor: Any = None
        #: Visited nodes, applied to the state at the next push.
        self._visited: list[str] = []
        self._visited_dirty = False
        #: The loops drawn collapsed now (the user's ``collapsed`` at the last rebuild).
        self._shown: frozenset[str] = frozenset()
        #: The run's phase (``"init"`` / ``"exec"``) the view last followed, and the view drawn.
        self._phase: Optional[str] = None
        self._drawn: Optional[str] = None
        #: Regions seen in this or an earlier run (kept over restarts): the init view draws
        #: their boxes before they initialize. The worker does not name them up front.
        self._regions: list[str] = []
        #: The node drawn for each op.
        self._node_of: dict[str, str] = {}
        #: The boxes drawn closed now (the user's ``boxes_closed`` but those a token or a
        #: breakpoint is in).
        self._closed: frozenset[str] = frozenset()
        #: The regions in the order of the execution view's boxes (kept over restarts).
        self._box_order: list[str] = []
        #: The coupling line of each coupling op (its key), and the line's two regions.
        self._line_of: dict[str, list[str]] = {}
        self._line_name: dict[str, Optional[str]] = {}
        self._line_regions: dict[str, tuple[str, str]] = {}
        #: Browser tabs showing the app (visible ones); none = nobody watches the DAG.
        self.viewers = 0
        self._watching: Optional[bool] = None
        app.pusher.before_push.append(self._apply_visited)
        state = app.state
        state.change("dag_on")(lambda **_: self.send_watch())
        state.change("dag_on")(lambda **_: self._fit())
        state.change("heat_on")(lambda **_: self._refresh_heat())
        state.change("dag_view")(lambda **_: self._view_changed())

    # -- pane -----------------------------------------------------------------------------

    def reset(self) -> None:
        s = self.app.state
        s.visited = []
        self._visited, self._visited_dirty = [], False
        s.bps = s.bps if isinstance(s.bps, list) else []
        # the ops marked final run in every pass (kept over restarts, as the breakpoints)
        s.every_pass = s.every_pass if isinstance(s.every_pass, list) else []
        s.collapsed = s.collapsed if isinstance(s.collapsed, list) else []
        s.boxes_closed = s.boxes_closed if isinstance(s.boxes_closed, list) else []
        s.loop_res = {}
        # the loops in their final pass (``ctx.loops[-1].final``), by node
        s.loop_final = {}
        self._shown = frozenset()
        self._phase = self._drawn = None
        s.dag_view = "init"
        s.ex_vals, s.ex_hot = {}, []
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
        _dag_view._canvas(self, "!nf_phone")
        self.editor._attributes["min_zoom"] = ':min-zoom="0.1"'
        # the diagram mounts whenever the step view opens (or the page loads): it gets the graph
        client.ClientTriggers(mounted="trigger('nf_dag_mounted')")

    def register_triggers(self) -> None:
        """The templates call these by name; after the page is built (the diagram's own click
        handler keeps its generated name)."""
        server = self.app.server
        server.trigger("nf_toggle_bp")(self.toggle_bp)
        server.trigger("nf_every_pass")(self.toggle_every_pass)
        server.trigger("nf_select")(self.select)
        server.trigger("nf_collapse")(self.toggle_collapse)
        server.trigger("nf_box")(self.toggle_box)
        server.trigger("nf_field")(self.highlight_field)
        server.trigger("nf_dag_mounted")(self.mounted)

    def side_panels(self) -> dict[str, Callable[[], None]]:
        """The narrow panels of the diagram, by panel id (the shell's sidebars)."""
        return {"tree": _dag_view._tree, "callstack": _dag_view._callstack,
                "breakpoints": _dag_view._breakpoints,
                "inspector": lambda: _dag_view._inspector(self.app.step), "legend": _dag_view._legend}

    def mounted(self) -> None:
        """A diagram mounted in a browser (the step view opened, or the page loaded): its graph
        is pushed (not state), so send it again, and fit it."""
        if self.editor is not None:
            self.editor.graph = self.editor.graph
            self.editor.fit_view()

    def handlers(self) -> Mapping[str, Any]:
        return {"graph": self._on_graph, "log": self._on_log, "paused": self._on_paused}

    def spawned(self) -> None:
        self.app.send({"cmd": "breakpoints", "ids": list(self.app.state.bps)})
        if self.app.state.every_pass:
            self.app.send({"cmd": "every_pass", "ids": list(self.app.state.every_pass)})
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
        self._follow_phase("init" if msg["kind"] == "init" else "exec")
        if msg["kind"] in ("exec", "test"):
            self._exchanged(msg)
        if msg["kind"] == "init":
            console(f"{prefix}init ▶ {msg['name']}  ({msg['ms']:.2f} ms)", "init")
            visited.append(msg["node"])
        elif msg["kind"] == "exec":
            console(f"{prefix}{scope}  ▶ {msg['name']}  ({msg['ms']:.2f} ms)", "")
            visited.append(msg["node"])
        else:
            res = msg.get("residuals")
            note = ("  res " + " ".join(f"{k} {v:.2e}" for k, v in res.items())) if res else ""
            console(f"{prefix}{scope}  ◇ {msg['name']} test #{msg['iter']} → {msg['result']}{note}", "test")
            self._loop_final(msg["node"], bool(msg["result"] and msg.get("final")))
            if res:
                self.model.residuals[msg["node"]] = res
                self.app.state.loop_res = {**self.app.state.loop_res,
                                           msg["node"]: "res " + " ".join(f"{v:.1e}" for v in res.values())}
            if not msg["stack"]:  # new time step: forget what ran in the previous one (its region's)
                region = msg.get("region")
                self._visited = [v for v in visited if v.startswith("init:")
                                 or (region is not None and not v.startswith(f"{region}/"))]
        self._visited_dirty = True

    def _loop_final(self, node: str, final: bool) -> None:
        """Loop ``node`` runs its final pass now (its test said so), or not."""
        s = self.app.state
        if bool((s.loop_final or {}).get(node)) == final:
            return
        shown = {k: v for k, v in (s.loop_final or {}).items() if k != node}
        s.loop_final = {**shown, node: True} if final else shown

    def _apply_visited(self) -> None:
        if self._visited_dirty:
            self.app.state.visited = list(self._visited)
            self._visited_dirty = False

    def _on_paused(self, msg: dict[str, Any]) -> None:
        s, ev = self.app.state, msg["event"]
        if self._boxes_shut() != self._closed:  # the token entered or left a closed box
            self.rebuild(fit=False)
        if ev["kind"] in ("init", "exec", "test"):
            self._follow_phase("init" if ev["kind"] == "init" else "exec")
        # the lines of a coupling action next to run (or of a coupling loop whose test exchanges
        # next) are hot, in any region; any other pause cools them
        keys: list[str] = []
        for e in _paused_events(ev):
            if e["kind"] in ("exec", "test"):
                keys += [k for k in self._line_of.get(e["node"] or "", []) if k not in keys]
        if (s.ex_hot or []) != keys:
            s.ex_hot = list(keys)
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
        self._regions += [r["name"] for r in s.regions if r["name"] not in self._regions]
        collapsed = self._shown = frozenset(s.collapsed or ())
        view = self._drawn = "init" if s.dag_view == "init" else "exec"
        self._closed = self._boxes_shut()
        graph = _build_flow(self.model.init_steps, self.model.ops or None, s.solver_name, collapsed, view,
                            tuple(self._regions), self._closed)
        self._node_of, self._line_of, self._line_regions = {}, {}, {}
        boxes = sorted((n["position"]["x"], n["id"][len("exec::box:"):]) for n in graph["nodes"]
                       if n["id"].startswith("exec::box:"))
        if boxes:
            self._box_order = [r for _, r in boxes]
        for n in graph["nodes"]:
            ref = (n.get("data") or {}).get("ref") or n["id"]
            self._node_of.setdefault(ref, n["id"])
            if n["type"] == "exmap":
                data = n["data"]
                self._line_regions[data["key"]] = (data["left"], data["right"])
                self._line_name[data["key"]] = data.get("name")
                for ref in data["nodes"]:  # (a coupling op may sit on several lines)
                    self._line_of.setdefault(ref, []).append(data["key"])
        self.editor.graph = graph
        if fit:
            self.editor.fit_view()
        self._refresh_bp_list()

    def _follow_phase(self, phase: str) -> None:
        """The run entered ``phase``: show its view, once per change of phase."""
        if phase == self._phase:
            return
        self._phase = phase
        if self.app.state.dag_view != phase:
            self.app.state.dag_view = phase
            self.rebuild()

    def _exchanged(self, msg: dict[str, Any]) -> None:
        """A coupling action ran, or a coupling loop's test exchanged: its lines are hot, and show
        what each port was handed (``exchanges``; a coupling action's single ``exchange``)."""
        s = self.app.state
        keys = self._line_of.get(msg["node"], [])
        if (s.ex_hot or []) != keys:
            s.ex_hot = list(keys)
        items = msg.get("exchanges") or ([msg["exchange"]] if msg.get("exchange") else [])
        if not keys or not items:
            return
        vals_all = dict(s.ex_vals or {})
        for item in items:
            ends = {str(item.get("from")), str(item.get("to"))}
            key = next((k for k in keys if item.get("exchange") is not None
                        and self._line_name.get(k) == item["exchange"]), None) or \
                next((k for k in keys if set(map(str, self._line_regions.get(k, ()))) == ends), None)
            if key is None:
                continue
            text = " ".join(f"{name} = {value}" for name, value in item.get("data", {}).items())
            vals_all[key] = {**vals_all.get(key, {}), str(item.get("from")): text}
        s.ex_vals = vals_all

    def _view_changed(self) -> None:
        if self.editor is not None and self.app.state.dag_view != self._drawn:
            self.rebuild()

    def toggle_bp(self, node_id: str) -> None:
        node_id = node_id.removesuffix("::test")
        bps = list(self.app.state.bps)
        if node_id in bps:
            bps.remove(node_id)
        else:
            bps.append(node_id)
        self.app.state.bps = bps
        self.app.send({"cmd": "breakpoints", "ids": bps})
        if self._boxes_shut() != self._closed:  # a breakpoint opens its box
            self.rebuild(fit=False)
        self._refresh_bp_list()
        self._refresh_inspector()

    def toggle_every_pass(self, node_id: str) -> None:
        """Run an op marked final in every pass of its wrapping loops, or in the final one only
        again: in its region's context (each region has its own), from the next run of the op.

        The solver names ops by name: every node of the region with the op's name goes along."""
        info = self.model.index.get(node_id)
        if not info or not info.get("final"):
            return
        same = [i for i, o in self.model.index.items() if o.get("final") and o.get("region") == info.get("region")
                and o.get("domain") == info.get("domain") and o.get("name") == info.get("name")]
        ids = [i for i in (self.app.state.every_pass or []) if i not in same]
        if node_id not in (self.app.state.every_pass or []):
            ids += same
        self.app.state.every_pass = ids
        self.app.send({"cmd": "every_pass", "ids": ids})

    def select(self, node_id: Optional[str], pan: bool = False) -> None:
        if not node_id:
            return
        node_id = node_id.removesuffix("::test")
        if node_id in ("act:init", "act:exec"):  # the tree's headings: show that view
            self.app.state.dag_view = node_id[4:]
            return
        if node_id.startswith(("act:", "exec::box", "init::box", "exmap:")) or node_id in ("start", "end", "exec:placeholder"):
            return
        s, index = self.app.state, self.model.index
        if node_id in index and (index[node_id]["kind"] == "init") != (s.dag_view == "init"):
            s.dag_view = "init" if index[node_id]["kind"] == "init" else "exec"
            self.rebuild(fit=False)
        s.selected = node_id
        self._refresh_inspector()
        if pan and node_id in index:
            shown = self.model.shown(node_id, self._shown)
            region = index[shown].get("region")
            if region and s.dag_view != "init":  # region boxes: show the box holding it
                box = f"exec::box:{region}"
                self.app.server.js_call(getattr(self.editor, "_NodeEditor__ref"), "fitView",
                                        {"nodes": [box], "duration": 250, "maxZoom": 1.0, "minZoom": 0.2,
                                         "padding": 0.06})
                return
            drawn = self._node_of.get(shown, shown)
            target = (f"{drawn}::test" if index[shown]["kind"] == "loop"
                      and shown not in self._shown else drawn)
            self.app.server.js_call(getattr(self.editor, "_NodeEditor__ref"), "fitView",
                                    {"nodes": [target], "duration": 250, "maxZoom": 1.0, "minZoom": 0.5,
                                     "padding": 1.2})

    def region_order(self) -> list[str]:
        """The regions in the order of the execution view's boxes (else as they appeared)."""
        boxes = [r for r in self._box_order if r in self._regions]
        return boxes + [r for r in self._regions if r not in boxes]

    def _boxes_shut(self) -> frozenset[str]:
        """The region boxes drawn closed: the user's, but those a token or a breakpoint is in."""
        s, index = self.app.state, self.model.index
        tokens = [s.cur_node, *(getattr(s, "cur_nodes", None) or ()), *(getattr(s, "cur_tests", None) or ())]
        inside = {index.get(n, {}).get("region") for n in [*tokens, *(s.bps or ())] if n}
        return frozenset(s.boxes_closed or ()) - inside

    def toggle_box(self, region: str) -> None:
        """Close a region's box to its coupling ops, or open it again."""
        s = self.app.state
        closed = list(s.boxes_closed or ())
        if region in closed:
            closed.remove(region)
        else:  # (a box holding the token or a breakpoint stays open until they leave)
            closed.append(region)
        s.boxes_closed = closed
        self.rebuild(fit=False)

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
        times.update({lid: m.subtree_ms(lid) for lid in self._shown if lid in m.index})
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
                shown = m.shown(op_id, self._shown)
                if shown != op_id and hl.get(shown) != "w":
                    hl[shown] = hl[op_id]
        s.hl = hl

    def _refresh_bp_list(self) -> None:
        s = self.app.state
        s.bp_list = [{"id": b, "name": self.model.name(b)} for b in s.bps]

    def _refresh_inspector(self) -> None:
        s = self.app.state
        s.insp = _inspector_model(self.model, s.selected, s.bps)


def _paused_events(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """The events the run is paused before: one per paused region (a multi-region solver),
    else ``ev`` itself."""
    regions = ev.get("regions")
    if not regions:
        return [ev]
    return [r["event"] for r in regions if r["state"] == "paused" and r.get("event")]
