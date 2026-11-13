"""The Scenes panel: the built-in ``Field`` scene and the case's ``system/scenes/*.json``.

Switch, new, duplicate, rename, delete (confirmed), save, export; ``●`` marks unsaved edits,
which stay in this process until saved. A scene's graph is what the solver shows; the scene
editor's edits are a draft of it until Apply (kept per scene; Revert drops it). ``Field`` is a
read-only template: editing it (the camera overlay, the editor) makes an unsaved copy,
``field-copy``. A file that is not a scene is listed with its error and cannot be shown.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3

from neofoam_ui.scenes import FIELD, SCENES_DIR, field_graph
from neofoam_ui.scenes.models import Scene, SceneError, parse, read_scenes, write_scene

__all__ = ()

#: What is not a letter or digit in a title becomes one ``-`` of the file's name.
_SLUG = re.compile(r"[^a-z0-9]+")
_FILTERS = ("Surface", "Slice", "Clip", "Contour", "Threshold", "Calculator", "Glyph", "StreamTracer")

_SCENES_CSS = r"""
.nf-scenes { display: flex; flex-direction: column; gap: 6px; padding: 4px 0; }
.nf-scene { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; cursor: pointer;
  border-left: 3px solid transparent; }
.nf-scene:hover { background: rgba(128,128,128,.08); }
.nf-scene.cur { background: rgba(var(--v-theme-primary), .1); border-left-color: rgb(var(--v-theme-primary)); }
.nf-scene.bad { cursor: default; opacity: .8; }
.nf-scene .n { font-size: 13px; font-weight: 600; }
.nf-scene .s { font-size: 11px; opacity: .7; }
.nf-scene .s.err { color: rgb(var(--v-theme-error)); opacity: 1; white-space: normal; }
.nf-scene .dot { color: rgb(var(--v-theme-warning)); margin-left: auto; font-size: 14px; }
.nf-scene-acts { display: flex; flex-wrap: wrap; gap: 4px; }
.nf-scene-path { font-family: monospace; font-size: 11px; opacity: .7; word-break: break-all; }
.nf-scene-hint { font-family: monospace; font-size: 11px; opacity: .6; }
"""


@dataclass
class _Entry:
    """One scene as the UI holds it: its graph (with layout; ``{}`` for a bad file), the one on
    disk (``None``: unsaved) and the editor's draft (``None``: none)."""

    graph: dict[str, Any]
    saved: Optional[dict[str, Any]]
    error: Optional[str] = None
    builtin: bool = False
    draft: Optional[dict[str, Any]] = None

    @property
    def unsaved(self) -> bool:
        return not self.builtin and self.error is None and self.graph != self.saved

    @property
    def edited(self) -> dict[str, Any]:
        """What the editor shows: the draft, else the graph."""
        return self.draft if self.draft is not None else self.graph


def _core(graph: dict[str, Any]) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    """A graph's nodes (by id) and edges, without layout and title: what Apply changes."""
    return ({n["id"]: n for n in graph.get("nodes", [])}, {tuple(e) for e in graph.get("edges", [])})


def _changes(old: dict[str, Any], new: dict[str, Any]) -> int:
    """Nodes added, removed or changed and edges added or removed from ``old`` to ``new``."""
    (old_nodes, old_edges), (new_nodes, new_edges) = _core(old), _core(new)
    nodes = sum(old_nodes.get(k) != new_nodes.get(k) for k in old_nodes.keys() | new_nodes.keys())
    return nodes + len(old_edges ^ new_edges)


def _dumped(scene: Scene) -> dict[str, Any]:
    return scene.model_dump(mode="json")


def _summary(entry: _Entry) -> str:
    """The list's second line: built-in, the error, unsaved, or the filters it uses."""
    if entry.builtin:
        return "built-in"
    if entry.error is not None:
        return entry.error
    if entry.draft is not None:
        return "edits not applied"
    if entry.unsaved:
        return "edited, not saved" if entry.saved is not None else "new, not saved"
    kinds = [n["type"] for n in entry.graph["nodes"] if n["type"] in _FILTERS]
    return " · ".join(dict.fromkeys(kinds)) or "grid only"


