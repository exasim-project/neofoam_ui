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


def test_one_view_at_a_time_and_a_placeholder_before_initialization():
    steps, ops = _graph()
    assert {n["id"] for n in build_flow(steps, None)["nodes"]} == {"exec:placeholder"}
    init = {n["id"] for n in build_flow(steps, ops, view="init")["nodes"]}
    assert "init:mesh" in init and not any(i.startswith("time_loop") for i in init)
    run = {n["id"] for n in build_flow(steps, ops)["nodes"]}
    assert "time_loop" in run and not any(i.startswith("init:") for i in run)
    # no frames: no activity nodes in either view
    assert not [n for v in ("init", "exec") for n in build_flow(steps, ops, view=v)["nodes"]
                if n["type"] == "activity"]


def test_a_loop_is_a_control_node_with_decision_and_back_edge():
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
    full_end = {n["id"]: n for n in full["nodes"]}["end"]["position"]["y"]
    assert nodes["end"]["position"]["y"] < full_end


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

    assert nodes["loop/a.solve@@a"]["data"]["color"] == _REGION.color("a")
    assert nodes["loop/a.solve@@a"]["data"]["color"] != nodes["loop/b.solve@@b"]["data"]["color"]
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


def _pair():
    from neofoam_ui.engine import Op  # noqa: PLC0415

    loop = _coupled("couple", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])
    return [Op("time", "loop", lambda ctx: False, [Op("set_time_step", "action", lambda ctx: None), loop])]


def test_each_region_box_is_the_full_solver_with_copies_of_the_shared_loops():
    flow = _flow(_pair())
    nodes = {n["id"]: n for n in flow["nodes"]}

    for region in "ab":
        box = nodes[f"exec::box:{region}"]
        assert box["type"] == "region" and box["data"]["label"] == region
        own = [f"start@@{region}", f"time@@{region}", f"time/set_time_step@@{region}",
               f"time/couple@@{region}", f"time/couple/couple_{region}.x@@{region}",
               f"time/couple/{region}.solve@@{region}", f"end@@{region}"]
        for i in own:
            n = nodes[i]
            assert box["position"]["x"] <= n["position"]["x"] and \
                n["position"]["x"] + n["width"] <= box["position"]["x"] + box["width"], i
            assert box["position"]["y"] <= n["position"]["y"] <= box["position"]["y"] + box["height"], i
        # a copy binds to the shared op: token, selection, breakpoints, pass
        assert nodes[f"time/couple@@{region}"]["data"]["ref"] == "time/couple"
        assert nodes[f"time/couple@@{region}::test"]["data"]["loop"] == "time/couple"
        assert nodes[f"time/set_time_step@@{region}"]["data"]["number"] == "1.1"
    # nothing of the other region in a box
    assert not [i for i in nodes if i.endswith("@@a") and ("b.solve" in i or "couple_b" in i)]
    # between the boxes only the line between the coupling nodes, both ways, labelled
    couple = [e for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    assert [(e["source"], e["target"], e["class"]) for e in couple] == [
        ("time/couple/couple_a.x@@a", "time/couple/couple_b.x@@b", "nf-couple cl-0")]
    assert couple[0]["markerStart"] and couple[0]["markerEnd"]
    # its data label sits between the two coupling nodes, keyed for the values and the highlight
    label = nodes["exlabel:0"]
    assert label["data"] == {"key": "0", "data": "x", "left": "a", "right": "b",
                             "nodes": ["time/couple/couple_a.x", "time/couple/couple_b.x"]}
    a_right = nodes["time/couple/couple_a.x@@a"]["position"]["x"] + nodes["time/couple/couple_a.x@@a"]["width"]
    assert a_right < label["position"]["x"] < nodes["time/couple/couple_b.x@@b"]["position"]["x"]
    a_box = {i for i in nodes if i.endswith("@@a")}  # (the label node belongs to no box)
    b_box = {i for i in nodes if i.endswith("@@b")}
    crossing = [e for e in flow["edges"] if (e["source"].split("::")[0] in a_box)
                != (e["target"].split("::")[0] in a_box) and "nf-couple" not in e.get("class", "")]
    assert not crossing and a_box and b_box
    # the coupling nodes face each other
    assert nodes["time/couple/couple_a.x@@a"]["position"]["y"] == nodes["time/couple/couple_b.x@@b"]["position"]["y"]


def test_a_solver_without_regions_has_no_boxes_and_no_copies():
    from neofoam_ui.engine import Op  # noqa: PLC0415

    flow = _flow([Op("loop", "loop", lambda ctx: False, [Op("solve", "action", lambda ctx: None)])])

    assert not [n for n in flow["nodes"] if n["type"] == "region"]
    assert {n["id"] for n in flow["nodes"]} == {"start", "loop", "loop::test", "loop/solve", "end"}


def test_a_nested_coupling_loop_is_projected_into_each_region_s_box():
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

    # each box shows the loops its region runs in: a re-solves inside every outer pass too
    assert {"outer@@a", "outer/inner@@a", "outer@@b", "outer/inner@@b", "outer@@c"} <= set(nodes)
    assert "outer/inner@@c" not in nodes
    # the boxes along the chain, b in the middle
    xs = [nodes[f"exec::box:{r}"]["position"]["x"] for r in "abc"]
    assert xs == sorted(xs)
    # a shared loop's copies on one row; each line joins neighbouring boxes, horizontally, in the gap
    assert nodes["outer/inner@@a"]["position"]["y"] == nodes["outer/inner@@b"]["position"]["y"]
    couple = [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    assert set(couple) == {("outer/inner/couple_a.x@@a", "outer/inner/couple_b.x@@b"),
                           ("outer/couple_b.x@@b", "outer/couple_c.x@@c")}
    for s, tgt in couple:
        left, right = s[-1], tgt[-1]
        assert nodes[s]["position"]["y"] == nodes[tgt]["position"]["y"]
        box_l, box_r = nodes[f"exec::box:{left}"], nodes[f"exec::box:{right}"]
        assert box_l["position"]["x"] < nodes[s]["position"]["x"] + nodes[s]["width"] \
            <= box_l["position"]["x"] + box_l["width"] < box_r["position"]["x"] <= nodes[tgt]["position"]["x"]
    # b holds both of its coupling nodes, one per exchange
    assert {"outer/couple_b.x@@b", "outer/inner/couple_b.x@@b"} <= set(nodes)


def test_a_collapsed_loop_hides_the_exchange_into_it():
    flow = _flow([_coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])],
                 collapsed=frozenset({"loop"}))
    assert {n["id"] for n in flow["nodes"]} >= {"loop@@a", "loop@@b"}

    assert not [e for e in flow["edges"] if "nf-couple" in e.get("class", "")]


