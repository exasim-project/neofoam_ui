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


def _coupled(name, regions, sources, extra=()):
    """A coupling loop: per region its coupling node, taking ``sources[i]``'s data, then its solve."""
    from neofoam_ui.engine import Exchange, Op  # noqa: PLC0415

    body = []
    for r, (source, partner) in zip(regions, sources):
        body += [Op(f"couple_{r}.x", "action", lambda ctx: None, region=r,
                    exchange=Exchange(source, ("x",), partner)),
                 Op(f"{r}.solve", "action", lambda ctx: None, region=r)]
    return Op(name, "loop", lambda ctx: False, [*extra, *body])


def _flow(ops, **kw):
    assign_ids(ops)
    return build_flow([], [op_dict(o) for o in ops], **kw)


def _box(nodes, prefix, i):
    n = nodes[f"{prefix}::box{i}"]
    return n["position"]["x"], n["position"]["y"], n["width"], n["height"]


def test_a_coupling_loop_draws_free_solver_boxes_joined_only_by_data_lines():
    from neofoam_ui.engine import Op  # noqa: PLC0415

    loop = _coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])
    flow = _flow([Op("time", "loop", lambda ctx: False, [Op("set_time_step", "action", lambda ctx: None), loop])])
    nodes = {n["id"]: n for n in flow["nodes"]}
    p = "time/loop"

    # the boxes and every op of the body hang off the loop's parent, not the loop
    assert nodes[f"{p}::box0"]["type"] == "region"
    assert [nodes[f"{p}::box{i}"]["data"]["label"] for i in (0, 1)] == ["a", "b"]
    assert {nodes[i]["parentNode"] for i in (f"{p}::box0", f"{p}::box1", f"{p}/couple_a.x", f"{p}/b.solve")} == {"time"}
    # the loop itself is a compact control node with its test
    assert nodes[p]["data"]["control"] and nodes[f"{p}::test"]["parentNode"] == p
    # each coupling node inside its own solver's box, right above the op it feeds
    for i, region in enumerate("ab"):
        bx, by, bw, bh = _box(nodes, p, i)
        for name in (f"couple_{region}.x", f"{region}.solve"):
            n = nodes[f"{p}/{name}"]
            assert bx < n["position"]["x"] and n["position"]["x"] + n["width"] < bx + bw
            assert by < n["position"]["y"] and n["position"]["y"] + n["height"] < by + bh
        assert nodes[f"{p}/couple_{region}.x"]["position"]["y"] < nodes[f"{p}/{region}.solve"]["position"]["y"]
    # the coupling nodes face each other across the gap; the driver's op above, the body below
    # the control node
    assert nodes[f"{p}/couple_a.x"]["position"]["y"] == nodes[f"{p}/couple_b.x"]["position"]["y"]
    assert nodes["time/set_time_step"]["position"]["y"] < nodes[p]["position"]["y"] < _box(nodes, p, 0)[1]
    pairs = {(e["source"], e["target"]): e for e in flow["edges"]}
    # between the boxes only one line, coupling node to coupling node, both ways, labelled
    data = {k: e for k, e in pairs.items() if e.get("class") == "nf-couple"}
    assert list(data) == [(f"{p}/couple_a.x", f"{p}/couple_b.x")]
    line = data[(f"{p}/couple_a.x", f"{p}/couple_b.x")]
    assert line["label"] == "x" and line["markerStart"] and line["markerEnd"]
    assert (line["sourceHandle"], line["targetHandle"]) == ("r", "lt")
    # inside each box its own flow: the coupling node into the op it feeds
    body = {f"{p}/{n}" for n in ("couple_a.x", "a.solve", "couple_b.x", "b.solve")}
    flow_edges = {k for k in pairs if k[0] in body and k[1] in body} - set(data)
    assert flow_edges == {(f"{p}/couple_a.x", f"{p}/a.solve"), (f"{p}/couple_b.x", f"{p}/b.solve")}
    assert (f"{p}::test", f"{p}/couple_a.x") in pairs and (f"{p}/b.solve", f"{p}::test") in pairs


def test_a_loop_without_coupling_nodes_has_no_region_boxes():
    from neofoam_ui.engine import Op  # noqa: PLC0415

    flow = _flow([Op("loop", "loop", lambda ctx: False, [
        Op("a.solve", "action", lambda ctx: None, region="a"),
        Op("b.solve", "action", lambda ctx: None, region="b")])])

    assert not [n for n in flow["nodes"] if n["type"] == "region"]
    assert not [e for e in flow["edges"] if e.get("class") == "nf-couple"]
    assert not [n for n in flow["nodes"] if n["data"].get("control")]


def test_a_nested_coupling_loop_sits_in_the_box_it_continues():
    from neofoam_ui.engine import Exchange, Op  # noqa: PLC0415

    inner = _coupled("inner", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])
    outer = Op("outer", "loop", lambda ctx: False, [
        Op("couple_b.x", "action", lambda ctx: None, region="b", exchange=Exchange("c.solve", ("x",), "c")),
        inner,
        Op("couple_c.x", "action", lambda ctx: None, region="c", exchange=Exchange("inner", ("x",), "b")),
        Op("c.solve", "action", lambda ctx: None, region="c"),
    ])
    flow = _flow([outer])
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert [nodes[f"outer::box{i}"]["data"]["label"] for i in (0, 1)] == ["b, a", "c"]
    assert [nodes[f"outer/inner::box{i}"]["data"]["label"] for i in (0, 1)] == ["a", "b"]
    edges = {(e["source"], e["target"], e.get("class")) for e in flow["edges"]}
    # the outer coupling nodes joined to each other, the inner ones too; each feeds its op
    assert {("outer/couple_b.x", "outer/couple_c.x", "nf-couple"), ("outer/couple_b.x", "outer/inner", None),
            ("outer/couple_c.x", "outer/c.solve", None),
            ("outer/inner/couple_a.x", "outer/inner/couple_b.x", "nf-couple")} <= edges


def test_a_collapsed_loop_hides_the_exchange_into_it():
    flow = _flow([_coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])],
                 collapsed=frozenset({"loop"}))

    assert not [e for e in flow["edges"] if e.get("class") == "nf-couple"]


def test_the_inspector_names_a_coupling_node_s_exchange():
    from neofoam_ui._ui._model import _RunModel  # noqa: PLC0415
    from neofoam_ui._ui.panes._inspector import _inspector_model  # noqa: PLC0415

    ops = [_coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])]
    assign_ids(ops)
    model = _RunModel()
    model.graph({"activity": "exec", "ops": [op_dict(o) for o in ops]})
    model.reindex("probe")

    rows = {r["k"]: r["v"] for r in _inspector_model(model, "loop/couple_a.x", [])["rows"]}
    assert rows["exchange"] == "x ← b (b.solve), for the op below"
