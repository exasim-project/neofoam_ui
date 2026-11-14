"""The scene editor: the current scene as a node graph (trame-flow), its palette and the selected
node's property form; Apply shows the draft, Revert drops it.

A palette entry clicked adds its node after the selected one, linked when the ports fit; one
dragged onto the canvas is placed unlinked. Ports are typed (data → Display → View): a link
between ports of different types is refused while dragging, and a node that takes one input
keeps the newest; the ports are named by the node models. A right-click on a node (not ``grid``
or the View) or a link offers to remove it, as Del / Backspace do on the selected one: removals,
like every edit, change the draft on the server (Vue Flow's own delete is off). Each node shows
its last execute time and, red, why it failed (the solver's build, or the check at Apply).
Drafts are kept per scene until Apply or Revert.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_flow.widgets.flow import Background, Controls, CustomNode, Handle, NodeEditor

from neofoam_ui._ui.panes import _scene_form
from neofoam_ui._ui.panes._scenes import _Scenes
from neofoam_ui.extensions import BasePane, PaneContext, PaneToggle
from neofoam_ui.scenes import inputs, ordered
from neofoam_ui.scenes.models import AnyNode

__all__ = ()

#: The palette: its groups and their node types.
_PALETTE = [("source", ["grid"]),
            ("filters", ["Surface", "Slice", "Clip", "Contour", "Threshold", "Calculator", "Glyph", "StreamTracer"]),
            ("output", ["Display", "View"])]
#: Each node type's model: its ports (``takes``, ``gives``, ``many``) and their names.
_PORTS: dict[str, Any] = {node.model_fields["type"].annotation.__args__[0]: node
                          for node in AnyNode.__origin__.__args__}  # type: ignore[attr-defined]
#: Nodes the editor does not remove (no menu, Del does nothing): the scene's source and its View.
_FIXED = ("grid", "View")
_ROLE = {"grid": "source", "Display": "display", "View": "view"}
#: Layout of a scene without one: layers by depth, this far apart.
_DX, _DY = 200, 110
#: The canvas never zooms out further: nodes stay readable (pan to the rest).
_MIN_ZOOM = 0.75
#: A link's ends must be ports of one type (a data port to a data port, a display to the View).
_VALID_LINK = "(c) => c.source !== c.target && c.sourceHandle === c.targetHandle"

_EDITOR_CSS = r"""
.nf-se { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.nf-se-top { display: flex; flex: 1 1 auto; min-height: 0; }
.nf-se-pal { flex: 0 0 112px; padding: 8px; display: flex; flex-direction: column; gap: 3px; overflow: auto;
  border-right: 1px solid rgba(128,128,128,.2); font-size: 12px; }
.nf-se-pal .g { font-size: 10px; text-transform: uppercase; opacity: .55; margin-top: 6px; }
.nf-se-pal .e { border: 1px solid rgba(128,128,128,.35); border-radius: 4px; padding: 3px 8px; cursor: grab;
  background: rgb(var(--v-theme-surface)); user-select: none; }
.nf-se-pal .e:hover { border-color: rgb(var(--v-theme-primary)); }
.nf-se-pal .e.off { opacity: .4; cursor: not-allowed; }
.nf-se-canvas { flex: 1 1 auto; min-width: 0; position: relative; }
.nf-sn { min-width: 140px; border: 1.5px solid rgba(128,128,128,.5); border-radius: 6px; background: #fff;
  font-size: 11px; text-align: left; box-shadow: 0 1px 2px rgba(0,0,0,.08); }
.nf-sn.sel { box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), .55); }
.nf-sn.err { border-color: rgb(var(--v-theme-error)); }
.nf-sn .h { height: 24px; padding: 0 8px; font-weight: 600; display: flex; gap: 6px; align-items: center;
  white-space: nowrap; border-bottom: 1px solid rgba(128,128,128,.2); border-radius: 5px 5px 0 0; }
