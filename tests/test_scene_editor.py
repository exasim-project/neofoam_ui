"""The scene editor (``_ui/panes/_scene_editor.py``, forms ``_scene_form.py``): the canvas of the
current scene, adding nodes (click: after the selected one, linked; drop: unlinked), the form from
the pydantic schema, removing nodes and links (right-click, Del) in the draft, port names from the
node models, drafts kept per scene until Apply / Revert, a refused Apply naming the node,
and what frames tell the nodes (build errors, times). A View with a layout has a port per cell;
another layout in the form moves the links cell by cell and drops those of cells gone. Runs on the app as the page drives it, on a
copy of a case; the typed-link check is the canvas's own JavaScript, run with Node.js."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from trame.app import get_server

from neofoam_ui._ui.panes import _scene_editor, _scene_form
from neofoam_ui.app import StepperApp

CASES = Path(__file__).parent / "scenes" / "cases"


@pytest.fixture
def case(tmp_path):
    shutil.copytree(CASES / "listing", tmp_path / "case")
    return tmp_path / "case"


def _editor(name, case):
    app = StepperApp(get_server(name, client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D", case=str(case))
    sent = []
    app.link.send = sent.append
    app.state.fv_on = True
    pane = app.pane("scene_editor")
    pane.scenes.switch("slice")
    return app, pane, sent


def _views(sent):
    return [c for c in sent if c["cmd"] == "view"]


def _canvas(pane):
    graph = pane.editor.graph
    return ({n["id"]: n["data"]["kind"] for n in graph["nodes"]},
            sorted((e["source"], e["target"], e["sourceHandle"], e["targetHandle"]) for e in graph["edges"]))


def test_the_canvas_shows_the_current_scene_with_typed_ports(case):
    _, pane, _ = _editor("se_canvas", case)
    assert _canvas(pane) == ({"grid": "grid", "s1": "Slice", "d1": "Display", "view": "View"},
                             [("d1", "view", "display", "display"), ("grid", "s1", "data", "data"),
                              ("s1", "d1", "data", "data")])


def _positions(pane):
    return {n["id"]: [n["position"]["x"], n["position"]["y"]] for n in pane.editor.graph["nodes"]}


def test_a_scene_without_layout_is_laid_out_left_to_right(case):
    _, pane, _ = _editor("se_layout", case)
    assert _positions(pane) == {"grid": [0, 0], "s1": [200, 0], "d1": [400, 0], "view": [600, 0]}


def test_auto_layout_places_every_node_anew(case):
    """A moved node goes back to the left-to-right layout."""
    _, pane, _ = _editor("se_arrange", case)
    graph = pane._draft()
    graph["layout"]["s1"] = [37, 410]
    pane._edit(graph, repush=True)
    pane.arrange()
    assert _positions(pane) == {"grid": [0, 0], "s1": [200, 0], "d1": [400, 0], "view": [600, 0]}
    assert pane.scenes.entry().edited["layout"]["s1"] == [200.0, 0.0]


def test_the_layout_has_columns_by_role(tmp_path):
    """slice-contour: a slice and a contour of the grid, a Display each: filters, then Displays, then the View."""
    shutil.copytree(CASES / "builder", tmp_path / "case")
    app = StepperApp(get_server("se_columns", client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D",
                     case=str(tmp_path / "case"))
    pane = app.pane("scene_editor")
    pane.scenes.switch("slice-contour")
    assert _positions(pane) == {"grid": [0, 0], "s1": [200, 0], "c1": [200, 110], "d1": [400, 0],
                                "d2": [400, 110], "view": [600, 0]}


def test_removing_a_node_leaves_the_others_where_they_are(case):
    _, pane, _ = _editor("se_layout_kept", case)
    pane.remove_node("s1")
    assert _positions(pane) == {"grid": [0, 0], "d1": [400, 0], "view": [600, 0]}


def test_a_display_added_after_a_filter_is_linked_to_the_view(case):
    _, pane, _ = _editor("se_display_view", case)
    pane.select("s1")
    pane.add("Display")
    assert {("s1", "display1"), ("display1", "view")} <= {tuple(e) for e in pane.scenes.entry().draft["edges"]}


def test_the_canvas_keeps_nodes_readable(case):
    """It never zooms out below 0.75 (fitting a wide graph shows part of it; pan to the rest)."""
    _, pane, _ = _editor("se_min_zoom", case)
    assert pane.editor._attributes["min_zoom"] == ':min-zoom="0.75"'


def test_a_click_adds_the_node_after_the_selected_one_linked(case):
    app, pane, _ = _editor("se_add", case)
    pane.select("s1")
    pane.add("Contour")
    draft = pane.scenes.entry().draft
    assert app.state.se_sel == "contour1" and ["s1", "contour1"] in draft["edges"]
    assert draft["layout"]["contour1"] == [400, 110]  # below d1, which is right of s1


def test_a_node_whose_ports_do_not_fit_is_added_unlinked(case):
    _, pane, _ = _editor("se_add_unlinked", case)
    pane.select("view")
    pane.add("Slice")
    assert not [e for e in pane.scenes.entry().draft["edges"] if "slice1" in e]


def test_a_drop_places_the_node_unlinked(case):
    _, pane, _ = _editor("se_drop", case)
    pane.select("s1")
    pane.drop("Threshold", 120.0, 300.0)
    draft = pane.scenes.entry().draft
    assert draft["layout"]["threshold1"] == [120.0, 300.0] and not [e for e in draft["edges"] if "threshold1" in e]


def test_a_new_link_into_a_one_input_node_replaces_its_input(case):
    _, pane, _ = _editor("se_one_input", case)
    pane.drop("Clip", 0.0, 200.0)
    graph = pane.editor.graph
    edges = graph["edges"] + [{"source": "clip1", "target": "d1"}]
    pane.on_graph_change(graph["nodes"], edges)
    assert sorted(map(tuple, pane.scenes.entry().draft["edges"])) == [("clip1", "d1"), ("d1", "view"), ("grid", "s1")]


def test_the_form_sets_the_selected_node_in_the_draft(case):
    app, pane, sent = _editor("se_form", case)
    pane.select("s1")
    pane.prop("position", 0.3)
    pane.prop("axis", "y")
    s1 = next(n for n in pane.scenes.entry().draft["nodes"] if n["id"] == "s1")
    assert s1["props"] == {"axis": "y", "position": 0.3, "triangulate": True}
    assert app.state.se_changes == 1 and app.state.se_form["axis"] == "y" and json.loads(_views(sent)[-1]["options"]["graph"])["nodes"][1]["props"]["axis"] == "x"


def test_apply_shows_the_draft(case):
    app, pane, sent = _editor("se_apply", case)
    pane.select("s1")
    pane.prop("axis", "z")
    pane.apply()
    assert json.loads(_views(sent)[-1]["options"]["graph"])["nodes"][1]["props"]["axis"] == "z"
    assert app.state.se_changes == 0 and pane.scenes.entry().draft is None


def test_apply_refuses_a_bad_draft_naming_the_node(case):
    app, pane, sent = _editor("se_refused", case)
    shown = _views(sent)[-1]
    pane.select("s1")
    pane.add("Contour")  # its values are required
    pane.apply()
    assert app.state.se_errors == {"contour1": "node 'contour1' (Contour): props.values: Field required"}
    assert _views(sent)[-1] == shown and pane.scenes.entry().draft is not None


def test_revert_drops_the_draft(case):
    app, pane, _ = _editor("se_revert", case)
    pane.select("s1")
    pane.add("Contour")
    pane.revert()
    assert pane.scenes.entry().draft is None and "contour1" not in _canvas(pane)[0]


def test_a_draft_is_kept_per_scene_when_switching(case):
    _, pane, _ = _editor("se_draft_kept", case)
    pane.select("s1")
    pane.add("Contour")
    pane.scenes.switch("threshold")
    assert "contour1" not in _canvas(pane)[0]
    pane.scenes.switch("slice")
    assert "contour1" in _canvas(pane)[0] and pane.scenes.entry().draft is not None


def test_editing_field_edits_a_copy(case):
    app, pane, _ = _editor("se_field", case)
    pane.scenes.switch("Field")
    pane.select("surface")
    pane.add("Slice")
    assert app.state.sc_current == "field-copy" and "slice1" in _canvas(pane)[0]
    assert "slice1" not in {n["id"] for n in pane.scenes.entry("Field").graph["nodes"]}


def test_remove_takes_the_node_and_its_links(case):
    _, pane, _ = _editor("se_remove", case)
    pane.remove_node("d1")
    assert pane.scenes.entry().draft["edges"] == [["grid", "s1"]]


def test_remove_link_takes_only_that_link(case):
    _, pane, _ = _editor("se_remove_link", case)
    pane.remove_link("s1->d1")
    assert pane.scenes.entry().draft["edges"] == [["grid", "s1"], ["d1", "view"]]


def test_a_removal_shows_on_the_canvas(case):
    """The server removes, then pushes the draft: the canvas holds what the draft holds."""
    _, pane, _ = _editor("se_remove_canvas", case)
    pane.remove_node("s1")
    assert _canvas(pane) == ({"grid": "grid", "d1": "Display", "view": "View"}, [("d1", "view", "display", "display")])


def test_delete_removes_the_selected_node(case):
    _, pane, _ = _editor("se_delete_node", case)
    pane.select("s1")
    pane.delete()
    assert [n["id"] for n in pane.scenes.entry().draft["nodes"]] == ["grid", "d1", "view"]


def test_delete_removes_the_selected_link(case):
    app, pane, _ = _editor("se_delete_link", case)
    pane.select_link("grid->s1")
    pane.delete()
    assert pane.scenes.entry().draft["edges"] == [["s1", "d1"], ["d1", "view"]] and app.state.se_link is None


def test_the_selected_link_stays_selected_when_the_canvas_is_redrawn(case):
    _, pane, _ = _editor("se_link_selected", case)
    pane.select_link("s1->d1")
    pane.remove_link("grid->s1")
    assert [e["id"] for e in pane.editor.graph["edges"] if e["selected"]] == ["s1->d1"]


def test_revert_brings_a_removed_node_back(case):
    _, pane, _ = _editor("se_remove_revert", case)
    pane.remove_node("s1")
    pane.revert()
    assert _canvas(pane) == ({"grid": "grid", "s1": "Slice", "d1": "Display", "view": "View"},
                             [("d1", "view", "display", "display"), ("grid", "s1", "data", "data"),
                              ("s1", "d1", "data", "data")])


@pytest.mark.parametrize("node", ["grid", "view"])
def test_grid_and_view_are_not_removed(case, node):
    _, pane, _ = _editor(f"se_fixed_{node}", case)
    pane.select(node)
    pane.delete()
    assert pane.scenes.entry().draft is None


def test_only_removable_nodes_offer_a_menu(case):
    _, pane, _ = _editor("se_removable", case)
    assert {n["id"]: n["data"]["removable"] for n in pane.editor.graph["nodes"]} == \
        {"grid": False, "s1": True, "d1": True, "view": False}


def test_the_ports_are_named_by_the_node_models(case):
    _, pane, _ = _editor("se_port_names", case)
    assert {n["id"]: (n["data"]["takes_name"], n["data"]["gives_name"]) for n in pane.editor.graph["nodes"]} == \
        {"grid": (None, "mesh"), "s1": ("input", "output"), "d1": ("input", "to view"), "view": ("displays", None)}


def test_vue_flows_own_delete_is_off(case):
    """Its Backspace removed a node in the browser only and blanked the canvas; the server removes now."""
    _, pane, _ = _editor("se_no_vue_delete", case)
    assert pane.editor._attributes["delete_key_code"] == ':delete-key-code="null"'


def test_a_build_error_marks_its_node(case):
    app, pane, _ = _editor("se_error", case)
    app.dispatch({"type": "frame", "image": None, "fields": [], "scene": "slice", "node": "s1",
                  "error": "scene 'slice': node 's1' (Slice): broken"})
    assert app.state.se_errors == {"s1": "scene 'slice': node 's1' (Slice): broken"}


def test_each_node_shows_its_last_execute_time(case):
    app, pane, _ = _editor("se_ms", case)
    app.dispatch({"type": "frame", "mode": "image", "image": "", "field": "T", "component": "mag",
                  "view": ["T", "mag", "image"], "scene": "slice", "fields": [{"name": "T", "ncomp": 1}],
                  "pipeline_ms": {"pv1": 0.5, "pv2": 1.25, "pv3": 2.0},
                  "report": {"proxies": {"grid": ["pv1"], "s1": ["pv2", "pv9"], "view": ["pv3"]}}})
    assert app.state.se_ms == {"grid": "0.5 ms", "s1": "1.2 ms", "view": "2.0 ms"}


@pytest.mark.parametrize("kind, fields", [
    ("Slice", [("axis", "choice"), ("position", "slider"), ("triangulate", "check")]),
    ("Contour", [("field", "field"), ("component", "choice"), ("values", "text")]),
    ("Calculator", [("expression", "string"), ("result", "string")]),
    ("Display", [("field", "field"), ("component", "choice"), ("colormap", "choice"), ("range", "text"),
                 ("log", "check"), ("color", "text"), ("line_width", "number"), ("opacity", "slider"), ("bar", "check")]),
    ("View", [("camera", "choice"), ("zoom", "slider"), ("layout", "choice"), ("cameras", "choice")]),
])
def test_the_form_comes_from_the_schema(kind, fields):
    assert [(f["name"], f["kind"]) for f in _scene_form.form(kind)] == fields


@pytest.mark.parametrize("name, text, value", [
    ("values", "2, 5, 8", [2, 5, 8]), ("values", "0.5", [0.5]), ("range", "auto", "auto"), ("range", "1, 2", [1, 2]),
    ("color", "1, 0, 0", [1, 0, 0]),
])
def test_a_typed_text_becomes_the_property(name, text, value):
    field = next(f for f in _scene_form.form("Display" if name in ("range", "color") else "Contour") if f["name"] == name)
    assert _scene_form.parsed(field, text) == value


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
@pytest.mark.parametrize("link, valid", [
    ({"source": "s1", "target": "d1", "sourceHandle": "data", "targetHandle": "data"}, True),
    ({"source": "d1", "target": "view", "sourceHandle": "display", "targetHandle": "display"}, True),
    ({"source": "s1", "target": "view", "sourceHandle": "data", "targetHandle": "display"}, False),
    ({"source": "s1", "target": "s1", "sourceHandle": "data", "targetHandle": "data"}, False),
    ({"source": "d1", "target": "view", "sourceHandle": "display", "targetHandle": "display:left"}, True),
    ({"source": "s1", "target": "view", "sourceHandle": "data", "targetHandle": "display:left"}, False),
])
def test_a_link_between_ports_of_other_types_is_refused_while_dragging(link, valid):
    code = f"console.log(JSON.stringify(({_scene_editor._VALID_LINK})({json.dumps(link)})))"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) is valid


def test_a_node_added_where_another_is_goes_below_it(case):
    _, pane, _ = _editor("se_add_free", case)
    pane.select("grid")
    pane.add("Clip")  # where s1 is
    assert pane.scenes.entry().draft["layout"]["clip1"] == [200, 110]


@pytest.fixture
def builder(tmp_path):
    shutil.copytree(CASES / "builder", tmp_path / "case")
    return tmp_path / "case"


def _on(name, case, scene):
    app = StepperApp(get_server(name, client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D", case=str(case))
    app.link.send = lambda _: None
    pane = app.pane("scene_editor")
    pane.scenes.switch(scene)
    return app, pane


def _to_view(pane):
    return sorted(tuple(e) for e in pane.scenes.entry().edited["edges"] if e[1] == "view")


def test_the_view_has_a_port_per_cell(builder):
    _, pane = _on("se_cells", builder, "quad")
    view = next(n for n in pane.editor.graph["nodes"] if n["id"] == "view")
    assert view["data"]["cells"] == ["tl", "tr", "bl", "br"]
    assert sorted((e["source"], e["targetHandle"]) for e in pane.editor.graph["edges"] if e["target"] == "view") == [
        ("d1", "display:tl"), ("d2", "display:tr"), ("d3", "display:bl")]


def test_a_one_cell_view_has_its_one_port(case):
    _, pane, _ = _editor("se_one_cell", case)
    view = next(n for n in pane.editor.graph["nodes"] if n["id"] == "view")
    assert view["data"]["cells"] == []


def test_a_link_to_a_cell_names_it_in_the_edge(builder):
    _, pane = _on("se_link_cell", builder, "side-by-side")
    graph = pane.editor.graph
    edges = [e for e in graph["edges"] if e["source"] != "d2"] + [
        {"source": "d2", "target": "view", "sourceHandle": "display", "targetHandle": "display:left"}]
    pane.on_graph_change(graph["nodes"], edges)
    assert _to_view(pane) == [("d1", "view", "left"), ("d2", "view", "left")]


def test_a_display_linked_to_another_cell_leaves_its_first(builder):
    _, pane = _on("se_one_cell_each", builder, "side-by-side")
    graph = pane.editor.graph
    edges = graph["edges"] + [{"source": "d1", "target": "view", "sourceHandle": "display",
                               "targetHandle": "display:right"}]
    pane.on_graph_change(graph["nodes"], edges)
    assert _to_view(pane) == [("d1", "view", "right"), ("d2", "view", "right")]


def test_a_display_added_goes_into_the_first_cell(builder):
    _, pane = _on("se_add_cell", builder, "side-by-side")
    pane.select("s1")
    pane.add("Display")
    assert ("display1", "view", "left") in _to_view(pane)


@pytest.mark.parametrize("layout, edges", [
    ("2x2", [("d1", "view", "tl"), ("d2", "view", "tr")]),
    ("2x1", [("d1", "view", "top"), ("d2", "view", "bottom")]),
    ("1", [("d1", "view")]),
])
def test_another_layout_moves_the_links_cell_by_cell(builder, layout, edges):
    """side-by-side: d1 left, d2 right; ``1`` has one cell, so d2's link goes."""
    _, pane = _on(f"se_relayout_{layout}", builder, "side-by-side")
    pane.select("view")
    pane.prop("layout", layout)
    assert _to_view(pane) == edges


