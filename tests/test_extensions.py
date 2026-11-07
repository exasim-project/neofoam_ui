"""A pane from outside: registered without editing the app, it gets a toolbar toggle, a slot,
the worker's messages, and a say in whether frames are rendered."""

from trame.widgets import html

from neofoam_ui import extensions
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
    from trame.app import get_server  # noqa: PLC0415

    from neofoam_ui.app import StepperApp  # noqa: PLC0415

    server = get_server(name, client_type="vue3")
    return server, StepperApp(server, "neofoam_ui.toy.heat1d:Heat1D", registry=registry)


def test_an_extension_pane_takes_part_like_a_built_in_one():
    registry = ExtensionRegistry()
    registry.add_pane(Residuals)
    server, app = _app("ext_pane", registry)
    pane = app.pane("residuals")
    html_ = server.state._pending_update["trame__template_main"]
    assert 'click="rs_on = !rs_on"' in html_ and "mdi-chart-bell-curve" in html_  # its toggle
    assert "fv_on || pp_on || pt_on || rs_on" in html_  # it widens the grid like a side pane
    assert "!dag_on && !fv_on && !pp_on && !pt_on && !rs_on" in html_  # and counts for "all closed"
    assert "{{ rs_lines.length }} lines" in html_
    assert any(".nf-rs { flex: 1 1 0; }" in str(v) for v in server.state._pending_update.values())
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
