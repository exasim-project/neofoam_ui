"""The scene graph as plain data (``scenes/__init__.py``, no pydantic): inputs and order."""

import pytest

from neofoam_ui import scenes

TWO_DISPLAYS = {"nodes": [{"id": "view", "type": "View"}, {"id": "d2", "type": "Display"},
                          {"id": "c1", "type": "Contour"}, {"id": "d1", "type": "Display"},
                          {"id": "grid", "type": "grid"}],
                "edges": [["grid", "d1"], ["grid", "c1"], ["c1", "d2"], ["d1", "view"], ["d2", "view"]]}


def test_inputs_of_each_node():
    assert scenes.inputs(TWO_DISPLAYS) == {"view": ["d1", "d2"], "d2": ["c1"], "c1": ["grid"], "d1": ["grid"],
                                           "grid": []}


def test_ordered_puts_every_input_first():
    assert [n["id"] for n in scenes.ordered(TWO_DISPLAYS)] == ["grid", "c1", "d1", "d2", "view"]


def test_ordered_refuses_a_cycle():
    graph = {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [["a", "b"], ["b", "a"]]}
    with pytest.raises(ValueError, match="cycle"):
        scenes.ordered(graph)


def test_the_field_graph_is_a_fresh_copy():
    scenes.field_graph()["nodes"].clear()
    assert len(scenes.field_graph()["nodes"]) == 4