def test_the_inspector_names_a_coupling_node_s_exchange():
    from neofoam_ui._ui._model import _RunModel  # noqa: PLC0415
    from neofoam_ui._ui.panes._inspector import _inspector_model  # noqa: PLC0415

    ops = [_coupled("loop", ["a", "b"], [("b.solve", "b"), ("a.solve", "a")])]
    assign_ids(ops)
    model = _RunModel()
    model.graph({"activity": "exec", "ops": [op_dict(o) for o in ops]})
    model.reindex("probe")

    rows = {r["k"]: r["v"] for r in _inspector_model(model, "loop/couple_a.x", [])["rows"]}  # by the shared id
    assert rows["exchange"] == "x ← b (b.solve), for the op below"


def test_the_init_view_puts_each_region_s_steps_in_its_box_below_the_driver_s():
    from neofoam_ui.engine import InitStep  # noqa: PLC0415

    steps = [InitStep("time", lambda r: None), InitStep("a.mesh", lambda r: None, ["time"], region="a"),
             InitStep("b.mesh", lambda r: None, ["time"], region="b"),
             InitStep("b.T", lambda r: None, ["b.mesh"], region="b")]
    loop = _coupled("loop", ["b", "a"], [("a.solve", "a"), ("b.solve", "b")])
    assign_ids([loop])
    flow = build_flow([init_dict(s) for s in steps], [op_dict(loop)], view="init")
    nodes = {n["id"]: n for n in flow["nodes"]}

    # the regions in the order of the execution view (b, then a)
    bx, ax = nodes["init::box:b"]["position"]["x"], nodes["init::box:a"]["position"]["x"]
    assert bx < ax
    for step, region in (("init:a.mesh", "a"), ("init:b.mesh", "b"), ("init:b.T", "b")):
        box, n = nodes[f"init::box:{region}"], nodes[step]
        assert box["position"]["x"] < n["position"]["x"] < box["position"]["x"] + box["width"]
        assert box["position"]["y"] < n["position"]["y"] < box["position"]["y"] + box["height"]
    assert nodes["init:time"]["position"]["y"] < nodes["init::box:a"]["position"]["y"]
    edges = {(e["source"], e["target"]) for e in flow["edges"]}
    assert edges == {("init:time", "init:a.mesh"), ("init:time", "init:b.mesh"), ("init:b.mesh", "init:b.T")}


