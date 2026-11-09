"""The page: a slim toolbar, the activity bars, the sidebars, the dock (main area), the footer;
on a phone a panel picker, the stack of the panels picked, and a drawer with all of them.

The panels (:mod:`neofoam_ui._ui._panels`) are trame templates of their own; the dock
(trame-dockview), the sidebars and the phone's stack show them. The layout's state and
logic are the browser's (:mod:`neofoam_ui._ui._dock`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from trame.ui.html import DivLayout
from trame.ui.vuetify3 import VAppLayout
from trame.widgets import client, dockview, html
from trame.widgets import vuetify3 as v3

from neofoam_ui._ui._dock import _script
from neofoam_ui._ui._panels import _catalogue, _Panel, _side_defaults
from neofoam_ui._ui._styles import _CSS

if TYPE_CHECKING:
    from neofoam_ui._ui._app import StepperApp

__all__ = ()

#: The layout's client-only state keys (each browser has its own layout).
_CLIENT_ONLY = ("nf_lay", "nf_dk", "nf_phone", "nf_drawer")


def _build(app: StepperApp) -> None:
    """Build the page (once): the panels' templates, then the page around them."""
    panels = _catalogue(app)
    app.panels = panels
    defaults = _side_defaults(panels)
    state = app.state
    state.nf_lay = defaults
    state.nf_dk = {"open": {}, "vis": {}}
    state.nf_phone = None  # decided by the browser before anything is laid out
    state.nf_drawer = False
    state.client_only(*_CLIENT_ONLY)
    for p in panels:
        with DivLayout(app.server, p.template, classes=f"nf-pc nf-pc-{p.kind} nf-pc-{p.id}"):
            p.build()

    mains = [p for p in panels if p.kind == "main"]
    sides = [p for p in panels if p.kind == "side"]
    with VAppLayout(app.server, full_height=True):
        client.Style(_CSS + "".join(p.css for p in app.panes))
        client.Script(_script([p.client() for p in panels], defaults))
        client.ClientTriggers(mounted="window.nfL && window.nfL.init()")
        # Who watches: a visible tab counts (closed or hidden, a run reports no events).
        client.ClientTriggers(mounted=(
            "let shown = window.document.visibilityState !== 'hidden'; if (shown) trigger('nf_viewer', [true]);"
            " window.document.addEventListener('visibilitychange', () => {"
            " const now = window.document.visibilityState !== 'hidden';"
            " if (now !== shown) { shown = now; trigger('nf_viewer', [now]); } });"
            " window.addEventListener('pagehide', () => { if (shown) { shown = false;"
            " trigger('nf_viewer', [false]); } })"
        ))
        # Debugger keys: F11 into, F10 over, Shift+F11 out, F8 run.
        client.ClientTriggers(mounted=(
            "window.addEventListener('keydown', (e) => {"
            " const k = {F11: e.shiftKey ? 'out' : 'into', F10: 'over', F8: 'run'}[e.key];"
            " if (k) { e.preventDefault(); trigger('nf_key', [k]); } })"
        ))
        with html.Div(classes="nf-shell"):
            _toolbar(app)
            # -- desktop: activity bars, sidebars, dock
            with html.Div(classes="nf-body", v_if="nf_phone === false"):
                with html.Nav(classes="nf-abar left"):
                    html.Span("main", classes="nf-lbl")
                    for p in mains:
                        _launcher(p, "ab", f"nf_dk.vis.{p.id} ? 'on' : nf_dk.open.{p.id} ? 'bg' : ''")
                    html.Span(classes="nf-div")
                    html.Span("left", classes="nf-lbl")
                    for p in sides:
                        _launcher(p, "ab", _side_on(p), v_if=f"nf_lay.side.{p.id}.side === 'left'")
                for where in ("left", "right"):
                    with html.Aside(classes=f"nf-sbar {where}",
                                    v_if=f"Object.values(nf_lay.side).some(s => s.open && s.side === '{where}')"):
                        for p in sides:
                            _panel(p, f"nf_lay.side.{p.id}.open && nf_lay.side.{p.id}.side === '{where}'",
                                   f"nf_lay.side.{p.id}.ord", movable=True)
                    if where == "left":
                        with html.Main(classes="nf-main"):
                            dockview.DockView(
                                ref="nf_dock", theme="Light", default_renderer="always",
                                ready="window.nfL.ready()", panel_visibility="window.nfL.vis($event)",
                                remove_panel="window.nfL.removed($event)",
                                layout_changed="window.nfL.layout($event)",
                            )
                with html.Nav(classes="nf-abar right"):
                    html.Span("right", classes="nf-lbl")
                    for p in sides:
                        _launcher(p, "ab", _side_on(p), v_if=f"nf_lay.side.{p.id}.side === 'right'")
            # -- phone: a picker row, the panels picked (newest on top), a drawer with all
            with html.Nav(classes="nf-pbar", v_if="nf_phone"):
                with html.Button(classes="nf-pmenu", title="All panels", click="nf_drawer = true"):
                    v3.VIcon("mdi-menu", size=20)
                    html.Span("All")
                with html.Div(classes="nf-pscroll"):
                    for p in mains:
                        _launcher(p, "pb2", f"nf_lay.phone.includes('{p.id}') ? 'on' : ''", label=p.short)
                    html.Span(classes="nf-pgap")
                    for p in sides:
                        _launcher(p, "pb2", f"nf_lay.phone.includes('{p.id}') ? 'on' : ''", label=p.short)
            with html.Div(classes="nf-pstack", v_if="nf_phone"):
                for p in panels:
                    _panel(p, f"nf_lay.phone.includes('{p.id}')", f"nf_lay.phone.indexOf('{p.id}')")
                html.Div("Nothing shown. Tap an icon above to show a panel.", classes="nf-pempty",
                         v_if="!nf_lay.phone.length")
            with html.Footer(classes="nf-foot"):
                html.Span(classes="nf-grow")
                html.Span("Layout is remembered in this browser ·", classes="nf-mut")
                html.Button("Reset layout", classes="nf-lnk", click="window.nfL.reset()")
                html.Span("· drag tabs to split or dock · ⇄ moves a side panel to the other sidebar",
                          classes="nf-mut nf-dhint")
        _drawer(panels)


