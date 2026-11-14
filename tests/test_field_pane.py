"""The field pane (``_ui/panes/_field.py``): image frames as data URLs, the scene and image size the
worker is asked for, the camera overlay writing the scene's View node, and why data mode is greyed
out. The pane runs in the test's interpreter, which has no ParaView: data mode itself is covered by
``test_live_data.py`` under ``pvpython``; a stand-in Live view covers what the pane does with it."""

import base64
import json
import shutil
from pathlib import Path

import pytest
from trame.app import get_server

from neofoam_ui._ui.panes import _field
from neofoam_ui.app import StepperApp

PNG = b"\x89PNG\r\n\x1a\n"
CASES = Path(__file__).parent / "scenes" / "cases"
CONFIG = Path(__file__).parent / "config" / "cases"


@pytest.fixture
def case(tmp_path):
    """A case with the scenes ``slice`` and ``threshold`` (and bad files)."""
    shutil.copytree(CASES / "listing", tmp_path / "case")
    return tmp_path / "case"


def _app(name, case):
    app = StepperApp(get_server(name, client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D", case=str(case))
    sent = []
    app.link.send = sent.append
    app.state.fv_on = True
    app.state.fv_field = "T"
    app.pane("field").send_view()  # what showing the pane does
    return app, sent


def _frame(**extra):
    return {"type": "frame", "mode": "image", "image": base64.b64encode(PNG).decode(), "field": "T",
            "component": "mag", "view": ["T", "mag", "image"], "scene": "Field", "time": 0.5,
            "fields": [{"name": "T", "ncomp": 1}], **extra}


def _last_view(sent):
    return [c for c in sent if c["cmd"] == "view"][-1]


def _graph(sent):
    return json.loads(_last_view(sent)["options"]["graph"])


def _view_node(graph):
    """The View node's camera (its layout and cell cameras aside)."""
    props = next(n for n in graph["nodes"] if n["type"] == "View")["props"]
    return {k: props[k] for k in ("camera", "zoom")}


def test_an_image_frame_shows_as_a_data_url(case):
    app, _ = _app("field_pane_image", case)
    with app.state:
        app.dispatch(_frame())
    assert app.state.fv_src == "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_the_view_command_names_the_mode_and_the_built_in_scene(case):
    _, sent = _app("field_pane_mode", case)
    assert _last_view(sent) == {"cmd": "view", "on": True, "field": "T", "component": "mag", "mode": "image",
                                "options": {"scene": "Field"}}


def test_the_image_is_rendered_at_the_size_the_browser_reports(case):
    app, sent = _app("field_pane_size", case)
    app.pane("field")._on_size(800, 450, 2)
    assert _last_view(sent)["options"] == {"scene": "Field", "width": "800", "height": "450", "scale": "2"}


def test_a_scene_of_the_case_goes_with_its_graph(case):
    app, sent = _app("field_pane_scene", case)
    app.pane("field").scenes.switch("slice")
    assert _last_view(sent)["options"]["scene"] == "slice"
    slice_node = next(n for n in _graph(sent)["nodes"] if n["id"] == "s1")
    assert slice_node == {"id": "s1", "type": "Slice", "props": {"axis": "x", "position": 0.9, "triangulate": True}}
    assert "layout" not in _graph(sent)


def test_a_camera_preset_on_field_edits_an_unsaved_copy(case):
    app, sent = _app("field_pane_camera_field", case)
    app.pane("field").look("+x")
    assert app.state.sc_current == "field-copy"
    assert _view_node(_graph(sent)) == {"camera": "+x", "zoom": 1.0}
    assert not (case / "system" / "scenes" / "field-copy.json").exists()


def test_a_camera_preset_is_at_zoom_1_and_zoom_multiplies(case):
    app, sent = _app("field_pane_camera", case)
    pane = app.pane("field")
    pane.scenes.switch("slice")
    pane.zoom(1.25)
    assert _view_node(_graph(sent)) == {"camera": "+x", "zoom": 1.25}
    pane.look("-y")
    pane.zoom(1e9)  # bounded
    assert _view_node(_graph(sent)) == {"camera": "-y", "zoom": 50.0}


def test_each_scene_keeps_its_own_camera(case):
    app, sent = _app("field_pane_camera_per_scene", case)
    pane = app.pane("field")
    pane.scenes.switch("slice")
    pane.look("-z")
    pane.scenes.switch("threshold")
    pane.look("+y")
    pane.scenes.switch("slice")
    assert _view_node(_graph(sent)) == {"camera": "-z", "zoom": 1.0}


def test_the_scene_and_its_unsaved_camera_survive_a_restart(case):
    app, sent = _app("field_pane_restart", case)
    pane = app.pane("field")
    pane.scenes.switch("slice")
    pane.look("-z")
    app._reset_state()
    pane.send_view()
    assert app.state.sc_current == "slice" and _view_node(_graph(sent)) == {"camera": "-z", "zoom": 1.0}


def test_a_frame_of_another_scene_is_not_shown(case):
    app, _ = _app("field_pane_other_scene", case)
    with app.state:
        app.dispatch(_frame(scene="slice"))
    assert app.state.fv_src is None


def test_a_scene_error_shows_instead_of_the_picture(case):
    app, _ = _app("field_pane_scene_error", case)
    app.dispatch(_frame())
    app.dispatch({"type": "frame", "image": None, "fields": [], "scene": "Field",
                  "error": "scene 'Field': node 'surface' (Surface): broken"})
    assert app.state.fv_src is None and "node 'surface'" in app.state.fv_msg


def test_a_frame_reports_its_scene_and_notes(case):
    app, _ = _app("field_pane_report", case)
    with app.state:
        app.dispatch(_frame(report={"bounds": [0, 4, 0, 1, 0, 1], "axis": "x", "notes": ["log scale off"]}))
    assert "scene Field" in app.state.fv_msg and app.state.fv_msg.endswith("⚠ log scale off")


def test_data_mode_is_greyed_out_without_paraview(case):
    app, _ = _app("field_pane_no_paraview", case)
    assert not app.state.fv_data_ok and "pvpython" in app.state.fv_data_why
    assert app.link.data_url is None


def test_data_mode_is_greyed_out_while_a_desktop_paraview_has_live(case):
    shutil.copy(CONFIG / "desktop_live" / "system" / "neofoam_ui.yaml", case / "system")
    app, _ = _app("field_pane_desktop_live", case)
    assert not app.state.fv_data_ok and "localhost:22222" in app.state.fv_data_why


class _FakeLive:
    """Stands in for ``_live._LiveData`` (needs pvpython): records what the pane asks of it."""

    def __init__(self, port, on_status):
        self.url = f"localhost:{port}"
        self.calls = []
        self.note = ""

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args)) or True


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(_field, "_LIVE", type("Live", (), {"_LiveData": _FakeLive}))


