"""The panels of the page: the catalogue the layout places, and the widgets of the plain ones.

Two kinds of panel:

* **views** (``kind="main"``): wide; tabs of the dock (trame-dockview) in the main area,
  split and docked freely. The step view (the diagram), plots, the CFD view (Catalyst) and
  every extension pane, the console and the field table.
* **side panels** (``kind="side"``): narrow; stacked and collapsible in the left or the
  right sidebar, movable to the other one. Containment tree, call stack, breakpoints,
  inspector, variables, options, legend, scenes.

Each panel's widgets are a trame template of their own (:attr:`_Panel.template`): the dock,
the sidebars and the phone's stack show it with ``<trame-template>``, so it is built once.
A panel with a ``flag`` sets that state key while it shows (the server's pane toggle: the
step view's ``dag_on`` tells the worker whether anyone watches, the CFD view's ``fv_on``
whether frames are wanted).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, cast

from trame.widgets import html
from trame.widgets import vuetify3 as v3

if TYPE_CHECKING:
    from neofoam_ui._ui._app import StepperApp
    from neofoam_ui._ui.panes._field import _FieldPane

__all__ = ()


@dataclass(frozen=True)
class _Panel:
    """One panel of the layout."""

    id: str
    title: str
    #: Label under the phone picker's icon.
    short: str
    #: An ``mdi-*`` icon.
    icon: str
    kind: Literal["main", "side"]
    build: Callable[[], None]
    #: State key set true while the panel shows (``None``: nobody needs to know).
    flag: Optional[str] = None
    tip: str = ""
    #: A side panel's sidebar, and whether it is open (collapsed) in the default layout.
    side: Literal["left", "right"] = "right"
    open: bool = False
    collapsed: bool = False
    #: A count on its launcher (a JS expression, e.g. the breakpoints).
    badge: Optional[str] = None

    @property
    def template(self) -> str:
        return f"nf_p_{self.id}"

    def client(self) -> dict[str, Any]:
        """What the layout's script needs of it."""
        return {"id": self.id, "title": self.title, "kind": self.kind, "flag": self.flag,
                "tmpl": self.template, "side": self.side, "open": self.open, "collapsed": self.collapsed}


def _catalogue(app: StepperApp) -> list[_Panel]:
    """Every panel, in launcher order: the views (the built-in ones, the extension panes), then
    the side panels."""
    dag, field, plots = app.dag, cast("_FieldPane", app.pane("field")), app.pane("plots")

    def view(pane: Any, pid: str, icon: Optional[str] = None) -> _Panel:
        t = pane.toggle
        if t is None:  # an extension pane without a toggle: shown while open, nobody told
            return _Panel(pane.name, pane.name, pane.name, "mdi-puzzle-outline", "main", pane.build)
        return _Panel(pid, t.label, t.short or t.label, icon or t.icon, "main", pane.build, t.key, t.title)

    builtin = {dag.name, field.name, plots.name}
    side = dag.side_panels()
    return [
        view(dag, "step"),
        view(plots, "plots"),
        view(field, "cfd"),
        _Panel("console", "Console", "Console", "mdi-console", "main", _console,
               tip="the simulation console, newest first"),
        _Panel("fields", "Field table", "Fields", "mdi-table", "main", _fields_table,
               tip="min / max / mean of every field and who wrote it last; a click highlights its ops"),
        *(view(p, p.name) for p in app.panes if p.name not in builtin),
        _Panel("tree", "Containment", "Tree", "mdi-file-tree-outline", "side", side["tree"],
               tip="the init steps and the ops per region; a click selects", side="left", open=True),
        _Panel("breakpoints", "Breakpoints", "Breaks", "mdi-record-circle-outline", "side", side["breakpoints"],
               side="left", open=True, collapsed=True, badge="bps.length"),
        _Panel("scenes", "Scenes", "Scenes", "mdi-layers-outline", "side", field.build_scenes,
               tip="the in-situ scenes of the case: switch, new, duplicate, rename, delete, save", side="left",
               open=True),
        _Panel("inspector", "Inspector", "Inspect", "mdi-information-outline", "side", side["inspector"],
               tip="the selected node", open=True),
        _Panel("callstack", "Call stack", "Stack", "mdi-layers-outline", "side", side["callstack"],
               tip="where each region is: its op, the loops around it", open=True),
        _Panel("vars", "Variables (ctx)", "Vars", "mdi-code-braces", "side", _variables,
               tip="the solver context's scalars"),
        _Panel("options", "Options", "Options", "mdi-tune", "side", _options,
               tip="step delay, wait for UI, follow, timing, minimap"),
        _Panel("legend", "Legend", "Legend", "mdi-help-circle-outline", "side", side["legend"],
               tip="what the diagram's marks mean"),
    ]


