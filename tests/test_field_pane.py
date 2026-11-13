"""The field pane (``_ui/panes/_field.py``): image frames as data URLs, the mode and display options
the worker is asked for, and why data mode is greyed out. The pane runs in the test's interpreter, which
has no ParaView: data mode itself is covered by ``test_live_data.py`` under ``pvpython``."""

import base64

import pytest

from trame.app import get_server

from neofoam_ui.app import StepperApp
from neofoam_ui.protocol import CATALYST_LIVE_ENV

PNG = b"\x89PNG\r\n\x1a\n"


def _app(name):
    app = StepperApp(get_server(name, client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D")
    sent = []
    app.link.send = sent.append
    app.state.fv_on = True
    app.state.fv_field = "T"
    app.pane("field").send_view()  # what showing the pane does
    return app, sent


def _frame(**extra):
    return {"type": "frame", "mode": "image", "image": base64.b64encode(PNG).decode(), "field": "T",
            "component": "mag", "view": ["T", "mag", "image"], "time": 0.5,
            "fields": [{"name": "T", "ncomp": 1}], **extra}


def test_an_image_frame_shows_as_a_data_url():
    app, _ = _app("field_pane_image")
    with app.state:
        app.dispatch(_frame())
    assert app.state.fv_src == "data:image/png;base64," + base64.b64encode(PNG).decode()


def _last_view(sent):
    return [c for c in sent if c["cmd"] == "view"][-1]


def test_the_view_command_names_the_mode_and_the_camera():
    _, sent = _app("field_pane_mode")
    assert _last_view(sent) == {"cmd": "view", "on": True, "field": "T", "component": "mag", "mode": "image",
                                "options": {"camera": "reset", "zoom": "1"}}


def test_the_image_is_rendered_at_the_size_the_browser_reports():
    app, sent = _app("field_pane_size")
    app.pane("field")._on_size(800, 450, 2)
    assert _last_view(sent)["options"] == {"camera": "reset", "zoom": "1", "width": "800", "height": "450",
                                           "scale": "2"}


def test_a_camera_preset_is_at_zoom_1_and_zoom_multiplies():
    app, sent = _app("field_pane_camera")
    pane = app.pane("field")
    pane.zoom(1.25)
    pane.look("+x")
    assert _last_view(sent)["options"] == {"camera": "+x", "zoom": "1"}
    pane.zoom(1.25)
    pane.zoom(1e9)  # bounded
    assert _last_view(sent)["options"]["zoom"] == "50"


@pytest.mark.parametrize("settings, options", [
    ({"fv_range": "fixed", "fv_lo": 1, "fv_hi": 2}, {"range": "1:2"}),
    ({"fv_range": "fixed", "fv_lo": 2, "fv_hi": 1}, {}),  # not a range: auto
    ({"fv_cmap": "jet", "fv_log": True}, {"cmap": "jet", "log": "1"}),
    ({"fv_show": "slice", "fv_axis": "x", "fv_pos": 25}, {"show": "slice", "pos": "0.25", "axis": "x"}),
    ({"fv_show": "clip", "fv_axis": "y", "fv_flip": True}, {"show": "clip", "pos": "0.5", "axis": "y", "flip": "1"}),
    ({"fv_show": "volume"}, {}),  # an image draws the volume's surface
])
def test_display_settings_become_options(settings, options):
    app, sent = _app("field_pane_options_" + "_".join(map(str, settings.values())))
    for key, value in settings.items():
        app.state[key] = value
    app.pane("field").send_view()
    assert _last_view(sent)["options"] == {"camera": "reset", "zoom": "1", **options}


def test_data_mode_sends_only_the_cut():
    app, sent = _app("field_pane_data_options")
    app.state.update({"fv_mode": "data", "fv_show": "volume", "fv_cmap": "jet", "fv_zoom": 3})
    app.pane("field").send_view()
    assert _last_view(sent)["options"] == {"show": "volume"}


def test_another_field_puts_the_colour_range_back_to_auto():
    app, _ = _app("field_pane_range_auto")
    app.state.update({"fv_range": "fixed", "fv_lo": 1, "fv_hi": 2})
    app.pane("field")._on_field()
    assert app.state.fv_range == "auto"


def test_display_settings_survive_a_restart():
    app, _ = _app("field_pane_restart")
    app.state.update({"fv_camera": "-y", "fv_zoom": 2.0, "fv_cmap": "inferno", "fv_show": "clip", "fv_pos": 30})
    app._reset_state()
    assert (app.state.fv_camera, app.state.fv_zoom, app.state.fv_cmap, app.state.fv_show, app.state.fv_pos) == \
        ("-y", 2.0, "inferno", "clip", 30)


def test_a_frame_reports_the_bounds_the_default_axis_and_its_notes():
    app, _ = _app("field_pane_report")
    with app.state:
        app.dispatch(_frame(report={"bounds": [0, 4, 0, 1, 0, 1], "axis": "x", "notes": ["log scale off"]}))
    assert app.state.fv_bounds == [0, 4, 0, 1, 0, 1] and app.state.fv_axis == "x"
    assert app.state.fv_msg.endswith("⚠ log scale off")


def test_data_mode_is_greyed_out_without_paraview():
    app, _ = _app("field_pane_no_paraview")
    assert not app.state.fv_data_ok and "pvpython" in app.state.fv_data_why
    assert app.link.data_url is None


def test_data_mode_is_greyed_out_while_a_desktop_paraview_has_live(monkeypatch):
    monkeypatch.setenv(CATALYST_LIVE_ENV, "localhost:22222")
    app, _ = _app("field_pane_desktop_live")
    assert not app.state.fv_data_ok and "localhost:22222" in app.state.fv_data_why