def test_data_mode_is_offered_for_every_scene(live, case):
    app, _ = _app("field_pane_data_every_scene", case)
    app.pane("field").scenes.switch("threshold")
    assert app.state.fv_data_ok


def test_another_scene_stays_in_data_mode(live, case):
    app, sent = _app("field_pane_data_stays", case)
    app.state.fv_mode = "data"
    app.pane("field").scenes.switch("threshold")
    assert app.state.fv_mode == "data" and _last_view(sent)["options"]["scene"] == "threshold"


def test_data_mode_draws_the_displays_the_frame_names(live, case):
    app, _ = _app("field_pane_data_extracts", case)
    pane = app.pane("field")
    app.state.fv_mode = "data"
    pane.send_view()
    extracts = [{"id": "d1", "source": "threshold:t1", "field": "T", "component": "mag", "colormap": "jet"}]
    app.dispatch(_frame(mode="data", image=None, view=["T", "mag", "data"], report={"extracts": extracts}))
    assert ("show", extracts) in pane.data.calls


BOUNDS = [0.0, 4.0, 0.0, 1.0, 0.0, 0.1]


def _in_data_mode(app):
    """Data mode with a frame shown: its bounds known, the data view drawn."""
    pane = app.pane("field")
    app.state.fv_mode = "data"
    app.state.fv_size = [800, 400, 1]
    pane.send_view()
    app.dispatch(_frame(mode="data", image=None, view=["T", "mag", "data"],
                        report={"bounds": BOUNDS, "extracts": [{"id": "display", "source": "Field:surface"}]}))
    return pane


def _fits(pane):
    return [c[1:] for c in pane.data.calls if c[0] == "fit"]


def test_the_first_data_fits_the_view_to_the_domain_as_the_scene_says(live, case):
    app, _ = _app("field_pane_data_first_fit", case)
    assert _fits(_in_data_mode(app)) == [("reset", 1.0, BOUNDS, (800, 400))]


