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
    # between the boxes only the line between the coupling nodes, through the interface's
    # mapping node, arrowheads at the coupling nodes
    couple = [e for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    assert [(e["source"], e["target"], e["class"]) for e in couple] == [
        ("time/couple/couple_a.x@@a", "exmap:0", "nf-couple cl-0"),
        ("exmap:0", "time/couple/couple_b.x@@b", "nf-couple cl-0")]
    assert couple[0]["markerStart"] and "markerEnd" not in couple[0] and couple[1]["markerEnd"]
    # the mapping node sits between the two coupling nodes, outside both boxes, keyed for the
    # values and the highlight
    label = nodes["exmap:0"]
    assert {k: label["data"][k] for k in ("key", "data", "left", "right", "nodes")} == {
        "key": "0", "data": "x", "left": "a", "right": "b",
        "nodes": ["time/couple/couple_a.x", "time/couple/couple_b.x"]}
    a_right = nodes["exec::box:a"]["position"]["x"] + nodes["exec::box:a"]["width"]
    assert a_right < label["position"]["x"] < label["position"]["x"] + label["width"] < \
        nodes["exec::box:b"]["position"]["x"]
    couple_a = nodes["time/couple/couple_a.x@@a"]
    assert label["position"]["y"] + label["height"] / 2 == couple_a["position"]["y"] + couple_a["height"] / 2
    # a chip on the line, no taller than the coupling node (the details show on hover)
    assert label["height"] <= couple_a["height"]
    a_box = {i for i in nodes if i.endswith("@@a")}  # (the mapping node belongs to no box)
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
    halves = [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    couple = [(s, t2) for s, m in halves if m.startswith("exmap:") for m2, t2 in halves if m2 == m]
    assert set(couple) == {("outer/inner/couple_a.x@@a", "outer/inner/couple_b.x@@b"),
                           ("outer/couple_b.x@@b", "outer/couple_c.x@@c")}
    # one mapping node per interface, each in its own gap, on its line's row
    maps = {n["data"]["left"] + n["data"]["right"]: n for n in flow["nodes"] if n["type"] == "exmap"}
    assert set(maps) == {"ab", "bc"}
    for s, tgt in couple:
        m = maps[s[-1] + tgt[-1]]
        assert nodes[s]["position"]["x"] + nodes[s]["width"] < m["position"]["x"]
        assert m["position"]["x"] + m["width"] < nodes[tgt]["position"]["x"]
        assert m["position"]["y"] + m["height"] / 2 == nodes[s]["position"]["y"] + nodes[s]["height"] / 2
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
    edges = {(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" not in e.get("class", "")}
    assert edges == {("init:time", "init:a.mesh"), ("init:time", "init:b.mesh"), ("init:b.mesh", "init:b.T")}
    # the interface: its mapping node between the two boxes, the coupling line through it
    m = nodes["exmap:0"]
    assert bx + nodes["init::box:b"]["width"] < m["position"]["x"] < m["position"]["x"] + m["width"] < ax
    assert [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")] == [
        ("init::box:b", "exmap:0"), ("exmap:0", "init::box:a")]


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
        ("time/couple/couple_a.x@@a", "exmap:0"), ("exmap:0", "time/couple/couple_b.x@@b")]


def test_a_closed_region_box_keeps_only_its_coupling_nodes_on_their_rows():
    assign_ids(ops := _pair())
    full = {n["id"]: n for n in build_flow([], [op_dict(o) for o in ops])["nodes"]}
    flow = build_flow([], [op_dict(o) for o in ops], closed=frozenset({"b"}))
    nodes = {n["id"]: n for n in flow["nodes"]}

    b = [i for i in nodes if i.endswith("@@b")]
    assert b == ["time/couple/couple_b.x@@b"]
    assert nodes["exec::box:b"]["data"]["closed"] and nodes["exec::box:b"]["width"] < full["exec::box:b"]["width"]
    # still facing its partner, still joined to it
    assert nodes["time/couple/couple_b.x@@b"]["position"]["y"] == nodes["time/couple/couple_a.x@@a"]["position"]["y"]
    assert [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")] == [
        ("time/couple/couple_a.x@@a", "exmap:0"), ("exmap:0", "time/couple/couple_b.x@@b")]
    # its mapping node between the narrow box and its partner's
    m = nodes["exmap:0"]
    assert nodes["exec::box:a"]["position"]["x"] + nodes["exec::box:a"]["width"] < m["position"]["x"]
    assert m["position"]["x"] + m["width"] < nodes["exec::box:b"]["position"]["x"]


def test_a_box_holding_the_token_or_a_breakpoint_opens():
    from types import SimpleNamespace  # noqa: PLC0415

    from neofoam_ui._ui.panes._dag import _DagPane  # noqa: PLC0415

    index = {"c/b.solve": {"region": "b"}, "c/a.solve": {"region": "a"}, "c": {}}
    state = SimpleNamespace(boxes_closed=["a", "b"], cur_node="c", bps=[])
    pane = SimpleNamespace(app=SimpleNamespace(state=state), model=SimpleNamespace(index=index))
    shut = lambda: _DagPane._boxes_shut(pane)  # noqa: E731
    assert shut() == {"a", "b"}  # a shared loop holds the token: no box opens
    state.cur_node = "c/b.solve"
    assert shut() == {"a"}
    state.bps = ["c/a.solve"]
    assert shut() == set()


def test_field_rows_are_grouped_per_region_in_the_boxes_order():
    from neofoam_ui._ui._app import _by_region  # noqa: PLC0415
    from neofoam_ui._ui._layout._palette import _REGION  # noqa: PLC0415

    rows = [{"name": n} for n in ("c.x", "a.x", "time", "b.x", "a.T")]
    out = _by_region(rows, ["a", "b", "c"])
    assert [r["name"] for r in out] == ["time", "a.x", "a.T", "b.x", "c.x"]
    assert out[1]["region"] == "a" and out[1]["color"] == _REGION.color("a") and out[0]["color"] is None


def test_a_region_fields_table_is_drawn_in_the_region_s_colour():
    from neofoam_ui._ui._layout._palette import _REGION  # noqa: PLC0415
    from neofoam_ui._ui.panes._table_history import _TableHistory  # noqa: PLC0415

    history = _TableHistory()
    history.add({"name": "fields · b", "time": 1.0, "columns": ["x"], "values": [[0.5]], "region": "b",
                 "x": "coupling pass"})
    (chart,) = history.charts()
    assert chart["data"][0]["line"]["color"] == _REGION.color("b")
    assert chart["layout"]["xaxis"]["title"]["text"] == "coupling pass"


def _mapped_pair(left, right):
    """The pair, its coupling nodes telling how they map (``left``/``right``: each node's PointMapping)."""
    from neofoam_ui.engine import Exchange, Op  # noqa: PLC0415

    body = [Op("couple_a.q", "action", lambda ctx: None, region="a",
               exchange=Exchange("b.solve", ("T", "h"), "b", left)),
            Op("a.solve", "action", lambda ctx: None, region="a"),
            Op("couple_b.q", "action", lambda ctx: None, region="b",
               exchange=Exchange("a.solve", ("T", "h"), "a", right)),
            Op("b.solve", "action", lambda ctx: None, region="b")]
    return [Op("loop", "loop", lambda ctx: False, body)]


def test_the_mapping_node_shows_kind_points_and_mapped_fields_each_way():
    from neofoam_ui.engine import PointMapping  # noqa: PLC0415

    # a has 4 interface points, b 3; T is mapped point by point, h handed over as is
    ops = _mapped_pair(PointMapping("nearest-neighbor", ("T",), 3, 4), PointMapping("nearest-neighbor", ("T",), 4, 3))
    data = {n["id"]: n for n in _flow(ops)["nodes"]}["exmap:0"]["data"]
    assert data["kind"] == "nearest-neighbor"
    assert data["points"] == {"a": 4, "b": 3}
    assert data["ways"] == [
        {"from": "a", "to": "b", "fields": [{"name": "T", "mapped": True}, {"name": "h", "mapped": False}]},
        {"from": "b", "to": "a", "fields": [{"name": "T", "mapped": True}, {"name": "h", "mapped": False}]}]


def test_the_mapping_node_marks_what_the_solver_does_not_tell():
    from neofoam_ui.engine import PointMapping  # noqa: PLC0415

    # one side tells nothing (no mapping), the other its mapped fields but no points
    data = {n["id"]: n for n in _flow(_mapped_pair(None, PointMapping(mapped=("T", "h"))))["nodes"]}["exmap:0"]["data"]
    assert data["kind"] is None and data["points"] == {"a": None, "b": None}
    assert data["ways"][0]["fields"] == [{"name": "T", "mapped": True}, {"name": "h", "mapped": True}]
    assert data["ways"][1]["fields"] == [{"name": "T", "mapped": None}, {"name": "h", "mapped": None}]
    # each side's count comes from either node: a's own (target) or b's partner's (source)
    data = {n["id"]: n for n in _flow(_mapped_pair(PointMapping(target_points=5), PointMapping()))["nodes"]}[
        "exmap:0"]["data"]
    assert data["points"] == {"a": 5, "b": None}


def test_the_wire_carries_a_coupling_node_s_mapping_only_when_known():
    from neofoam_ui.engine import Exchange, Op, PointMapping  # noqa: PLC0415

    plain = op_dict(Op("couple_a.x", "action", lambda ctx: None, exchange=Exchange("b.solve", ("x",), "b")))
    assert "mapping" not in plain["exchange"]
    told = op_dict(Op("couple_a.x", "action", lambda ctx: None,
                      exchange=Exchange("b.solve", ("x",), "b", PointMapping("identity", ("x",), None, 2))))
    assert told["exchange"]["mapping"] == {"kind": "identity", "mapped": ["x"], "source_points": None,
                                           "target_points": 2}


def test_the_inspector_shows_a_coupling_node_s_mapping():
    from neofoam_ui._ui._model import _RunModel  # noqa: PLC0415
    from neofoam_ui._ui.panes._inspector import _inspector_model  # noqa: PLC0415
    from neofoam_ui.engine import PointMapping  # noqa: PLC0415

    ops = _mapped_pair(PointMapping("nearest-neighbor", ("T",), 3, 4), None)
    assign_ids(ops)
    model = _RunModel()
    model.graph({"activity": "exec", "ops": [op_dict(o) for o in ops]})
    model.reindex("probe")
    rows = {r["k"]: r["v"] for r in _inspector_model(model, "loop/couple_a.q", [])["rows"]}
    assert rows["mapping"] == "nearest-neighbor, 3 → 4 points; mapped T, as is h"
    rows = {r["k"]: r["v"] for r in _inspector_model(model, "loop/couple_b.q", [])["rows"]}
    assert "mapping" not in rows


def _pair_with_mapping_ops():
    """The pair as NeoFOAM steps it: before each coupling node the mapping op that feeds it."""
    from neofoam_ui.engine import Exchange, Op  # noqa: PLC0415

    body = []
    for r, partner in (("a", "b"), ("b", "a")):
        body += [Op(f"map_a-b_to_{r}", "action", lambda ctx: None, region=r, maps=f"{r}.couple"),
                 Op(f"{r}.couple", "action", lambda ctx: None, region=r,
                    exchange=Exchange(f"{partner}.solve", ("x",), partner)),
                 Op(f"{r}.solve", "action", lambda ctx: None, region=r)]
    return [Op("loop", "loop", lambda ctx: False, body)]


def test_a_mapping_op_is_its_own_chip_on_the_line_next_to_the_coupling_node_it_feeds():
    flow = _flow(_pair_with_mapping_ops())
    nodes = {n["id"]: n for n in flow["nodes"]}

    # not in a box (no copy), and the box rows hold no gap for it
    assert not [i for i in nodes if i.startswith("loop/map_") and "@@" in i]
    for r in "ab":
        couple, solve = nodes[f"loop/{r}.couple@@{r}"], nodes[f"loop/{r}.solve@@{r}"]
        assert couple["position"]["y"] < solve["position"]["y"]
    # one chip per way, the mapping op's own node (token, breakpoint, selection bind to it)
    into_a, into_b = nodes["loop/map_a-b_to_a"], nodes["loop/map_a-b_to_b"]
    assert (into_a["type"], into_a["data"]["ref"], into_a["data"]["into"]) == ("exmap", "loop/map_a-b_to_a", "left")
    assert (into_b["data"]["ref"], into_b["data"]["into"], into_b["data"]["number"]) == (
        "loop/map_a-b_to_b", "right", "1.4")
    assert into_a["data"]["key"] == into_b["data"]["key"] == "0"
    # in the gap, each next to the coupling node it feeds: a ◂ [→ a] ── [→ b] ▸ b
    a_right = nodes["exec::box:a"]["position"]["x"] + nodes["exec::box:a"]["width"]
    b_left = nodes["exec::box:b"]["position"]["x"]
    assert a_right < into_a["position"]["x"] < into_a["position"]["x"] + into_a["width"] \
        < into_b["position"]["x"] < into_b["position"]["x"] + into_b["width"] < b_left
    # one line through both chips, arrowheads at the coupling nodes only
    couple = [e for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    assert [(e["source"], e["target"]) for e in couple] == [
        ("loop/a.couple@@a", "loop/map_a-b_to_a"), ("loop/map_a-b_to_a", "loop/map_a-b_to_b"),
        ("loop/map_a-b_to_b", "loop/b.couple@@b")]
    assert couple[0]["markerStart"] and "markerEnd" not in couple[0]
    assert "markerStart" not in couple[1] and "markerEnd" not in couple[1] and couple[2]["markerEnd"]