def test_fewer_cells_drop_the_links_of_the_cells_gone(builder):
    """quad (2x2: d1 tl, d2 tr, d3 bl) to 1x2: d3 is no longer shown; Revert brings it back."""
    _, pane = _on("se_fewer_cells", builder, "quad")
    pane.select("view")
    pane.prop("layout", "1x2")
    assert _to_view(pane) == [("d1", "view", "left"), ("d2", "view", "right")]
    pane.revert()
    assert _to_view(pane) == [("d1", "view", "tl"), ("d2", "view", "tr"), ("d3", "view", "bl")]


def test_fewer_cells_drop_the_cameras_of_the_cells_gone(builder):
    """side-by-side-per-cell: the right cell has its own camera; one cell (``1``) keeps none of it."""
    _, pane = _on("se_fewer_cameras", builder, "side-by-side-per-cell")
    pane.select("view")
    pane.prop("layout", "2x2")
    view = next(n for n in pane.scenes.entry().draft["nodes"] if n["id"] == "view")
    assert view["props"]["cell_cameras"] == {"tr": {"camera": "-x", "zoom": 2.0}}
    pane.prop("layout", "1")
    view = next(n for n in pane.scenes.entry().draft["nodes"] if n["id"] == "view")
    assert view["props"]["cell_cameras"] == {}


def test_a_draft_with_a_new_layout_applies(builder):
    _, pane = _on("se_relayout_apply", builder, "quad")
    pane.select("view")
    pane.prop("layout", "1x2")
    pane.apply()
    assert pane.scenes.entry().draft is None and _to_view(pane) == [("d1", "view", "left"), ("d2", "view", "right")]


def test_each_cell_shows_its_last_render_time(builder):
    app, pane = _on("se_cell_ms", builder, "side-by-side")
    app.dispatch({"type": "frame", "mode": "image", "image": "", "field": "T", "component": "mag",
                  "view": ["T", "mag", "image"], "scene": "side-by-side", "fields": [{"name": "T", "ncomp": 1}],
                  "pipeline_ms": {"pv3": 2.0, "pv4": 3.0},
                  "report": {"proxies": {"view": ["pv3", "pv4"]}, "cells": {"left": "pv3", "right": "pv4"}}})
    assert app.state.se_ms["view"] == "5.0 ms" and app.state.se_cell_ms == {"left": "2.0 ms", "right": "3.0 ms"}