def _side_on(p: _Panel) -> str:
    return f"nf_lay.side.{p.id}.open ? 'on' : ''"


def _launcher(p: _Panel, cls: str, on: str, label: Optional[str] = None, **kw: Any) -> None:
    """An activity bar's (``ab``) or the phone picker's (``pb2``) button: opens, focuses, closes."""
    title = p.title + (f": {p.tip}" if p.tip else "")
    with html.Button(classes=(f"['{cls}', {on}]",), title=title, click=f"window.nfL.toggle('{p.id}')", **kw):
        v3.VIcon(p.icon, size=20)
        if label:
            html.Span(label)
        if p.badge:
            html.Span(f"{{{{ {p.badge} }}}}", classes="nf-badge", v_if=p.badge)


def _panel(p: _Panel, show: str, order: str, movable: bool = False) -> None:
    """A panel with its header (icon, title, move, collapse, close), in a sidebar or the phone's stack."""
    with html.Section(classes=(f"['nf-panel', {{ collapsed: nf_lay.col.{p.id} }}]",), v_if=show,
                      style=(f"{{ order: {order} }}",), raw_attrs=[f'data-id="{p.id}"']):
        with html.Div(classes="nf-ph"):
            v3.VIcon(p.icon, size=15)
            html.Span(p.title, classes="t", title=p.tip or None)
            if movable:
                html.Button("⇄", classes="hb", title="move to the other sidebar",
                            click=f"window.nfL.move('{p.id}')")
            html.Button("{{ nf_lay.col." + p.id + " ? '▸' : '▾' }}", classes="hb", title="collapse / expand",
                        click=f"window.nfL.collapse('{p.id}')")
            html.Button("×", classes="hb", title="close", click=f"window.nfL.close('{p.id}')")
        with html.Div(classes="nf-pb", v_if=f"!nf_lay.col.{p.id}"):
            client.ServerTemplate(name=p.template)


