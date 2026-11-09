"""A pane from outside: registered without editing the app, it gets a launcher, a dock view,
the worker's messages, and a say in whether frames are rendered."""

from trame.app import get_server
from trame.widgets import html

from neofoam_ui import extensions
from neofoam_ui.app import StepperApp
from neofoam_ui.extensions import BasePane, ExtensionRegistry, PaneToggle


class Residuals(BasePane):
    name = "residuals"
    toggle = PaneToggle("rs_on", "Residuals", "mdi-chart-bell-curve", "the solver's residuals")
    css = ".nf-rs { flex: 1 1 0; }"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.seen = []
        self.spawns = 0

    def reset(self):
        self.ctx.state.rs_on = bool(self.ctx.state.rs_on)
        self.ctx.state.rs_lines = []

    def build(self):
        html.Div("{{ rs_lines.length }} lines", classes="nf-field nf-rs", v_show="rs_on")

    def handlers(self):
        return {"stdout": self.seen.append}

    def spawned(self):
        self.spawns += 1

    def wants_frames(self):
        return bool(self.ctx.state.rs_on)


def _app(name, registry):
    server = get_server(name, client_type="vue3")
    return server, StepperApp(server, "neofoam_ui.toy.heat1d:Heat1D", registry=registry)


def test_an_extension_pane_takes_part_like_a_built_in_one():
    registry = ExtensionRegistry()
    registry.add_pane(Residuals)
    server, app = _app("ext_pane", registry)
    pane = app.pane("residuals")
    pending = server.state._pending_update
    html_ = pending["trame__template_main"]
    # its launcher in the activity bar and the phone's picker, after the built-in views
    assert html_.count("window.nfL.toggle('residuals')") == 3 and "mdi-chart-bell-curve" in html_
    assert html_.index("toggle('fields')") < html_.index("toggle('residuals')") < html_.index("toggle('tree')")
    # a view of the dock: its widgets are a template of their own, shown while rs_on
    assert "{{ rs_lines.length }} lines" in pending["trame__template_nf_p_residuals"]
    view = next(p for p in app.panels if p.id == "residuals")
    assert (view.kind, view.flag, view.title, view.template) == ("main", "rs_on", "Residuals", "nf_p_residuals")
    script = next(v for k, v in pending.items() if k.startswith("trame__inline_script_"))
    assert '"id": "residuals"' in script and '"flag": "rs_on"' in script
    assert any(".nf-rs { flex: 1 1 0; }" in str(v) for v in pending.values())
    assert app.state.rs_on is False and app.state.rs_lines == []

    sent = []
    app.link.send = sent.append
    with app.state:
        app.dispatch({"type": "stdout", "text": "hello"})
    assert pane.seen == [{"type": "stdout", "text": "hello"}]
    assert app.pusher._lines[-1]["t"] == "hello"  # the app's own handler still runs

    field = app.pane("field")
    field.send_view()
    assert sent[-1]["on"] is False
    app.state.rs_on = True
    field.send_view()  # the extension pane asks for frames: the worker renders them
    assert sent[-1]["cmd"] == "view" and sent[-1]["on"] is True


def test_entry_points_are_loaded_once(monkeypatch):
    calls = []

    class EntryPoint:
        def load(self):
            return lambda registry: (calls.append(registry), registry.add_pane(Residuals))

    monkeypatch.setattr(extensions, "entry_points", lambda group: [EntryPoint()] if group == extensions.ENTRY_POINT_GROUP else [])
    registry = ExtensionRegistry()
    registry.load_entry_points()
    registry.load_entry_points()
    assert calls == [registry] and registry.panes == (Residuals,)
    _, app = _app("ext_entry", None)  # the default registry looks the entry points up too
    assert app.pane("residuals").toggle.key == "rs_on"
