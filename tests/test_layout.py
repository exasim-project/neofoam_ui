from neofoam_ui.engine import Session, assign_ids, topological_order
from neofoam_ui.engine._wire import _init_dict as init_dict
from neofoam_ui.engine._wire import _op_dict as op_dict
from neofoam_ui._ui._layout._dag import _build_flow as build_flow
from neofoam_ui.toy.heat1d import Heat1D


def _graph():
    solver = Heat1D()
    steps = [init_dict(s) for s in topological_order(solver.init_steps())]
    session = Session(solver, lambda m: None)
    session.advance("skip_init")
    ops = solver.execution_graph(session.ctx)
    assign_ids(ops)
    return steps, [op_dict(o) for o in ops]


def test_placeholder_before_initialization():
    steps, _ = _graph()
    flow = build_flow(steps, None)
    ids = {n["id"] for n in flow["nodes"]}
    assert "exec:placeholder" in ids and "init:mesh" in ids


def test_loops_are_groups_with_decision_and_back_edge():
    steps, ops = _graph()
    flow = build_flow(steps, ops)
    nodes = {n["id"]: n for n in flow["nodes"]}
    # parents precede children (VueFlow requirement)
    order = [n["id"] for n in flow["nodes"]]
    for n in flow["nodes"]:
        if "parentNode" in n:
            assert order.index(n["parentNode"]) < order.index(n["id"])
    assert nodes["time_loop/inner_loop"]["type"] == "loop"
    assert nodes["time_loop/inner_loop::test"]["parentNode"] == "time_loop/inner_loop"
    edges = {(e["source"], e["target"]) for e in flow["edges"]}
    assert ("time_loop/inner_loop/update_kappa", "time_loop/inner_loop::test") in edges  # back edge
    assert ("time_loop/inner_loop::test", "time_loop/inner_loop/assemble") in edges
    assert ("time_loop/inner_loop", "time_loop/write_output") in edges  # loop exit
    assert nodes["time_loop/inner_loop/apply_source"]["data"]["injected_by"] == "heatSource"
    # children fit inside their loop
    loop = nodes["time_loop/inner_loop"]
    for n in flow["nodes"]:
        if n.get("parentNode") == "time_loop/inner_loop":
            assert n["position"]["x"] + n["width"] <= loop["width"]
            assert n["position"]["y"] + n["height"] <= loop["height"]


def test_collapsed_loop_hides_its_body():
    steps, ops = _graph()
    full = build_flow(steps, ops)
    flow = build_flow(steps, ops, collapsed=frozenset({"time_loop/inner_loop"}))
    nodes = {n["id"]: n for n in flow["nodes"]}
    loop = nodes["time_loop/inner_loop"]
    assert loop["data"]["collapsed"] and loop["data"]["size"] == 4
    assert not any(i.startswith("time_loop/inner_loop/") or i == "time_loop/inner_loop::test"
                   for i in nodes)
    edges = {(e["source"], e["target"]) for e in flow["edges"]}
    assert ("time_loop/store_old_time", "time_loop/inner_loop") in edges
    assert ("time_loop/inner_loop", "time_loop/write_output") in edges
    assert all(e["source"] in nodes and e["target"] in nodes for e in flow["edges"])
    full_h = {n["id"]: n for n in full["nodes"]}["time_loop"]["height"]
    assert nodes["time_loop"]["height"] < full_h


def test_nodes_show_their_position():
    steps, ops = _graph()
    nodes = {n["id"]: n for n in build_flow(steps, ops)["nodes"]}
    assert nodes["time_loop/inner_loop/solve"]["data"]["number"] == "1.4.2"


def test_empty_loop_is_drawn():
    ops = [{"id": "wait", "name": "wait", "kind": "loop", "children": [], "test": "until()"},
           {"id": "after", "name": "after", "kind": "action", "children": []}]
    nodes = {n["id"]: n for n in build_flow([], ops)["nodes"]}
    assert nodes["wait"]["type"] == "loop" and "wait::test" in nodes and "after" in nodes


def test_region_ops_take_their_region_colour_and_make_the_legend():
    from neofoam_ui._ui._layout._palette import _REGION  # noqa: PLC0415
    from neofoam_ui._ui._model import _RunModel  # noqa: PLC0415
    from neofoam_ui.engine import Op  # noqa: PLC0415

    ops = [Op("loop", "loop", lambda ctx: False, [Op("a.solve", "action", lambda ctx: None, region="a"),
                                                  Op("b.solve", "action", lambda ctx: None, region="b")])]
    assign_ids(ops)
    model = _RunModel()
    model.graph({"activity": "exec", "ops": [op_dict(o) for o in ops]})
    nodes = {n["id"]: n for n in build_flow([], model.ops)["nodes"]}

    assert nodes["loop/a.solve"]["data"]["color"] == _REGION.color("a")
    assert nodes["loop/a.solve"]["data"]["color"] != nodes["loop/b.solve"]["data"]["color"]
    assert [r["name"] for r in model.regions()] == ["a", "b"]