def _side_defaults(panels: list[_Panel]) -> dict[str, Any]:
    """The default layout of the sidebars and the phone (the dock's own is in the script)."""
    order = {"left": 0, "right": 0}
    side: dict[str, Any] = {}
    for p in panels:
        if p.kind == "side":
            side[p.id] = {"side": p.side, "open": p.open, "ord": order[p.side]}
            order[p.side] += 1
    return {"v": 1, "side": side, "col": {p.id: True for p in panels if p.collapsed}, "phone": ["step"]}


# -- the plain panels ------------------------------------------------------------------------


def _console() -> None:
    """The simulation console (newest first)."""
    with html.Div(classes="nf-console"):
        html.Div("{{ l.t }}", v_for="(l, i) in logs", key="i", classes=("l.c",))


def _fields_table() -> None:
    """Every field: min, max, mean, who wrote it last; a click highlights its writers / readers."""
    with v3.VTable(density="compact", classes="nf-vars", fixed_header=True):
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
                with html.Td(classes="nf-mono",
                             style=("f.color ? `color:${f.color}` : ''",)):
                    html.Span(classes="nf-dot mr-1", v_if="f.color",
                              style=("`background:${f.color}`",))
                    html.Span("{{ f.name }}")
                html.Td("{{ f.min }}")
                html.Td("{{ f.max }}")
                html.Td("{{ f.mean }}")
                html.Td("{{ f.written_by }}", style="opacity:.7")
    html.Div("The fields appear at the first pause after initialization.", v_if="!fields_rows.length",
             classes="nf-empty")


def _variables() -> None:
    """The solver context's scalars."""
    with html.Table(classes="nf-kvt", v_if="scalars.length"):
        with html.Tr(v_for="s in scalars", key="s.k"):
            html.Td("{{ s.k }}")
            html.Td("{{ s.v }}", classes="nf-mono")
    html.Div("The context's scalars appear at the first pause.", v_if="!scalars.length", classes="nf-empty")


def _options() -> None:
    """The run's options: animation delay, wait for UI, follow, timing, minimap; reset the layout."""
    with html.Div(classes="nf-opts"):
        with html.Div(classes="d-flex align-center"):
            html.Span("delay {{ delay_ms }} ms", classes="text-caption mr-2",
                      style="white-space: nowrap; min-width: 82px;")
            v3.VSlider(v_model=("delay_ms",), min=0, max=1000, step=10, hide_details=True,
                       density="compact", style="min-width: 120px;",
                       title="pause after every event while running, to follow the "
                             "token (0: none; only while the step view shows)")
        v3.VCheckbox(v_model=("wait_ui",), label="wait for UI", hide_details=True, density="compact",
                     title="the solver waits until every event and frame is shown "
                           "(slower); off, it never waits and a lagging UI skips some")
        v3.VCheckbox(v_model=("follow",), label="follow (pan to the token)", hide_details=True,
                     density="compact")
        v3.VCheckbox(v_model=("heat_on",), label="timing", hide_details=True, density="compact",
                     title="tint nodes by their summed run time")
        v3.VCheckbox(v_model=("minimap_on",), label="minimap", hide_details=True, density="compact",
                     title="the step view's minimap")
        html.Div("Step view hidden: the solver runs as it would without the UI "
                 "(breakpoints and Pause still work).", v_if="!dag_on", classes="nf-empty")
        v3.VBtn("Reset layout", size="small", variant="tonal", prepend_icon="mdi-view-dashboard-outline",
                classes="mt-2", click="window.nfL.reset()",
                title="the default panels and dock; the layout is remembered per browser")