class _Scenes:
    """The scenes of a case and the current one (state ``sc_*``); ``changed`` is called when
    what the pane shows changes (another scene, or the current one edited)."""

    def __init__(self, state: Any, case: str, changed: Callable[[], None]) -> None:
        self._state = state
        self._case = Path(case)
        self._changed = changed
        self.entries: dict[str, _Entry] = {}
        #: Called after every change of the list or of a scene (the scene editor follows).
        self.listeners: list[Callable[[], None]] = []
        #: The scene shown last (a click on a bad file goes back to it).
        self._shown = FIELD
        state.change("sc_current")(lambda sc_current, **_: self.switch(sc_current))

    # -- what the pane needs ---------------------------------------------------------------

    @property
    def current(self) -> str:
        return str(self._state.sc_current or FIELD)

    def options(self) -> dict[str, str]:
        """The view options of the current scene: its name and, but for ``Field``, its graph."""
        name = self.current
        entry = self.entries.get(name)
        if entry is None or entry.builtin:
            return {"scene": FIELD}
        graph = {k: v for k, v in entry.graph.items() if k != "layout"}
        return {"scene": name, "graph": json.dumps(graph, separators=(",", ":"))}

    def view_props(self) -> dict[str, Any]:
        """The current scene's View node properties (camera, zoom)."""
        entry = self.entries.get(self.current) or self.entries[FIELD]
        return dict(next(n["props"] for n in entry.graph["nodes"] if n["type"] == "View"))

    def edit_view(self, camera: str, zoom: float) -> None:
        """Set the current scene's camera and zoom (unsaved); ``Field`` is copied first."""
        if self.current == FIELD:
            self.duplicate()
        entry = self.entries[self.current]
        for graph in (entry.graph, entry.draft or {"nodes": []}):  # a draft keeps the camera too
            for view in (n for n in graph["nodes"] if n["type"] == "View"):
                view.setdefault("props", {}).update(camera=camera, zoom=float(zoom))
        self._publish()
        self._changed()

    # -- the scene editor ------------------------------------------------------------------

    def entry(self, name: Optional[str] = None) -> _Entry:
        return self.entries[name or self.current]

    def edit(self, graph: dict[str, Any]) -> None:
        """The editor's graph of the current scene: a draft (``Field`` is copied first). Node
        positions alone need no Apply: they go to the scene's graph as well."""
        entry = self.entries[self.current]
        if entry.builtin:
            if _changes(entry.graph, graph) == 0:
                return  # Field's nodes moved: nothing to keep
            self.duplicate()
            entry = self.entries[self.current]
            graph = {**graph, "title": entry.graph.get("title")}
        entry.graph["layout"] = copy.deepcopy(graph.get("layout", {}))
        entry.draft = graph if _changes(entry.graph, graph) else None
        self._publish()

    def changes(self) -> int:
        """How many changes the current scene's draft makes."""
        entry = self.entries[self.current]
        return _changes(entry.graph, entry.draft) if entry.draft is not None else 0

    def apply(self) -> Optional[SceneError]:
        """Show the current draft: checked first (the error, naming the node, refuses it)."""
        entry = self.entries[self.current]
        if entry.draft is None:
            return None
        try:
            scene = parse(entry.draft)
        except SceneError as exc:
            return exc
        entry.graph, entry.draft = _dumped(scene), None
        self._publish()
        self._changed()
        return None

    def revert(self) -> None:
        """Drop the current scene's draft."""
        self.entries[self.current].draft = None
        self._publish()

    # -- the panel's actions -------------------------------------------------------------

    def load(self) -> None:
        """Read the case's scene files again; unsaved edits stay."""
        kept = {k: e for k, e in self.entries.items() if e.unsaved or e.draft is not None}
        self.entries = {FIELD: _Entry(field_graph(), None, builtin=True)}
        for file in read_scenes(self._case):
            if file.name == FIELD:  # listed by its file name: the scene's is taken
                self.entries[file.path.name] = _Entry({}, None, f"{FIELD} is the built-in scene's name")
            elif file.scene is None:
                self.entries[file.name] = _Entry({}, None, file.error)
            else:
                graph = _dumped(file.scene)
                self.entries[file.name] = _Entry(graph, copy.deepcopy(graph))
        for name, entry in kept.items():
            saved = self.entries[name].saved if name in self.entries else None
            self.entries[name] = _Entry(entry.graph, saved, draft=entry.draft)
        if self.current not in self.entries or self.entries[self.current].error is not None:
            self._state.sc_current = FIELD
        self._publish()

    def switch(self, name: Optional[str]) -> None:
        entry = self.entries.get(name or "")
        if entry is None or entry.error is not None:  # a bad file is listed, never shown
            self._state.sc_current = self._shown
            return
        self._shown = str(name)
        self._state.sc_current = name
        self._publish()
        self._changed()

    def new(self) -> None:
        """A new unsaved scene, ``Scene``: a copy of ``Field``'s graph."""
        self._add("Scene", field_graph())

    def duplicate(self) -> None:
        """An unsaved copy of the current scene, ``<title> copy`` (``Field``: ``field-copy.json``)."""
        self._add(f"{self.title(self.current)} copy", self.entries[self.current].graph)

    def _add(self, title: str, graph: dict[str, Any]) -> None:
        name = self._free(_slug(title))
        self.entries[name] = _Entry(_dumped(parse({**graph, "title": title})), None)
        self.switch(name)

    def title(self, name: str) -> str:
        """What the panel calls a scene: its title, else its name."""
        entry = self.entries.get(name)
        return str((entry.graph.get("title") if entry is not None else None) or name)

    def save(self) -> None:
        """Write the current scene to ``system/scenes/<name>.json``."""
        name, entry = self.current, self.entries[self.current]
        if entry.builtin:
            return
        write_scene(self._case, name, parse(entry.graph))
        entry.saved = copy.deepcopy(entry.graph)
        self._publish()

    def rename(self, title: str) -> None:
        """Give the current scene another title, its file the title's slug (a saved file is
        moved and gets the title, nothing else); a title without a free slug is refused."""
        title, old = str(title).strip(), self.current
        new = _slug(title)
        why = self._bad_title(title, new, old)
        if why or self.entries[old].builtin:
            self._state.sc_msg = why or f"{FIELD} cannot be renamed"
            return
        entry = self.entries.pop(old)
        entry.graph["title"] = title
        if entry.saved is not None:
            _retitle(self._path(old), self._path(new), title)
            entry.saved["title"] = title
        self.entries[new] = entry
        self._state.sc_rename = False
        self.switch(new)

    def delete(self) -> None:
        """Delete the current scene and its file (the panel asked first); ``Field`` shows."""
        name = self.current
        entry = self.entries[name]
        if entry.builtin:
            return
        path = self._path(name)
        if path.exists():
            path.unlink()
        del self.entries[name]
        self._state.sc_confirm = False
        self.switch(FIELD)

    def export_path(self) -> Path:
        """Where the current scene's Catalyst script goes: next to its file (``Field``: ``field.py``)."""
        name = self.current
        return self._path(_slug(name) if self.entries[name].builtin else name).with_suffix(".py")

    def ask_rename(self) -> None:
        s = self._state
        s.sc_new_name, s.sc_msg, s.sc_rename = self.title(self.current), "", True

    # -- helpers ---------------------------------------------------------------------------

    def _path(self, name: str) -> Path:
        return self._case / SCENES_DIR / f"{name}.json"

    def _free(self, base: str) -> str:
        name, n = base, 2
        while name in self.entries or self._path(name).exists():
            name, n = f"{base}-{n}", n + 1
        return name

    def _bad_title(self, title: str, name: str, old: str) -> str:
        if not name:
            return "a title needs a letter or a digit"
        if title == FIELD or name != old and (name in self.entries or self._path(name).exists()):
            return f"a scene {title!r} exists ({SCENES_DIR}/{name}.json)"
        return ""

    def _publish(self) -> None:
        """The list the panel and the bar show."""
        self._state.sc_list = [
            {"name": name, "title": self.title(name), "sub": _summary(e), "unsaved": e.unsaved,
             "drafted": e.draft is not None, "error": e.error is not None,
             "builtin": e.builtin, "path": "built-in, read-only" if e.builtin else f"{SCENES_DIR}/{name}.json"}
            for name, e in self.entries.items()]
        for listener in self.listeners:
            listener()


