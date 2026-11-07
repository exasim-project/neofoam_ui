"""The page: toolbar, drawer, the canvas row (main pane, side panes), inspector, console, variables."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from trame.ui.vuetify3 import SinglePageWithDrawerLayout
from trame.widgets import client, html
from trame.widgets import vuetify3 as v3

from neofoam_ui._ui._styles import _CSS
from neofoam_ui.extensions import PaneToggle

if TYPE_CHECKING:
    from neofoam_ui._ui._app import StepperApp

__all__ = ()


def _build(app: StepperApp) -> None:
    """Build the page (once)."""
    toggled = [p for p in app.panes if p.toggle is not None]
    mains = [p for p in app.panes if p.placement == "main"]
    sides = [p.toggle.key for p in toggled if p.placement == "side"]
    idle = "!connected || running || finished"
    with SinglePageWithDrawerLayout(app.server) as layout:
        layout.title.set_text("DAG stepper")
        client.Style(_CSS + "".join(p.css for p in app.panes))
        # Phones: drawer closed and overlaying instead of pushing the content.
        # Who watches: a visible tab counts (closed or hidden, a run reports no events).
        client.ClientTriggers(mounted=(
            "let shown = window.document.visibilityState !== 'hidden'; if (shown) trigger('nf_viewer', [true]);"
            " window.document.addEventListener('visibilitychange', () => {"
            " const now = window.document.visibilityState !== 'hidden';"
            " if (now !== shown) { shown = now; trigger('nf_viewer', [now]); } });"
            " window.addEventListener('pagehide', () => { if (shown) { shown = false;"
            " trigger('nf_viewer', [false]); } })"
        ))
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
                _button(app, "Run", "mdi-play", "run", idle, color="primary", variant="flat",
                             title="Run until breakpoint or end (F8)")
                _button(app, "Pause", "mdi-pause", None, "!running", click=app.pause)
                _button(app, "Stop", "mdi-stop", None, "!connected || finished", click=app.stop)
                _button(app, "Restart", "mdi-restart", None, "false", click=app.restart)
                v3.VDivider(vertical=True, classes="mx-1")
                _button(app, "Into", "mdi-debug-step-into", "into", idle, title="Step into (F11)")
                _button(app, "Over", "mdi-debug-step-over", "over", idle, title="Step over (F10)")
                _button(app, "Out", "mdi-debug-step-out", "out",
                             f"{idle} || (cur_kind !== 'init' && depth === 0)",
                             title="Step out (Shift+F11)")
                _button(app, "Next time step", "mdi-skip-next", "time_step", idle)
                _button(app, "Skip init", "mdi-fast-forward", "skip_init",
                             f"{idle} || cur_kind !== 'init'")
                v3.VDivider(vertical=True, classes="mx-1")
                # the DAG's view: one graph at a time; it follows the run's phase
                with v3.VBtnToggle(v_model=("dag_view",), mandatory=True, density="compact",
                                   variant="outlined", divided=True, classes="mx-1", v_show="dag_on",
                                   style="flex: none;",
                                   title="show the initialization or the execution graph"):
                    v3.VBtn("Init", value="init", size="small")
                    v3.VBtn("Execution", value="exec", size="small")
                v3.VDivider(vertical=True, classes="mx-1")
                for pane in toggled:
                    _toggle(pane.toggle)
                v3.VDivider(vertical=True, classes="mx-1")
                # run options in a menu, so the toolbar fits 1600 px
                with v3.VMenu(close_on_content_click=False, location="bottom end"):
                    with html.Template(v_slot_activator="{ props }"):
                        with v3.VBtn(v_bind="props", size="small", variant="text", classes="mx-1",
                                     title="delay, wait for UI, follow, timing, minimap"):
                            v3.VIcon("mdi-tune", size="small")
                            html.Span("Options", classes="nf-btxt ml-1")
                    with v3.VCard(classes="pa-3 d-flex flex-column", style="min-width: 280px;"):
                        with html.Div(classes="d-flex align-center"):
                            html.Span("delay {{ delay_ms }} ms", classes="text-caption mr-2",
                                      style="white-space: nowrap; min-width: 82px;")
                            v3.VSlider(v_model=("delay_ms",), min=0, max=1000, step=10, hide_details=True,
                                       density="compact", style="min-width: 140px;",
                                       title="pause after every event while running, to follow the "
                                             "token (0: none; only with the DAG pane open)")
                        v3.VCheckbox(v_model=("wait_ui",), label="wait for UI", hide_details=True,
                                     density="compact",
                                     title="the solver waits until every event and frame is shown "
                                           "(slower); off, it never waits and a lagging UI skips some")
                        v3.VCheckbox(v_model=("follow",), label="follow", hide_details=True,
                                     density="compact")
                        v3.VCheckbox(v_model=("heat_on",), label="timing", hide_details=True,
                                     density="compact", title="tint nodes by their summed run time")
                        v3.VCheckbox(v_model=("minimap_on",), label="minimap", hide_details=True,
                                     density="compact", title="the DAG's minimap")

        with layout.drawer as drawer:
            app.dag.build_drawer(drawer)

        with layout.content:
            with html.Div(classes=(f"['nf-grid', {{ fv: {' || '.join(sides)} }}]",)):
                with html.Div(classes="nf-canvas"):
                    for pane in mains:
                        with html.Div(classes="nf-diagram", v_show=pane.toggle.key if pane.toggle else "true"):
                            pane.build()
                    html.Div("All panes are closed: the solver runs as it would without the UI "
                             "(breakpoints and Pause still work).", classes="nf-idle",
                             v_show=" && ".join(f"!{p.toggle.key}" for p in toggled))
                    for pane in app.panes:
                        if pane.placement == "side":
                            pane.build()
                with html.Div(classes="nf-side"):
                    app.dag.build_inspector()
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
                                    with html.Td(classes="nf-mono",
                                                 style=("f.color ? `color:${f.color}` : ''",)):
                                        html.Span(classes="nf-dot mr-1", v_if="f.color",
                                                  style=("`background:${f.color}`",))
                                        html.Span("{{ f.name }}")
                                    html.Td("{{ f.min }}")
                                    html.Td("{{ f.max }}")
                                    html.Td("{{ f.mean }}")
                                    html.Td("{{ f.written_by }}", style="opacity:.7")


def _toggle(toggle: PaneToggle) -> None:
    """A pane's toolbar button (tonal while the pane shows)."""
    key = toggle.key
    with v3.VBtn(size="small", classes="mx-1 nf-view", click=f"{key} = !{key}",
                 variant=(f"{key} ? 'tonal' : 'text'",),
                 color=(f"{key} ? 'primary' : undefined",),
                 title=toggle.title):
        v3.VIcon(toggle.icon, size="small")
        html.Span(toggle.label, classes="nf-btxt ml-1")


def _button(app: StepperApp, text: str, icon: str, mode: Optional[str], disabled: str,
            click: Any = None, **kw: Any) -> None:
    """Toolbar button; the label hides on narrow screens, leaving the icon."""
    kw.setdefault("variant", "text")
    with v3.VBtn(size="small", classes="mx-1", disabled=(disabled,), title=kw.pop("title", text),
                 click=click or (lambda: app.step(mode)), **kw):
        v3.VIcon(icon, size="small")
        html.Span(text, classes="nf-btxt ml-1")
