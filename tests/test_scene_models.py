"""Scene models (``scenes/models.py``): every scene file of the test cases is one, every graph of
``tests/scenes/invalid`` is refused with the node and the reason, the built-in ``Field`` graph is
what its models give, and the node properties have the JSON schema the editor's forms need.
A View's layout names its input ports (one per cell); a scene file from before layouts reads as a
one-cell View with linked cameras, its edges as they were."""

from pathlib import Path

import pytest

from neofoam_ui.scenes import cell_of, field_graph
from neofoam_ui.scenes import models

SCENES = Path(__file__).parent / "scenes"
VALID = sorted(p for p in (SCENES / "cases").glob("*/system/scenes/*.json")
               if p.stem not in ("broken", "not-json", "two-views", "Field"))


@pytest.mark.parametrize("path", VALID, ids=lambda p: f"{p.parts[-4]}/{p.stem}")
def test_a_scene_file_of_the_cases_is_a_scene(path):
    assert models.parse(path.read_text()).nodes


@pytest.mark.parametrize("name, error", [
    ("bad-component", "node 'd1' (Display): props.component: Input should be '@component', 'mag', '0', '1' or '2'"),
    ("bad-position", "node 's1' (Slice): props.position: Input should be less than or equal to 1"),
    ("contour-without-values", "node 'c1' (Contour): props.values: Field required"),
    ("cycle", "node 'a': its inputs form a cycle"),
    ("data-into-view", "node 'view' (View) takes display, not the data of node 's1' (Surface)"),
    ("display-into-filter", "node 's1' (Slice) takes data, not the display of node 'd1' (Display)"),
    ("duplicate-id", "node 's1' (Clip): the id is used twice"),
    ("falling-range", "node 'd1' (Display): props: range [2.0, 1.0]: the upper end must be above the lower one"),
    ("missing-node", "edge 'd1' → 'viewport': no node 'viewport'"),
    ("no-input", "node 't1' (Threshold) needs one input, has 0"),
    ("no-view", "a scene has one View node, not 0"),
    ("two-grids", "node 'grid2' (grid): a scene has at most one grid"),
    ("two-inputs", "node 'd1' (Display) needs one input, has 2"),
    ("unknown-prop", "node 'c1' (Clip): props.side: Extra inputs are not permitted"),
    ("unknown-type", "node 'w1' (Warp): Input tag 'Warp' found using 'type' does not match"),
    ("version-2", "version: Input should be 1"),
    ("cell-not-named", "edge 'd1' → 'view': name the cell of the 1x2 layout (left, right)"),
    ("no-such-cell", "edge 'd1' → 'view': node 'view' (View) has no port 'middle' (ports: left, right)"),
    ("wrong-port", "edge 'grid' → 's1': node 's1' (Slice) has no port 'left' (ports: input)"),
    ("display-in-two-cells", "node 'd1' (Display) shows in one cell of the View, not 2"),
    ("camera-of-no-cell", "node 'view' (View): props: cell_cameras: no cell 'tl' in the 1x2 layout (left, right)"),
    ("bad-layout", "node 'view' (View): props.layout: Input should be '1', '1x2', '2x1' or '2x2'"),
])
def test_a_bad_graph_is_refused_naming_the_node(name, error):
    with pytest.raises(models.SceneError) as refused:
        models.parse((SCENES / "invalid" / f"{name}.json").read_text())
    assert str(refused.value).startswith(error)


def test_text_that_is_not_json_is_refused():
    with pytest.raises(models.SceneError, match="not JSON"):
        models.parse('{"nodes": [')


def test_the_field_graph_is_what_its_models_give():
    assert models.parse(field_graph()).graph() == field_graph()


def test_the_graph_leaves_the_title_and_the_layout_out():
    scene = models.parse({**field_graph(), "title": "p slice", "layout": {"grid": [0, 0]}})
    assert (scene.title, scene.layout) == ("p slice", {"grid": (0.0, 0.0)}) and scene.graph() == field_graph()


@pytest.mark.parametrize("node", [models.GridNode, models.SurfaceNode, models.SliceNode, models.ClipNode,
                                  models.ContourNode, models.ThresholdNode, models.CalculatorNode, models.GlyphNode,
                                  models.StreamTracerNode, models.DisplayNode, models.ViewNode])
def test_every_node_has_a_properties_schema(node):
    schema = node.model_fields["props"].annotation.model_json_schema()
    assert schema["type"] == "object" and schema.get("additionalProperties") is False


def test_read_scenes_lists_a_bad_file_with_its_error():
    files = {f.name: f for f in models.read_scenes(SCENES / "cases" / "listing")}
    assert files["slice"].scene is not None and files["two-views"].scene is None
    assert files["two-views"].error == "a scene has one View node, not 2"


def test_write_scene_writes_what_read_scenes_reads(tmp_path):
    scene = models.parse((SCENES / "cases" / "builder" / "system" / "scenes" / "every-node.json").read_text())
    models.write_scene(tmp_path, "every", scene)
    (read,) = models.read_scenes(tmp_path)
    assert read.name == "every" and read.scene == scene


@pytest.mark.parametrize("layout, ports", [("1", ("displays",)), ("1x2", ("left", "right")),
                                           ("2x1", ("top", "bottom")), ("2x2", ("tl", "tr", "bl", "br"))])
def test_the_views_ports_are_the_cells_of_its_layout(layout, ports):
    assert models.ViewNode.input_ports({"layout": layout}) == ports


def test_a_scene_file_from_before_layouts_has_one_cell_and_linked_cameras():
    scene = models.parse((SCENES / "cases" / "listing" / "system" / "scenes" / "slice.json").read_text())
    graph = scene.graph()
    view = next(n for n in graph["nodes"] if n["type"] == "View")
    assert (view["props"]["layout"], view["props"]["cameras"], view["props"]["cell_cameras"]) == ("1", "linked", {})
    assert graph["edges"] == [["grid", "s1"], ["s1", "d1"], ["d1", "view"]]


def test_an_edge_may_name_the_one_cell_of_a_one_cell_view():
    graph = {**field_graph(), "edges": [["grid", "surface"], ["surface", "display"], ["display", "view", "displays"]]}
    assert models.parse(graph).graph()["edges"][2] == ["display", "view", "displays"]


def test_each_display_is_in_the_cell_its_edge_names():
    scene = models.parse((SCENES / "cases" / "builder" / "system" / "scenes" / "quad.json").read_text())
    assert cell_of(scene.graph()) == {"d1": "tl", "d2": "tr", "d3": "bl"}