def _slug(title: str) -> str:
    """A title as a file name: ``p slice + U contour`` → ``p-slice-u-contour``."""
    return _SLUG.sub("-", title.lower()).strip("-")


def _retitle(old: Path, new: Path, title: str) -> None:
    """Move a scene file and set its title (the rest of the file as it was)."""
    data = json.loads(old.read_text())
    data["title"] = title
    new.write_text(json.dumps(data, indent=2) + "\n")
    if new != old:
        old.unlink()


def _scene_dialogs() -> None:
    """Rename (a name) and delete (are you sure?)."""
    with v3.VDialog(v_model=("sc_rename",), max_width=360):
        with v3.VCard(title="Rename scene"):
            with v3.VCardText():
                v3.VTextField(v_model=("sc_new_name",), label="title", density="compact", autofocus=True,
                              error_messages=("sc_msg",), hide_details="auto",
                              raw_attrs=["@keyup.enter=\"trigger('nf_sc_rename', [sc_new_name])\""])
            with v3.VCardActions():
                v3.VSpacer()
                v3.VBtn("Cancel", click="sc_rename = false")
                v3.VBtn("Rename", color="primary", click="trigger('nf_sc_rename', [sc_new_name])")
    with v3.VDialog(v_model=("sc_confirm",), max_width=360):
        with v3.VCard(title="Delete scene"):
            v3.VCardText("Delete the scene and system/scenes/{{ sc_current }}.json? This cannot be undone.")
            with v3.VCardActions():
                v3.VSpacer()
                v3.VBtn("Cancel", click="sc_confirm = false")
                v3.VBtn("Delete", color="error", click="trigger('nf_sc_delete')")