def test_a_plain_solver_s_init_view_has_no_region_boxes():
    steps, ops = _graph()
    flow = build_flow(steps, ops, view="init")
    assert not [n for n in flow["nodes"] if n["type"] == "region"]


def test_the_dag_view_follows_the_run_s_phase_until_the_user_switches():
    from types import SimpleNamespace  # noqa: PLC0415

    from neofoam_ui._ui.panes._dag import _DagPane  # noqa: PLC0415

    rebuilt = []
    pane = SimpleNamespace(_phase=None, app=SimpleNamespace(state=SimpleNamespace(dag_view="init")),
                           rebuild=lambda: rebuilt.append(pane.app.state.dag_view))
    follow = lambda phase: _DagPane._follow_phase(pane, phase)  # noqa: E731
    follow("init")
    assert pane.app.state.dag_view == "init" and rebuilt == []
    follow("exec")
    assert pane.app.state.dag_view == "exec" and rebuilt == ["exec"]
    pane.app.state.dag_view = "init"  # the user looks at the init view while it runs
    follow("exec")
    assert pane.app.state.dag_view == "init"  # holds: no change of phase
    follow("init")  # a restart: the next change of phase switches again
    assert pane.app.state.dag_view == "init"
    follow("exec")
    assert pane.app.state.dag_view == "exec"


def test_the_init_view_draws_known_regions_before_they_initialize():
    from neofoam_ui.engine import InitStep  # noqa: PLC0415

    steps = [InitStep("time", lambda r: None), InitStep("a.mesh", lambda r: None, ["time"], region="a")]
    flow = build_flow([init_dict(s) for s in steps], None, view="init", regions=("a", "b"))
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert nodes["init::box:a"]["data"]["label"] == "a"
    assert nodes["init::box:b"]["data"]["label"] == "b · not initialized yet"
    assert nodes["init::box:a"]["position"]["x"] < nodes["init::box:b"]["position"]["x"]


def test_the_dag_pane_shows_what_a_coupling_node_hands_over_and_heats_its_line():
    from types import SimpleNamespace  # noqa: PLC0415

    from neofoam_ui._ui.panes._dag import _DagPane  # noqa: PLC0415

    state = SimpleNamespace(ex_vals={}, ex_hot=[])
    pane = SimpleNamespace(app=SimpleNamespace(state=state), _line_of={"c/couple_b.x": "0", "c/couple_a.x": "0"})
    exchanged = lambda msg: _DagPane._exchanged(pane, msg)  # noqa: E731
    exchanged({"node": "c/couple_b.x", "exchange": {"from": "a", "to": "b", "data": {"x": "1.5"}}})
    assert state.ex_hot == ["0"] and state.ex_vals == {"0": {"a": "x = 1.5"}}
    exchanged({"node": "c/couple_a.x", "exchange": {"from": "b", "to": "a", "data": {"x": "0.75"}}})
    assert state.ex_vals == {"0": {"a": "x = 1.5", "b": "x = 0.75"}}
    exchanged({"node": "c/a.solve"})
    assert state.ex_hot == []


def test_the_pair_view_keeps_its_shape():
    """Guard: pair stays two boxes a | b, the coupling nodes face each other on one row."""
    flow = _flow(_pair())
    nodes = {n["id"]: n for n in flow["nodes"]}
    boxes = sorted((n["position"]["x"], n["id"]) for n in flow["nodes"] if n["type"] == "region")
    assert [b for _, b in boxes] == ["exec::box:a", "exec::box:b"]
    a, b = nodes["time/couple/couple_a.x@@a"], nodes["time/couple/couple_b.x@@b"]
    assert a["position"]["y"] == b["position"]["y"]
    assert nodes["time/couple/a.solve@@a"]["position"]["y"] == nodes["time/couple/b.solve@@b"]["position"]["y"]
    assert [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")] == [
        ("time/couple/couple_a.x@@a", "time/couple/couple_b.x@@b")]