def test_a_camera_preset_in_data_mode_sets_the_scene_and_refits(live, case):
    app, _ = _app("field_pane_data_camera", case)
    pane = _in_data_mode(app)
    pane.look("+z")
    assert app.state.sc_current == "field-copy" and _fits(pane)[-1] == ("+z", 1.0, BOUNDS, (800, 400))


def test_a_new_size_or_scene_refits_the_data_view(live, case):
    app, _ = _app("field_pane_data_refit", case)
    pane = _in_data_mode(app)
    pane._on_size(600, 600, 1)
    pane.scenes.switch("slice")  # its View looks from +x
    assert _fits(pane)[-2:] == [("reset", 1.0, BOUNDS, (600, 600)), ("+x", 1.0, BOUNDS, (600, 600))]


def test_data_mode_asks_for_the_frame_again_with_new_extracts(live, case):
    app, sent = _app("field_pane_data_again", case)
    pane = app.pane("field")
    app.state.fv_mode = "data"
    pane.send_view()
    pane.data.requested = "slice:s1"  # what the frame's displays need now
    app.dispatch(_frame(mode="data", image=None, view=["T", "mag", "data"],
                        report={"extracts": [{"id": "d1", "source": "slice:s1"}]}))
    assert _last_view(sent)["options"]["extracts"] == "slice:s1"


@pytest.fixture
def builder(tmp_path):
    """A case with scenes whose View has a layout (``side-by-side``, ``side-by-side-per-cell``, ``quad``)."""
    shutil.copytree(CASES / "builder", tmp_path / "case")
    return tmp_path / "case"


def _cell_cameras(graph):
    return next(n for n in graph["nodes"] if n["type"] == "View")["props"]["cell_cameras"]


def test_data_mode_is_greyed_out_for_a_view_of_more_than_one_cell(live, builder):
    app, _ = _app("field_pane_data_layout", builder)
    app.state.fv_mode = "data"
    app.pane("field").scenes.switch("side-by-side")
    assert (app.state.fv_data_ok, app.state.fv_mode) == (False, "image")
    assert app.state.fv_data_why == "data mode shows one cell: this scene's View has the 1x2 layout (image mode only)"


def test_data_mode_is_back_for_a_scene_of_one_cell(live, builder):
    app, _ = _app("field_pane_data_back", builder)
    app.pane("field").scenes.switch("side-by-side")
    app.pane("field").scenes.switch("slice")
    assert app.state.fv_data_ok


def test_the_pane_knows_the_cells_of_the_scene(builder):
    app, _ = _app("field_pane_grid", builder)
    app.pane("field").scenes.switch("quad")
    assert app.state.fv_grid == {"rows": 2, "cols": 2, "names": ["tl", "tr", "bl", "br"], "per_cell": False}


def test_linked_cameras_move_every_cell(builder):
    app, sent = _app("field_pane_linked", builder)
    pane = app.pane("field")
    pane.scenes.switch("side-by-side")
    pane.look("-y", "right")
    graph = _graph(sent)
    assert _view_node(graph) == {"camera": "-y", "zoom": 1.0} and _cell_cameras(graph) == {}


def test_per_cell_a_camera_button_moves_its_cell(builder):
    app, sent = _app("field_pane_per_cell", builder)
    pane = app.pane("field")
    pane.scenes.switch("side-by-side-per-cell")
    assert app.state.fv_grid["per_cell"]
    pane.look("+z", "left")
    graph = _graph(sent)
    assert _view_node(graph) == {"camera": "+x", "zoom": 1.0}
    assert _cell_cameras(graph) == {"left": {"camera": "+z", "zoom": 1.0}, "right": {"camera": "-x", "zoom": 2.0}}


def test_per_cell_the_wheel_zooms_the_cell_under_the_mouse(builder):
    app, sent = _app("field_pane_per_cell_zoom", builder)
    pane = app.pane("field")
    pane.scenes.switch("side-by-side-per-cell")
    pane.zoom(1.25, "right")
    assert _cell_cameras(_graph(sent))["right"] == {"camera": "-x", "zoom": 2.5}


def test_a_frame_of_several_cells_says_how_many(builder):
    app, _ = _app("field_pane_cells_msg", builder)
    app.pane("field").scenes.switch("quad")
    app.dispatch(_frame(scene="quad"))
    assert "4 cells" in app.state.fv_msg