def _scenes_panel() -> None:
    """The list (current highlighted, ● unsaved, bad files with their error), the actions, the file."""
    with html.Div(classes="nf-scenes"):
        with html.Div(v_for="s in sc_list", key="s.name", title=("s.error ? s.sub : s.path",),
                      classes=("['nf-scene', { cur: s.name === sc_current, bad: s.error }]",),
                      click="!s.error && (sc_current = s.name)"):
            v3.VIcon(icon=("s.error ? 'mdi-alert-circle-outline' : s.builtin ? 'mdi-cube-outline' : 'mdi-graph-outline'",),
                     size=18, color=("s.error ? 'error' : undefined",))
            with html.Div(style="min-width: 0;"):
                html.Div("{{ s.title }}", classes="n")
                html.Div("{{ s.sub }}", classes=("['s', { err: s.error }]",))
            html.Span("●", classes="dot", v_if="s.unsaved", title="not saved")
        with html.Div(classes="nf-scene-acts"):
            cur = "sc_list.find(s => s.name === sc_current) || {}"
            v3.VBtn("Save", size="small", variant="tonal", click="trigger('nf_sc_save')",
                    disabled=(f"!({cur}).unsaved",), title="write the scene to its file")
            v3.VBtn("Duplicate", size="small", variant="tonal", click="trigger('nf_sc_duplicate')",
                    title="an unsaved copy of this scene")
            v3.VBtn("Rename", size="small", variant="tonal", click="trigger('nf_sc_ask_rename')",
                    disabled=(f"({cur}).builtin",))
            v3.VBtn("Delete", size="small", variant="tonal", click="sc_confirm = true",
                    disabled=(f"({cur}).builtin",))
            v3.VBtn("New", size="small", variant="tonal", click="trigger('nf_sc_new')", prepend_icon="mdi-plus",
                    title="a new scene (the Field scene's graph, unsaved)")
            v3.VBtn("Export .py", size="small", variant="tonal", click="trigger('nf_sc_export')",
                    title="the scene shown, as a Catalyst script next to its file (runs without the UI)")
            v3.VBtn(icon="mdi-refresh", size="small", variant="text", click="trigger('nf_sc_load')",
                    title="read system/scenes/ again (unsaved edits stay)")
        html.Div(f"{{{{ ({cur}).path }}}}", classes="nf-scene-path")
        html.Div("{{ sc_msg }}", classes="nf-scene-path", v_if="sc_msg && !sc_rename")
        html.Div("Switching: the solver keeps every scene's ParaView objects; only the shown one executes.",
                 classes="nf-scene-hint")
    _scene_dialogs()


def _register(server: Any, scenes: _Scenes) -> None:
    """The panel's triggers."""
    for name, action in (("nf_sc_save", scenes.save), ("nf_sc_duplicate", scenes.duplicate),
                         ("nf_sc_new", scenes.new), ("nf_sc_delete", scenes.delete), ("nf_sc_load", scenes.load),
                         ("nf_sc_ask_rename", scenes.ask_rename), ("nf_sc_rename", scenes.rename)):
        server.trigger(name)(action)