def _drawer(panels: list[_Panel]) -> None:
    """The phone's drawer: every panel with an on/off switch; "Step view only", "Reset layout"."""
    html.Div(classes="nf-scrim", v_if="nf_phone && nf_drawer", click="nf_drawer = false")
    with html.Aside(classes=("['nf-drawer', { open: nf_drawer }]",), v_if="nf_phone"):
        with html.Div(classes="nf-dh"):
            html.Span("Panels", classes="nf-grow")
            html.Button("×", classes="hb", title="close", click="nf_drawer = false")
        with html.Div(classes="nf-dl"):
            for kind, head in (("main", "Views"), ("side", "Panels")):
                html.Div(head, classes="nf-dsec")
                for p in panels:
                    if p.kind != kind:
                        continue
                    with html.Div(classes=(f"['nf-drow', {{ on: nf_lay.phone.includes('{p.id}') }}]",),
                                  click=f"window.nfL.toggle('{p.id}')"):
                        v3.VIcon(p.icon, size=18)
                        html.Span(p.title)
                        html.I(classes="nf-sw")
        with html.Div(classes="nf-df"):
            html.Span("Tap to show or hide. Shown panels stack in the order you open them, newest on top.")
            with html.Div(classes="d-flex", style="gap: 8px;"):
                html.Button("Step view only", classes="nf-sbtn", click="window.nfL.only('step')")
                html.Button("Reset layout", classes="nf-sbtn", click="window.nfL.reset()")


def _toolbar(app: StepperApp) -> None:
    """Solver, run / step buttons, the init / execution graph, the status, Options."""
    idle = "!connected || running || finished"
    with html.Header(classes="nf-tb"):
        v3.VChip("{{ solver_name }}", size="small", variant="tonal", classes="nf-chip",
                 prepend_icon="mdi-function-variant")
        html.Span(classes="nf-sep")
        _button(app, "Run", "mdi-play", "run", idle, color="primary", variant="flat",
                title="Run until breakpoint or end (F8)")
        _button(app, "Pause", "mdi-pause", None, "!running", click=app.pause)
        _button(app, "Stop", "mdi-stop", None, "!connected || finished", click=app.stop)
        _button(app, "Restart", "mdi-restart", None, "false", click=app.restart)
        html.Span(classes="nf-sep")
        _button(app, "Into", "mdi-debug-step-into", "into", idle, title="Step into (F11)")
        _button(app, "Over", "mdi-debug-step-over", "over", idle, title="Step over (F10)")
        _button(app, "Out", "mdi-debug-step-out", "out", f"{idle} || (cur_kind !== 'init' && depth === 0)",
                title="Step out (Shift+F11)")
        _button(app, "Next time step", "mdi-skip-next", "time_step", idle)
        _button(app, "Skip init", "mdi-fast-forward", "skip_init", f"{idle} || cur_kind !== 'init'")
        html.Span(classes="nf-sep", v_show="dag_on")
        # the step view's graph: one at a time; it follows the run's phase
        with v3.VBtnToggle(v_model=("dag_view",), mandatory=True, density="compact", variant="outlined",
                           divided=True, classes="nf-seg", v_show="dag_on",
                           title="show the initialization or the execution graph"):
            v3.VBtn("Init", value="init", size="small")
            v3.VBtn("Execution", value="exec", size="small")
        html.Span(classes="nf-sep nf-brk")
        html.Span("{{ status }}", classes=("['nf-status', failed ? 'err' : running ? 'run' : 'stop']",),
                  title=("status",))
        html.Span(classes="nf-grow")
        with v3.VBtn(icon=True, size="small", variant="text", classes=("['nf-gear', { on: nf_phone"
                     " ? nf_lay.phone.includes('options') : nf_lay.side.options.open }]",),
                     title="Options: delay, wait for UI, follow, timing, minimap",
                     click="window.nfL.toggle('options')"):
            v3.VIcon("mdi-cog-outline", size="small")


def _button(app: StepperApp, text: str, icon: str, mode: Optional[str], disabled: str,
            click: Any = None, **kw: Any) -> None:
    """Toolbar button; the label hides on narrow screens, leaving the icon."""
    kw.setdefault("variant", "text")
    with v3.VBtn(size="small", classes="nf-btn", disabled=(disabled,), title=kw.pop("title", text),
                 click=click or (lambda: app.step(mode)), **kw):
        v3.VIcon(icon, size="small")
        html.Span(text, classes="nf-btxt ml-1")