.nf-sn .h .id { font-weight: 400; opacity: .55; }
.nf-sn .h .ms { margin-left: auto; font-weight: 400; opacity: .6; font-family: monospace; }
.nf-sn .b { padding: 3px 8px; font-family: monospace; opacity: .8; white-space: nowrap; }
.nf-sn .m { padding: 2px 8px 4px; color: rgb(var(--v-theme-error)); max-width: 220px; white-space: normal; }
.nf-sn.source .h { background: #e6f4ea; } .nf-sn.filter .h { background: #e8f0fe; }
.nf-sn.display .h { background: #f3e8fd; } .nf-sn.view .h { background: #fef3e2; }
.nf-sn .p { height: 22px; padding: 0 10px; display: flex; justify-content: space-between; align-items: center;
  gap: 12px; font-size: 10px; opacity: .7; border-bottom: 1px dashed rgba(128,128,128,.25); }
.nf-sn .vue-flow__handle { opacity: 1; width: 12px; height: 12px; top: 35px; border: 2px solid #fff; }
.nf-sn .vue-flow__handle.many { height: 18px; border-radius: 3px; }
.nf-sn .vue-flow__handle.data { background: #1a73e8; } .nf-sn .vue-flow__handle.display { background: #9334e6; }
.nf-se-canvas:focus { outline: none; }
.nf-se-canvas .vue-flow__edge.selected .vue-flow__edge-path { stroke: rgb(var(--v-theme-primary)); stroke-width: 3; }
.nf-se-menu-off { position: absolute; inset: 0; z-index: 10; }
.nf-se-menu { position: absolute; z-index: 11; min-width: 150px; padding: 4px 0; font-size: 12px; border-radius: 6px;
  background: rgb(var(--v-theme-surface)); border: 1px solid rgba(128,128,128,.35); box-shadow: 0 6px 18px rgba(0,0,0,.16); }
.nf-se-menu .i { padding: 4px 12px; display: flex; justify-content: space-between; gap: 16px; cursor: pointer; }
.nf-se-menu .i:hover { background: rgba(128,128,128,.12); }
.nf-se-menu .i.del { color: rgb(var(--v-theme-error)); }
.nf-se-menu kbd { font-family: monospace; font-size: 10px; opacity: .6; }
.nf-se-form { flex: 0 0 auto; max-height: 45%; overflow: auto; border-top: 1px solid rgba(128,128,128,.25);
  padding: 6px 12px; font-size: 13px; }
.nf-se-form .row { display: flex; align-items: center; gap: 10px; min-height: 34px; }
.nf-se-form .row > .k { flex: 0 0 96px; opacity: .75; }
.nf-se-form .row > .v { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 8px; }
.nf-se-form .err { color: rgb(var(--v-theme-error)); }
.nf-se-foot { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-top: 1px solid rgba(128,128,128,.2); }
.nf-se-foot .msg { flex: 1 1 auto; font-size: 12px; opacity: .8; }
.nf-se-foot .msg.err { color: rgb(var(--v-theme-error)); opacity: 1; }
"""


def _summary(node: dict[str, Any]) -> list[str]:
    """Two short lines of a node's settings, for the canvas."""
    props, kind = node.get("props", {}), node["type"]
    if kind == "grid":
        return ["solver cells", "all fields"]
    if kind == "View":
        return [f"camera {props.get('camera', 'reset')}", f"zoom {props.get('zoom', 1.0):.3g}"]
    if kind == "Display":
        field = props.get("field", "@field")
        return [f"colour {field}" if field else "solid", str(props.get("colormap", "viridis"))]
    defaults = _scene_form.defaults(kind)
    lines = [f"{k} {_scene_form._as_text(v)}" for k, v in props.items() if v != defaults.get(k)]
    return lines[:2] or ["defaults"]


def _columns(graph: dict[str, Any], order: list[dict[str, Any]]) -> dict[str, int]:
    """Each node's column, left to right: the source, the filters by depth, the Displays, the View."""
    sources = inputs(graph)
    column: dict[str, int] = {}
    for node in order:
        if node["type"] not in ("grid", "Display", "View"):
            column[node["id"]] = 1 + max((column.get(s, 0) for s in sources.get(node["id"], [])), default=0)
    displays = 1 + max(column.values(), default=0)
    for node in order:
        column.setdefault(node["id"], {"grid": 0, "Display": displays, "View": displays + 1}.get(node["type"], 1))
    return column


def _auto_layout(graph: dict[str, Any]) -> dict[str, list[float]]:
    """A left-to-right DAG: columns by role (:func:`_columns`); in a column, nodes in the order of
    their first input's row, so a chain stays on one row."""
    try:
        order = ordered(graph)
    except ValueError:  # a cycle (refused at Apply): as listed
        order = graph["nodes"]
    sources, column = inputs(graph), _columns(graph, order)
    out: dict[str, list[float]] = {}
    for col in sorted(set(column.values())):
        members = [n["id"] for n in order if column[n["id"]] == col]
        members.sort(key=lambda m: min((out[s][1] for s in sources.get(m, []) if s in out), default=0.0))
        row = 0.0
        for member in members:
            wanted = min((out[s][1] for s in sources.get(member, []) if s in out), default=row)
            row = max(row, wanted)
            out[member] = [float(col * _DX), row]
            row += _DY
    return out


def _free_id(graph: dict[str, Any], kind: str) -> str:
    stem = kind.lower() if kind != "StreamTracer" else "stream"
    taken, n = {node["id"] for node in graph["nodes"]}, 1
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}" if kind != "grid" else ("grid" if "grid" not in taken else f"grid{n}")


class _SceneEditorPane(BasePane):
    """The scene editor tab; it edits the scenes of the field pane's :class:`_Scenes`."""

    name = "scene_editor"
    toggle = PaneToggle("se_on", "Scene editor", "mdi-graph-outline",
                        "the current scene as a node graph: add, link, set; Apply shows it", short="Editor")
    placement = "main"
    css = _EDITOR_CSS

    def __init__(self, ctx: PaneContext, scenes: _Scenes) -> None:
        super().__init__(ctx)
        self.scenes = scenes
        self.editor: Any = None
        #: What the canvas holds: (scene, graph), so an edit of its own is not pushed back.
        self._pushed: Optional[tuple[str, Any]] = None
        self._pushing = False
        self._fitted: Optional[tuple[str, list[str]]] = None
        self._fields: list[dict[str, Any]] = []
        scenes.listeners.append(self.refresh)
        for name, action in (("nf_se_prop", self.prop), ("nf_se_add", self.add), ("nf_se_drop", self.drop), ("nf_se_select", self.select),
                             ("nf_se_apply", self.apply), ("nf_se_revert", self.revert),
                             ("nf_se_remove_node", self.remove_node), ("nf_se_remove_link", self.remove_link),
                             ("nf_se_delete", self.delete)):
            ctx.server.trigger(name)(action)

    # -- pane ---------------------------------------------------------------------------

    def reset(self) -> None:
        s = self.ctx.state
        s.se_on = bool(s.se_on)
        s.se_sel = None
        s.se_link = None
        s.se_menu = None
        s.se_fields = []
        s.se_form = {}
        s.se_errors = {}
        s.se_ms = {}
        s.se_changes = 0
        s.se_msg = ""
        s.se_msg_err = False
        s.se_present = []
        s.se_node = None
        self._pushed = None

    def build(self) -> None:
        self.ctx.state.client_only("se_menu")  # an open menu belongs to its browser
        _editor_view(self)

    def layout_changed(self) -> None:
        if self.ctx.state.se_on:
            self.refresh()

    def handlers(self) -> Mapping[str, Callable[[dict[str, Any]], None]]:
        return {"frame": self._on_frame}

    # -- the canvas ---------------------------------------------------------------------

    def refresh(self) -> None:
        """Show the current scene's draft (or graph) on the canvas, unless it shows already."""
        s = self.ctx.state
        entry = self.scenes.entry()
        graph = entry.edited
        s.se_changes = self.scenes.changes()
        s.se_present = sorted({n["type"] for n in graph.get("nodes", [])} & {"grid", "View"})
        key = (self.scenes.current, copy.deepcopy(graph))
        if self.editor is None or key == self._pushed:
            return
        if s.se_sel and s.se_sel not in {n["id"] for n in graph.get("nodes", [])}:
            s.se_sel = None
        if s.se_link and s.se_link not in {_link_id(*e) for e in graph.get("edges", [])}:
            s.se_link = None
        self._push(graph)
        self._pushed = key
        self._form()

    def _push(self, graph: dict[str, Any]) -> None:
        layout = graph.get("layout") or {}
        auto = _auto_layout(graph) if any(n["id"] not in layout for n in graph["nodes"]) else {}
        nodes = []
        for node in graph["nodes"]:
            model = _PORTS[node["type"]]
            x, y = layout.get(node["id"]) or auto.get(node["id"], [0, 0])
            nodes.append({"id": node["id"], "type": "scene", "position": {"x": x, "y": y},
                          "data": {"kind": node["type"], "role": _ROLE.get(node["type"], "filter"),
                                   "takes": model.takes, "gives": model.gives, "many": model.many,
                                   "takes_name": model.takes_name, "gives_name": model.gives_name,
                                   "removable": node["type"] not in _FIXED, "lines": _summary(node)}})
        link = self.ctx.state.se_link
        edges = [{"id": _link_id(a, b), "source": a, "target": b, "sourceHandle": _PORTS[_type(graph, a)].gives,
                  "targetHandle": _PORTS[_type(graph, b)].takes, "type": "default", "selected": _link_id(a, b) == link}
                 for a, b in graph["edges"]]
        self._pushing = True
        try:
            self.editor.graph = {"nodes": nodes, "edges": edges}
        finally:
            self._pushing = False
        fit = (self.scenes.current, sorted(n["id"] for n in nodes))
        if fit != self._fitted:  # another scene, or nodes added or removed: all in view (≥ min zoom)
            self._fitted = fit
            self.editor.fit_view()

    def on_graph_change(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        """The canvas changed (a link, a removal, a move): the draft follows."""
        if self._pushing:
            return
        old = self.scenes.entry().edited
        by_id = {n["id"]: n for n in old["nodes"]}
        kept = [copy.deepcopy(by_id[n["id"]]) for n in nodes if n["id"] in by_id]
        ids = {n["id"] for n in kept}
        links = _one_input([(e["source"], e["target"]) for e in edges if e["source"] in ids and e["target"] in ids],
                           {n["id"]: n["type"] for n in kept})
        graph = {**old, "nodes": kept, "edges": [list(e) for e in links],
                 "layout": {n["id"]: [n["position"]["x"], n["position"]["y"]] for n in nodes if n["id"] in ids}}
        self._edit(graph, repush=len(links) != len(edges))

    def _edit(self, graph: dict[str, Any], repush: bool = False) -> None:
        self.ctx.state.se_msg = ""
        if repush:
            self._pushed = None
        else:
            self._pushed = (self._target_scene(), copy.deepcopy(graph))
        self.scenes.edit(graph)
        if self._pushed is not None and self._pushed[0] != self.scenes.current:  # Field was copied
            self._pushed = (self.scenes.current, self._pushed[1])
        self.refresh()

    def _draft(self) -> dict[str, Any]:
        """A copy of the shown graph with every node placed: as stored, else where the canvas has it."""
        graph = copy.deepcopy(self.scenes.entry().edited)
        shown = {n["id"]: [n["position"]["x"], n["position"]["y"]] for n in self.editor.graph["nodes"]} \
            if self.editor is not None else {}
        placed = {**_auto_layout(graph), **shown, **(graph.get("layout") or {})}
        graph["layout"] = {n["id"]: placed[n["id"]] for n in graph["nodes"]}
        return graph

    def _target_scene(self) -> str:
        return self.scenes.current if not self.scenes.entry().builtin else "field-copy"

    # -- palette, selection, form --------------------------------------------------------

    def add(self, kind: str) -> None:
        """Add a ``kind`` node after the selected one (linked if the ports fit)."""
        s = self.ctx.state
        graph = self._draft()
        layout = graph["layout"]
        after = s.se_sel if s.se_sel in layout else None
        x, y = (layout[after][0] + _DX, layout[after][1]) if after else \
            (max((p[0] for p in layout.values()), default=-_DX) + _DX, 0)
        while any(abs(p[0] - x) < _DX / 2 and abs(p[1] - y) < _DY / 2 for p in layout.values()):
            y += _DY  # not on top of another node
        node_id = self._add_node(graph, kind, [x, y])
        if after and _PORTS[_type(graph, after)].gives == _PORTS[kind].takes and _PORTS[kind].takes is not None:
            graph["edges"].append([after, node_id])
            view = next((n["id"] for n in graph["nodes"] if n["type"] == "View"), None)
            if kind == "Display" and view is not None:  # shown at once: the scene's one View
                graph["edges"].append([node_id, view])
        s.se_sel = node_id
        self._edit(graph, repush=True)

    def drop(self, kind: str, x: float, y: float) -> None:
        """Place a ``kind`` node at the canvas position, unlinked."""
        graph = self._draft()
        self.ctx.state.se_sel = self._add_node(graph, kind, [float(x), float(y)])
        self._edit(graph, repush=True)

    def _add_node(self, graph: dict[str, Any], kind: str, at: list[float]) -> str:
        node_id = _free_id(graph, kind)
        graph["nodes"].append({"id": node_id, "type": kind, "props": _scene_form.defaults(kind)})
        graph.setdefault("layout", {})[node_id] = at
        return node_id

    def remove_node(self, node_id: str) -> None:
        """Remove a node and its links from the draft (not ``grid`` or the View)."""
        graph = self._draft()
        if _type(graph, node_id) in _FIXED:
            return
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != node_id]
        graph["edges"] = [e for e in graph["edges"] if node_id not in e]
        graph.get("layout", {}).pop(node_id, None)
        if self.ctx.state.se_sel == node_id:
            self.ctx.state.se_sel = None
        self._edit(graph, repush=True)

    def remove_link(self, link_id: str) -> None:
        """Remove a link (``source->target``) from the draft."""
        graph = self._draft()
        graph["edges"] = [e for e in graph["edges"] if _link_id(*e) != link_id]
        if self.ctx.state.se_link == link_id:
            self.ctx.state.se_link = None
        self._edit(graph, repush=True)

    def delete(self) -> None:
        """Del / Backspace on the canvas: remove the selected link, else the selected node."""
        s = self.ctx.state
        if s.se_link:
            self.remove_link(s.se_link)
        elif s.se_sel:
            self.remove_node(s.se_sel)

    def select(self, node_id: Optional[str]) -> None:
        self.ctx.state.se_sel = node_id
        self.ctx.state.se_link = None
        self._form()

    def select_link(self, link_id: str) -> None:
        """A click on a link selects it (and no node)."""
        self.select(None)
        self.ctx.state.se_link = link_id

    def _form(self) -> None:
        """The selected node's form and its values."""
        s = self.ctx.state
        node = next((n for n in self.scenes.entry().edited["nodes"] if n["id"] == s.se_sel), None)
        self._fields = _scene_form.form(node["type"]) if node else []
        s.se_fields = self._fields
        s.se_node = {"id": node["id"], "type": node["type"],
                     "inputs": inputs(self.scenes.entry().edited)[node["id"]]} if node else None
        s.se_form = _scene_form.shown(self._fields, node.get("props", {})) if node else {}

    def prop(self, name: str, value: Any) -> None:
        """A value typed or picked in the form: the selected node's property in the draft."""
        s = self.ctx.state
        field = next((f for f in self._fields if f["name"] == name), None)
        graph = self._draft()
        node = next((n for n in graph["nodes"] if n["id"] == s.se_sel), None)
        if node is None or field is None:
            return
        props = dict(node.get("props", {}))
        parsed = _scene_form.parsed(field, value)
        if parsed is None and field["required"]:
            props.pop(name, None)
        else:
            props[name] = parsed
        if props == node.get("props", {}):
            return
        node["props"] = props
        self._edit(graph, repush=True)
        s.se_form = _scene_form.shown(self._fields, props)

    # -- apply ----------------------------------------------------------------------------

    def apply(self) -> None:
        """Show the draft: checked first; a refused draft names its node."""
        s = self.ctx.state
        refused = self.scenes.apply()
        if refused is not None:
            s.se_errors = {refused.node: str(refused)} if refused.node else {}
            s.se_msg, s.se_msg_err = f"not applied: {refused}", True
            self._pushed = None
            self.refresh()
            return
        s.se_errors, s.se_msg, s.se_msg_err = {}, "applied", False
        self.refresh()

    def revert(self) -> None:
        self.scenes.revert()
        s = self.ctx.state
        s.se_errors, s.se_msg, s.se_msg_err = {}, "reverted to the applied scene", False
        self.refresh()

    # -- what the solver says -------------------------------------------------------------

    def _on_frame(self, msg: dict[str, Any]) -> None:
        """A build error on its node; each node's last execute time (ms)."""
        s = self.ctx.state
        if msg.get("scene", "Field") != self.scenes.current:
            return
        if msg.get("error"):
            s.se_errors = {msg["node"]: msg["error"]} if msg.get("node") else {}
            if not msg.get("node"):
                s.se_msg, s.se_msg_err = msg["error"], True
            return
        s.se_errors = {}
        ms, proxies = msg.get("pipeline_ms") or {}, (msg.get("report") or {}).get("proxies") or {}
        s.se_ms = {node: f"{sum(ms[p] for p in ids if p in ms):.1f} ms"
                   for node, ids in proxies.items() if any(p in ms for p in ids)}


def _link_id(source: str, target: str) -> str:
    return f"{source}->{target}"


def _type(graph: dict[str, Any], node_id: str) -> str:
    return next(n["type"] for n in graph["nodes"] if n["id"] == node_id)


def _one_input(links: list[tuple[str, str]], kinds: dict[str, str]) -> list[tuple[str, str]]:
    """The links, a node that takes one input keeping its newest (the last); no duplicates."""
    out: list[tuple[str, str]] = []
    for link in links:
        if link in out:
            continue
        if not _PORTS[kinds[link[1]]].many:
            out = [old for old in out if old[1] != link[1]]
        out.append(link)
    return out


# -- widgets -----------------------------------------------------------------------------------


def _palette() -> None:
    with html.Div(classes="nf-se-pal"):
        for group, kinds in _PALETTE:
            html.Div(group, classes="g")
            for kind in kinds:
                once = kind in ("grid", "View")
                off = f"se_present.includes('{kind}')" if once else "false"
                html.Div(kind, classes=(f"['e', {{ off: {off} }}]",), draggable=(f"!({off})",),
                         title=f"click: add after the selected node · drag: place {kind} on the canvas",
                         click=f"!({off}) && trigger('nf_se_add', ['{kind}'])",
                         raw_attrs=[f'@dragstart="$event.dataTransfer.setData(\'nf/node\', \'{kind}\')"'])


_DROP = ("const k = $event.dataTransfer.getData('nf/node'); if (k) {"
         " const p = window.trame.refs.nf_se_editor.screenToFlowCoordinate({x: $event.clientX, y: $event.clientY});"
         " trigger('nf_se_drop', [k, p.x, p.y]); }")


def _open_menu(item: str, kind: str, when: str = "true") -> str:
    """Right-click on a node or a link (``$event.node`` / ``$event.edge``): its menu at the pointer, ``when`` true.

    The position is relative to the canvas: a dock pane is transformed, so ``position: fixed`` would be off."""
    at = "$event.event.target.closest('.nf-se-canvas').getBoundingClientRect()"
    return (f"$event.event.preventDefault(); se_menu = {when} ? {{kind: '{kind}', id: $event.{item}.id,"
            f" x: $event.event.clientX - {at}.left, y: $event.event.clientY - {at}.top}} : null")


#: Keys on the canvas (the form's inputs are outside it): Del / Backspace remove through the server.
_KEYS = ("if (['Delete', 'Backspace'].includes($event.key)) { $event.preventDefault(); trigger('nf_se_delete'); }"
         " else if ($event.key === 'Escape') { se_menu = null; }")


def _canvas(pane: _SceneEditorPane) -> None:
    with html.Div(classes="nf-se-canvas", tabindex="0",
                  raw_attrs=['@dragover.prevent=""', f'@drop.prevent="{_DROP}"', '@contextmenu.prevent=""',
                             f'@keydown="{_KEYS}"']):
        with NodeEditor(ref="nf_se_editor", style="width: 100%; height: 100%;", is_valid_connection=(_VALID_LINK,),
                        node_click=(pane.select, "[$event.node.id]"), pane_click=(pane.select, "[null]"),
                        edge_click=(pane.select_link, "[$event.edge.id]"),
                        node_context_menu=_open_menu("node", "node", when="$event.node.data.removable"),  # not grid, View
                        edge_context_menu=_open_menu("edge", "link")) as editor:
            pane.editor = editor
            editor._attributes["min_zoom"] = f':min-zoom="{_MIN_ZOOM}"'
            # Vue Flow's own Del / Backspace would remove in the browser only: the server removes.
            editor._attributes["delete_key_code"] = ':delete-key-code="null"'
            editor.graph_change = pane.on_graph_change
            Background(gap=16, size=1, pattern_color="#c3c8d2")
            Controls(show_interactive=False)
            with CustomNode("scene"):
                with html.Div(classes=("['nf-sn', props.data.role, { sel: props.id === se_sel,"
                                       " err: !!se_errors[props.id] }]",)):
                    Handle(position="left", type="target", id=("props.data.takes",), v_if="props.data.takes",
                           classes=("[props.data.takes, { many: props.data.many }]",))
                    Handle(position="right", type="source", id=("props.data.gives",), v_if="props.data.gives",
                           classes=("props.data.gives",))
                    with html.Div(classes="h"):
                        html.Span("{{ props.data.kind }}")
                        html.Span("{{ props.id }}", classes="id")
                        html.Span("{{ se_ms[props.id] }}", classes="ms", v_if="se_ms[props.id]")
                    with html.Div(classes="p"):
                        html.Span("{{ props.data.takes_name }}")
                        html.Span("{{ props.data.gives_name }}")
                    html.Div("{{ l }}", v_for="l in props.data.lines", classes="b")
                    html.Div("{{ se_errors[props.id] }}", classes="m", v_if="se_errors[props.id]")
        _menu()


def _menu() -> None:
    """The open right-click menu (``se_menu``); a click anywhere else closes it."""
    html.Div(v_if="se_menu", classes="nf-se-menu-off", click="se_menu = null",
             raw_attrs=['@contextmenu.prevent="se_menu = null"'])
    with html.Div(v_if="se_menu", classes="nf-se-menu", style=("{ left: se_menu.x + 'px', top: se_menu.y + 'px' }",)):
        with html.Div(classes="i del", click="trigger(se_menu.kind === 'node' ? 'nf_se_remove_node'"
                                             " : 'nf_se_remove_link', [se_menu.id]); se_menu = null"):
            html.Span("{{ se_menu.kind === 'node' ? 'Remove node' : 'Remove link' }}")
            html.Kbd("Del")


#: Send a form value (the form shows its own copy, ``se_form``; the server sets the draft).
_PICKED = "trigger('nf_se_prop', [f.name, $event])"
_TYPED = ["@change=\"trigger('nf_se_prop', [f.name, $event.target.value])\"", '@keydown.stop=""']


def _value(kind: str) -> None:
    """The input of one form field ``f`` (a template over ``se_fields``): picks are sent at once,
    typed text when it is committed (Enter, or leaving the field), a slider when let go."""
    model = "se_form[f.name]"
    if kind == "choice":
        with v3.VBtnToggle(v_if="f.buttons", v_model=(model,), mandatory=True, density="compact",
                           variant="outlined", divided=True, update_modelValue=_PICKED):
            v3.VBtn("{{ c }}", v_for="c in f.items", key="c", value=("c",), size="small")
        v3.VSelect(v_else=True, v_model=(model,), items=("f.items",), density="compact", hide_details=True,
                   variant="outlined", style="max-width: 220px;", update_modelValue=_PICKED)
    elif kind == "check":
        v3.VCheckbox(v_model=(model,), density="compact", hide_details=True, update_modelValue=_PICKED)
    elif kind == "slider":
        v3.VSlider(v_model_number=(model,), min=("f.min",), max=("f.max",), step=("f.step",), density="compact",
                   hide_details=True, end=_PICKED)
        html.Span("{{ se_form[f.name] }}", classes="nf-mono", style="min-width: 48px;")
    elif kind == "number":
        v3.VTextField(v_model=(model,), type="number", density="compact", hide_details=True,
                      variant="outlined", style="max-width: 160px;", raw_attrs=_TYPED)
    elif kind == "field":
        v3.VCombobox(v_model=(model,), density="compact", hide_details=True, variant="outlined",
                     items=("['@field', ...fv_fields.map(x => x.value), ...(f.solid ? ['(solid)'] : [])]",),
                     style="max-width: 220px;", update_modelValue=_PICKED, raw_attrs=['@keydown.stop=""'])
    else:
        v3.VTextField(v_model=(model,), density="compact", hide_details=True, variant="outlined",
                      placeholder=("f.kind === 'text' ? 'e.g. 1, 2, 3' : ''",), style="max-width: 320px;",
                      raw_attrs=_TYPED)


def _form() -> None:
    with html.Div(classes="nf-se-form", v_if="se_node"):
        with html.Div(classes="d-flex align-center ga-2 mb-1"):
            html.B("{{ se_node.type }}")
            html.Span("· {{ se_node.id }}", style="opacity: .6")
            html.Span("input {{ se_node.inputs.join(', ') || '—' }}", classes="ml-4", style="opacity: .7")
        html.Div("{{ se_errors[se_node.id] }}", classes="err mb-1", v_if="se_errors[se_node.id]")
        with html.Div(classes="row", v_for="f in se_fields", key="f.name"):
            html.Span("{{ f.label }}{{ f.required ? ' *' : '' }}", classes="k")
            with html.Div(classes="v"):
                _value_switch()
        html.Div("form from {{ se_node.type }}Props.model_json_schema() (pydantic)", style="font-size: 11px; opacity: .5")
    html.Div("Select a node to set it; click a palette entry to add one after it, or drag one onto the canvas.",
             classes="nf-se-form", style="opacity: .6", v_if="!se_node")


def _value_switch() -> None:
    """One input per field kind (``v-if`` chain)."""
    for i, kind in enumerate(("choice", "check", "slider", "number", "field", "string", "text")):
        cond: dict[str, Any] = {"v_if": f"f.kind === '{kind}'"} if i == 0 else \
            ({"v_else_if": f"f.kind === '{kind}'"} if kind != "text" else {"v_else": True})
        with html.Template(**cond):
            _value(kind)


def _editor_view(pane: _SceneEditorPane) -> None:
    """The tab: palette and canvas, the form, Apply / Revert."""
    with html.Div(classes="nf-se", v_show="se_on"):
        with html.Div(classes="nf-se-top"):
            _palette()
            _canvas(pane)
        _form()
        with html.Div(classes="nf-se-foot"):
            html.Span("{{ se_msg || (se_changes ? se_changes + ' change' + (se_changes > 1 ? 's' : '') + ' since last apply'"
                      " : 'no changes since last apply') }}", classes=("['msg', { err: se_msg_err }]",))
            v3.VBtn("Revert", size="small", variant="outlined", disabled=("!se_changes",), click="trigger('nf_se_revert')")
            v3.VBtn("Apply", size="small", color="primary", variant="flat", disabled=("!se_changes",),
                    click="trigger('nf_se_apply')")
