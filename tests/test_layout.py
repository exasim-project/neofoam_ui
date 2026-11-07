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


def test_a_coupling_loop_draws_a_box_per_region_connected_through_its_coupling_nodes():
    flow = _flow([_coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])])
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert [nodes[f"loop::box{i}"]["data"]["label"] for i in (0, 1)] == ["a", "b"]
    assert nodes["loop::box0"]["type"] == "region"
    a, b = nodes["loop/a.solve"]["position"], nodes["loop/b.solve"]["position"]
    assert a["y"] == b["y"] and a["x"] < b["x"]  # side by side
    assert nodes["loop/couple_a.x"]["data"]["exchange"] == "x ← b"
    pairs = {(e["source"], e["target"]): e for e in flow["edges"]}
    # into a coupling node: from the partner's op; out of it: to its own op
    exchange = {k for k, e in pairs.items() if e.get("class") == "nf-couple"}
    assert exchange == {("loop/b.solve", "loop/couple_a.x"), ("loop/a.solve", "loop/couple_b.x")}
    assert pairs[("loop/a.solve", "loop/couple_b.x")]["sourceHandle"] == "r"
    assert pairs[("loop/b.solve", "loop/couple_a.x")]["targetHandle"] == "rt"
    assert pairs[("loop/b.solve", "loop/couple_a.x")]["label"] == "x"
    assert ("loop/couple_a.x", "loop/a.solve") in pairs and ("loop/couple_b.x", "loop/b.solve") in pairs
    # the boxes are connected only through the coupling nodes
    region = {f"loop/{name}": name[-3] if name.startswith("couple") else name[0]
              for name in ("couple_a.x", "a.solve", "couple_b.x", "b.solve")}
    assert not [k for k, e in pairs.items() if e.get("class") != "nf-couple"
                and k[0] in region and k[1] in region and region[k[0]] != region[k[1]]]
    # the loop's test leads into the first box, the last box back to it
    assert ("loop::test", "loop/couple_a.x") in pairs and ("loop/b.solve", "loop::test") in pairs


def test_a_loop_without_coupling_nodes_has_no_region_boxes():
    from neofoam_ui.engine import Op  # noqa: PLC0415

    flow = _flow([Op("loop", "loop", lambda ctx: False, [
        Op("a.solve", "action", lambda ctx: None, region="a"),
        Op("b.solve", "action", lambda ctx: None, region="b")])])

    assert not [n for n in flow["nodes"] if n["type"] == "region"]
    assert not [e for e in flow["edges"] if e.get("class") == "nf-couple"]


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
    exchange = {(e["source"], e["target"]) for e in flow["edges"] if e.get("class") == "nf-couple"}
    # the outer loop's own coupling nodes take the partner's op of the outer loop (a loop for c)
    assert exchange == {("outer/c.solve", "outer/couple_b.x"), ("outer/inner", "outer/couple_c.x"),
                        ("outer/inner/b.solve", "outer/inner/couple_a.x"),
                        ("outer/inner/a.solve", "outer/inner/couple_b.x")}


def test_the_labels_of_two_crossing_exchange_edges_do_not_overlap():
    flow = _flow([_coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])])

    labels = [e["labelStyle"]["transform"] for e in flow["edges"] if e.get("class") == "nf-couple"]
    assert len(labels) == 2 and labels[0] != labels[1]


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
