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


def _coupled(name, regions, couples, extra=()):
    from neofoam_ui.engine import Op  # noqa: PLC0415

    body = []
    for r in regions:
        body += [Op(f"receive_{r}.x", "action", lambda ctx: None, region=r),
                 Op(f"{r}.solve", "action", lambda ctx: None, region=r),
                 Op(f"send_{r}.x", "action", lambda ctx: None, region=r)]
    return Op(name, "loop", lambda ctx: False, [*extra, *body], couples=couples)


def test_a_coupling_loop_puts_each_region_in_a_lane_and_draws_the_exchange():
    loop = _coupled("couple", ["a", "b"], [("send_a.x", "receive_b.x", "x"), ("send_b.x", "receive_a.x", "x")])
    ops = [loop]
    assign_ids(ops)
    flow = build_flow([], [op_dict(o) for o in ops])
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert [nodes[f"couple::lane{i}"]["data"]["label"] for i in (0, 1)] == ["a", "b"]
    a, b = nodes["couple/a.solve"]["position"], nodes["couple/b.solve"]["position"]
    assert a["y"] == b["y"] and a["x"] < b["x"]  # side by side
    exchange = {(e["source"], e["target"]): e for e in flow["edges"] if e.get("class") == "nf-couple"}
    assert set(exchange) == {("couple/send_a.x", "couple/receive_b.x"), ("couple/send_b.x", "couple/receive_a.x")}
    assert exchange[("couple/send_a.x", "couple/receive_b.x")]["sourceHandle"] == "r"
    assert exchange[("couple/send_b.x", "couple/receive_a.x")]["targetHandle"] == "rt"
    assert exchange[("couple/send_a.x", "couple/receive_b.x")]["label"] == "x"
    # control flow stays, behind the exchange
    assert any(e.get("class") == "nf-secondary" and e["source"] == "couple/send_a.x"
               and e["target"] == "couple/receive_b.x" for e in flow["edges"])


def test_a_nested_coupling_loop_sits_in_the_lane_it_continues():
    inner = _coupled("inner", ["a", "b"], [("send_a.x", "receive_b.x", "x"), ("send_b.x", "receive_a.x", "x")])
    from neofoam_ui.engine import Op  # noqa: PLC0415

    outer = Op("outer", "loop", lambda ctx: False, [
        Op("receive_b.x", "action", lambda ctx: None, region="b"), inner,
        Op("send_b.x", "action", lambda ctx: None, region="b"),
        Op("receive_c.x", "action", lambda ctx: None, region="c"),
        Op("c.solve", "action", lambda ctx: None, region="c"),
        Op("send_c.x", "action", lambda ctx: None, region="c"),
    ], couples=[("send_b.x", "receive_c.x", "x"), ("send_c.x", "receive_b.x", "x")])
    ops = [outer]
    assign_ids(ops)
    flow = build_flow([], [op_dict(o) for o in ops])
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert [nodes[f"outer::lane{i}"]["data"]["label"] for i in (0, 1)] == ["b, a", "c"]
    exchange = {(e["source"], e["target"]) for e in flow["edges"] if e.get("class") == "nf-couple"}
    # the outer loop's own b ops, not the inner loop's of the same name
    assert ("outer/send_b.x", "outer/receive_c.x") in exchange
    assert ("outer/inner/send_a.x", "outer/inner/receive_b.x") in exchange


def _chain_model():
    from neofoam_ui._ui._model import _RunModel  # noqa: PLC0415
    from neofoam_ui.engine import Op  # noqa: PLC0415

    inner = _coupled("inner", ["a", "b"], [("send_a.x", "receive_b.x", "x"), ("send_b.x", "receive_a.x", "x")])
    outer = Op("outer", "loop", lambda ctx: False, [
        Op("receive_b.x", "action", lambda ctx: None, region="b"), inner,
        Op("send_b.x", "action", lambda ctx: None, region="b"),
        Op("receive_c.x", "action", lambda ctx: None, region="c"),
        Op("c.solve", "action", lambda ctx: None, region="c"),
        Op("send_c.x", "action", lambda ctx: None, region="c"),
    ], couples=[("send_b.x", "receive_c.x", "x"), ("send_c.x", "receive_b.x", "x")])
    ops = [outer]
    assign_ids(ops)
    model = _RunModel()
    model.graph({"activity": "exec", "ops": [op_dict(o) for o in ops]})
    model.reindex("probe")
    return model


def test_a_nested_coupling_loop_is_collapsed_unless_the_token_or_a_breakpoint_is_inside():
    from types import SimpleNamespace  # noqa: PLC0415

    from neofoam_ui._ui.panes._dag import _DagPane  # noqa: PLC0415

    model = _chain_model()
    assert model.nested_couplings() == {"outer/inner"}
    pane = SimpleNamespace(model=model, _opened=set(),
                           app=SimpleNamespace(state=SimpleNamespace(cur_node="outer/c.solve", bps=[], collapsed=[])))
    collapsed = lambda: _DagPane._collapsed(pane)  # noqa: E731
    assert collapsed() == {"outer/inner"}
    pane.app.state.cur_node = "outer/inner/a.solve"
    assert collapsed() == set()
    pane.app.state.cur_node, pane.app.state.bps = "outer/c.solve", ["outer/inner/b.solve"]
    assert collapsed() == set()
    pane.app.state.bps, pane._opened = [], {"outer/inner"}
    assert collapsed() == set()


def test_a_collapsed_coupling_loop_names_its_regions_and_labels_do_not_overlap():
    model = _chain_model()
    flow = build_flow([], model.ops, collapsed=frozenset({"outer/inner"}))
    nodes = {n["id"]: n for n in flow["nodes"]}
    assert nodes["outer/inner"]["data"]["badge"] == "a ↔ b"
    labels = [e["labelStyle"]["transform"] for e in flow["edges"] if e.get("class") == "nf-couple"]
    assert len(labels) == 2 and labels[0] != labels[1]
