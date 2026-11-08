from types import SimpleNamespace

from trame.app import get_server

from neofoam_ui._ui._app import _by_region
from neofoam_ui._ui._layout._dag import _build_flow as build_flow
from neofoam_ui._ui._layout._palette import _REGION
from neofoam_ui._ui._model import _RunModel
from neofoam_ui._ui.panes._dag import _DagPane
from neofoam_ui._ui.panes._inspector import _inspector_model
from neofoam_ui._ui.panes._table_history import _TableHistory
from neofoam_ui.app import StepperApp
from neofoam_ui.engine import (
    Exchange,
    InitStep,
    Op,
    PointMapping,
    Session,
    assign_ids,
    topological_order,
)
from neofoam_ui.engine._wire import _init_dict as init_dict
from neofoam_ui.engine._wire import _op_dict as op_dict
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


def _region(region, *, extra=(), exchanges=(), mapping=None, leader=False):
    """One region's own graph, as NeoFOAM builds it and the session ids it: ``time_loop {
    set_time_step, [extra ...,] <region>.couple { solve } }`` under ``<region>/``.
    ``exchanges``: the coupling op's ``[(name, partner region), ...]``."""
    def op(name, kind="action", children=(), **kw):
        return Op(name, kind, lambda ctx: False, list(children), region=region, **kw)

    maps = mapping if isinstance(mapping, list) else [mapping] * len(exchanges)
    couple = op(f"{region}.couple", "loop", [op("solve")], test="exchange · serial",
                tag="leader" if leader else None,
                exchanges=[Exchange(f"{p}.to_{region}", ("T", "h") if m is not None else ("x",), p, m, n)
                           for (n, p), m in zip(exchanges, maps)])
    ops = [op("time_loop", "loop", [op("set_time_step"), *(op(e) for e in extra), couple])]
    assign_ids(ops, f"{region}/")
    return ops


def _flow_of(*graphs, **kw):
    return build_flow([], [op_dict(o) for g in graphs for o in g], **kw)


def _pair(left=None, right=None):
    return (_region("a", exchanges=[("a-b", "b")], mapping=left),
            _region("b", exchanges=[("a-b", "a")], mapping=right, leader=True))


def _model(*graphs):
    model = _RunModel()
    model.graph({"activity": "exec", "ops": [op_dict(o) for g in graphs for o in g]})
    model.reindex("probe")
    return model


def test_region_ops_take_their_region_colour_and_make_the_legend():
    model = _model(*_pair())
    nodes = {n["id"]: n for n in build_flow([], model.ops)["nodes"]}

    assert nodes["a/time_loop/a.couple/solve"]["data"]["color"] == _REGION.color("a")
    assert nodes["a/time_loop/a.couple/solve"]["data"]["color"] != nodes["b/time_loop/b.couple/solve"]["data"]["color"]
    assert [r["name"] for r in model.regions()] == ["a", "b"]


def test_each_region_box_is_its_own_graph():
    flow = _flow_of(*_pair())
    nodes = {n["id"]: n for n in flow["nodes"]}

    for region in "ab":
        box = nodes[f"exec::box:{region}"]
        assert box["type"] == "region" and box["data"]["label"] == region and box["data"]["region"] == region
        own = [i for i in nodes if i.startswith(f"{region}/") and "::" not in i]
        own += [f"exec::start:{region}", f"exec::end:{region}"]
        assert len(own) == 6
        for i in own:
            n = nodes[i]
            assert box["position"]["x"] <= n["position"]["x"] and \
                n["position"]["x"] + n["width"] <= box["position"]["x"] + box["width"], i
            assert box["position"]["y"] <= n["position"]["y"] <= box["position"]["y"] + box["height"], i
        # a node binds to its own op: no copies of shared ops any more
        assert nodes[f"{region}/time_loop/{region}.couple"]["data"]["ref"] == f"{region}/time_loop/{region}.couple"
        assert nodes[f"{region}/time_loop/set_time_step"]["data"]["number"] == "1.1"
        assert nodes[f"exec::end:{region}"]["data"]["region"] == region
    assert not [i for i in nodes if "@@" in i]
    # between the boxes only the line between the coupling ops, through the exchange's
    # mapping node, arrowheads at the coupling ops
    couple = [e for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    assert [(e["source"], e["target"], e["class"]) for e in couple] == [
        ("a/time_loop/a.couple", "exmap:0", "nf-couple cl-0"),
        ("exmap:0", "b/time_loop/b.couple", "nf-couple cl-0")]
    assert couple[0]["markerStart"] and "markerEnd" not in couple[0] and couple[1]["markerEnd"]
    label = nodes["exmap:0"]
    assert {k: label["data"][k] for k in ("key", "data", "left", "right", "nodes", "name")} == {
        "key": "0", "data": "x", "left": "a", "right": "b", "name": "a-b",
        "nodes": ["a/time_loop/a.couple", "b/time_loop/b.couple"]}
    a_right = nodes["exec::box:a"]["position"]["x"] + nodes["exec::box:a"]["width"]
    assert a_right < label["position"]["x"] < label["position"]["x"] + label["width"] < \
        nodes["exec::box:b"]["position"]["x"]
    couple_a = nodes["a/time_loop/a.couple"]
    assert label["position"]["y"] + label["height"] / 2 == couple_a["position"]["y"] + couple_a["height"] / 2
    assert label["height"] <= couple_a["height"]
    a_box = {i for i in nodes if i.startswith("a/") or i.endswith(":a")}
    crossing = [e for e in flow["edges"] if (e["source"].removesuffix("::test") in a_box)
                != (e["target"].removesuffix("::test") in a_box) and "nf-couple" not in e.get("class", "")]
    assert not crossing and a_box
    assert nodes["b/time_loop/b.couple"]["data"]["tag"] == "leader"


def test_coupling_ops_face_each_other_when_the_regions_differ_before_them():
    # b has two ops more before its coupling op: a's coupling op moves down to b's row
    flow = _flow_of(_region("a", exchanges=[("a-b", "b")]),
                    _region("b", exchanges=[("a-b", "a")], extra=("agree_dt", "prep")))
    nodes = {n["id"]: n for n in flow["nodes"]}
    assert nodes["a/time_loop/a.couple"]["position"]["y"] == nodes["b/time_loop/b.couple"]["position"]["y"]
    assert nodes["a/time_loop/a.couple/solve"]["position"]["y"] == nodes["b/time_loop/b.couple/solve"]["position"]["y"]
    assert nodes["a/time_loop/set_time_step"]["position"]["y"] == nodes["b/time_loop/set_time_step"]["position"]["y"]


def test_a_solver_without_regions_has_no_boxes():
    ops = [Op("loop", "loop", lambda ctx: False, [Op("solve", "action", lambda ctx: None)])]
    assign_ids(ops)
    flow = build_flow([], [op_dict(o) for o in ops])

    assert not [n for n in flow["nodes"] if n["type"] == "region"]
    assert {n["id"] for n in flow["nodes"]} == {"start", "loop", "loop::test", "loop/solve", "end"}


def test_a_chain_s_boxes_follow_the_exchanges_and_b_sits_on_both_lines():
    # reported in another order (b, a, c); the chain is a - b - c
    flow = _flow_of(_region("b", exchanges=[("a-b", "a"), ("b-c", "c")]), _region("a", exchanges=[("a-b", "b")]),
                    _region("c", exchanges=[("b-c", "b")], leader=True))
    nodes = {n["id"]: n for n in flow["nodes"]}

    xs = [nodes[f"exec::box:{r}"]["position"]["x"] for r in "abc"]
    assert xs == sorted(xs)
    assert len({nodes[f"{r}/time_loop/{r}.couple"]["position"]["y"] for r in "abc"}) == 1
    couple = [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")]
    assert couple == [("a/time_loop/a.couple", "exmap:0"), ("exmap:0", "b/time_loop/b.couple"),
                      ("b/time_loop/b.couple", "exmap:1"), ("exmap:1", "c/time_loop/c.couple")]
    assert [nodes[f"exmap:{k}"]["data"]["name"] for k in "01"] == ["a-b", "b-c"]
    assert nodes["b/time_loop/b.couple"]["data"]["exchange"] == "x ← a · x ← c"
    for key, (left, right) in (("0", "ab"), ("1", "bc")):
        m = nodes[f"exmap:{key}"]
        box_l, box_r = nodes[f"exec::box:{left}"], nodes[f"exec::box:{right}"]
        assert box_l["position"]["x"] + box_l["width"] < m["position"]["x"]
        assert m["position"]["x"] + m["width"] < box_r["position"]["x"]


def test_two_exchanges_between_the_same_regions_pair_by_name():
    flow = _flow_of(_region("a", exchanges=[("p", "b"), ("q", "b")]), _region("b", exchanges=[("q", "a"), ("p", "a")]))
    maps = sorted(n["data"]["name"] for n in flow["nodes"] if n["type"] == "exmap")
    assert maps == ["p", "q"]


def test_a_collapsed_loop_hides_the_exchange_into_it():
    flow = _flow_of(*_pair(), collapsed=frozenset({"a/time_loop", "b/time_loop"}))
    assert {n["id"] for n in flow["nodes"]} >= {"a/time_loop", "b/time_loop"}
    assert not [e for e in flow["edges"] if "nf-couple" in e.get("class", "")]


def test_a_closed_region_box_keeps_only_its_coupling_ops_on_their_rows():
    full = {n["id"]: n for n in _flow_of(*_pair())["nodes"]}
    flow = _flow_of(*_pair(), closed=frozenset({"b"}))
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert [i for i in nodes if i.startswith("b/") and "::" not in i] == ["b/time_loop/b.couple"]
    assert nodes["exec::box:b"]["data"]["closed"] and nodes["exec::box:b"]["width"] < full["exec::box:b"]["width"]
    assert nodes["b/time_loop/b.couple"]["position"]["y"] == nodes["a/time_loop/a.couple"]["position"]["y"]
    assert [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")] == [
        ("a/time_loop/a.couple", "exmap:0"), ("exmap:0", "b/time_loop/b.couple")]
    m = nodes["exmap:0"]
    assert nodes["exec::box:a"]["position"]["x"] + nodes["exec::box:a"]["width"] < m["position"]["x"]
    assert m["position"]["x"] + m["width"] < nodes["exec::box:b"]["position"]["x"]


def test_a_box_holding_a_token_or_a_breakpoint_opens():
    index = {"b/l/solve": {"region": "b"}, "a/l/solve": {"region": "a"}, "a/l": {"region": "a"}}
    state = SimpleNamespace(boxes_closed=["a", "b"], cur_node=None, cur_nodes=[], cur_tests=[], bps=[])
    pane = SimpleNamespace(app=SimpleNamespace(state=state), model=SimpleNamespace(index=index))
    shut = lambda: _DagPane._boxes_shut(pane)  # noqa: E731
    assert shut() == {"a", "b"}
    state.cur_nodes = ["b/l/solve"]
    assert shut() == {"a"}
    state.cur_nodes, state.cur_tests = [], ["a/l"]  # every region's token counts
    assert shut() == {"b"}
    state.bps = ["b/l/solve"]
    assert shut() == set()


def test_the_inspector_names_a_coupling_op_s_exchange():
    rows = {r["k"]: r["v"] for r in _inspector_model(_model(*_pair()), "a/time_loop/a.couple", [])["rows"]}
    assert rows["exchange"] == "x ← b (b.to_a), exchange a-b"
    assert rows["loop test"] == "exchange · serial"


def test_the_init_view_puts_each_region_s_steps_in_its_box():
    steps = [InitStep("a.mesh", lambda r: None, region="a"), InitStep("b.mesh", lambda r: None, region="b"),
             InitStep("b.T", lambda r: None, ["b.mesh"], region="b")]
    graphs = (_region("b", exchanges=[("a-b", "a")]), _region("a", exchanges=[("a-b", "b")]))
    flow = build_flow([init_dict(s) for s in steps], [op_dict(o) for g in graphs for o in g], view="init")
    nodes = {n["id"]: n for n in flow["nodes"]}

    # the regions in the order of the execution view (b, then a)
    bx, ax = nodes["init::box:b"]["position"]["x"], nodes["init::box:a"]["position"]["x"]
    assert bx < ax
    for step, region in (("init:a.mesh", "a"), ("init:b.mesh", "b"), ("init:b.T", "b")):
        box, n = nodes[f"init::box:{region}"], nodes[step]
        assert box["position"]["x"] < n["position"]["x"] < box["position"]["x"] + box["width"]
        assert box["position"]["y"] < n["position"]["y"] < box["position"]["y"] + box["height"]
    edges = {(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" not in e.get("class", "")}
    assert edges == {("init:b.mesh", "init:b.T")}
    m = nodes["exmap:0"]
    assert bx + nodes["init::box:b"]["width"] < m["position"]["x"] < m["position"]["x"] + m["width"] < ax
    assert [(e["source"], e["target"]) for e in flow["edges"] if "nf-couple" in e.get("class", "")] == [
        ("init::box:b", "exmap:0"), ("exmap:0", "init::box:a")]


def test_the_mapping_node_shows_kind_points_and_mapped_fields_each_way():
    # a has 4 interface points, b 3 (each its own end tells); T is mapped, h handed over as is
    flow = _flow_of(*_pair(PointMapping("nearest-neighbor", ("T",), None, 4),
                           PointMapping("nearest-neighbor", ("T",), None, 3)))
    data = {n["id"]: n for n in flow["nodes"]}["exmap:0"]["data"]
    assert data["kind"] == "nearest-neighbor"
    assert data["points"] == {"a": 4, "b": 3}
    assert data["ways"] == [
        {"from": "a", "to": "b", "fields": [{"name": "T", "mapped": True}, {"name": "h", "mapped": False}]},
        {"from": "b", "to": "a", "fields": [{"name": "T", "mapped": True}, {"name": "h", "mapped": False}]}]


def test_the_mapping_node_marks_what_the_solver_does_not_tell():
    flow = _flow_of(*_pair(PointMapping(), PointMapping(mapped=("T", "h"))))
    data = {n["id"]: n for n in flow["nodes"]}["exmap:0"]["data"]
    assert data["kind"] is None and data["points"] == {"a": None, "b": None}
    assert data["ways"][0]["fields"] == [{"name": "T", "mapped": True}, {"name": "h", "mapped": True}]
    assert data["ways"][1]["fields"] == [{"name": "T", "mapped": None}, {"name": "h", "mapped": None}]


def test_the_inspector_shows_a_coupling_op_s_mapping():
    model = _model(*_pair(PointMapping("nearest-neighbor", ("T",), None, 4), None))
    rows = {r["k"]: r["v"] for r in _inspector_model(model, "a/time_loop/a.couple", [])["rows"]}
    assert rows["mapping"].startswith("nearest-neighbor")


def test_a_plain_solver_s_init_view_has_no_region_boxes():
    steps, ops = _graph()
    flow = build_flow(steps, ops, view="init")
    assert not [n for n in flow["nodes"] if n["type"] == "region"]


def test_the_dag_view_follows_the_run_s_phase_until_the_user_switches():
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
    steps = [InitStep("time", lambda r: None), InitStep("a.mesh", lambda r: None, ["time"], region="a")]
    flow = build_flow([init_dict(s) for s in steps], None, view="init", regions=("a", "b"))
    nodes = {n["id"]: n for n in flow["nodes"]}

    assert nodes["init::box:a"]["data"]["label"] == "a"
    assert nodes["init::box:b"]["data"]["label"] == "b · not initialized yet"
    assert nodes["init::box:a"]["position"]["x"] < nodes["init::box:b"]["position"]["x"]


def test_the_dag_pane_shows_what_a_coupling_node_hands_over_and_heats_its_line():
    state = SimpleNamespace(ex_vals={}, ex_hot=[])
    pane = SimpleNamespace(app=SimpleNamespace(state=state), _line_of={"c/couple_b.x": ["0"], "c/couple_a.x": ["0"]},
                           _line_sides={"0": ("a", "b")}, _line_name={"0": None})
    exchanged = lambda msg: _DagPane._exchanged(pane, msg)  # noqa: E731
    exchanged({"node": "c/couple_b.x", "exchange": {"from": "a", "to": "b", "data": {"x": "1.5"}}})
    assert state.ex_hot == ["0"] and state.ex_vals == {"0": {"a": "x = 1.5"}}
    exchanged({"node": "c/couple_a.x", "exchange": {"from": "b", "to": "a", "data": {"x": "0.75"}}})
    assert state.ex_vals == {"0": {"a": "x = 1.5", "b": "x = 0.75"}}
    exchanged({"node": "c/a.solve"})
    assert state.ex_hot == []


def test_a_coupling_op_s_test_heats_all_its_lines_and_fills_each_by_exchange_name():
    state = SimpleNamespace(ex_vals={}, ex_hot=[])
    # b's coupling op sits on the lines a-b (0) and b-c (1)
    pane = SimpleNamespace(app=SimpleNamespace(state=state), _line_of={"g/b.couple": ["0", "1"]},
                           _line_sides={"0": ("a", "b"), "1": ("b", "c")}, _line_name={"0": "a-b", "1": "b-c"})
    _DagPane._exchanged(pane, {"node": "g/b.couple", "kind": "test", "exchanges": [
        {"from": "a", "to": "b", "exchange": "a-b", "data": {"x": "1"}},
        {"from": "c", "to": "b", "exchange": "b-c", "data": {"x": "2"}}]})
    assert state.ex_hot == ["0", "1"]
    assert state.ex_vals == {"0": {"a": "x = 1"}, "1": {"c": "x = 2"}}


def test_field_rows_are_grouped_per_region_in_the_boxes_order():
    rows = [{"name": n} for n in ("c.x", "a.x", "time", "b.x", "a.T")]
    out = _by_region(rows, ["a", "b", "c"])
    assert [r["name"] for r in out] == ["time", "a.x", "a.T", "b.x", "c.x"]
    assert out[1]["region"] == "a" and out[1]["color"] == _REGION.color("a") and out[0]["color"] is None


def test_a_region_fields_table_is_drawn_in_the_region_s_colour():
    history = _TableHistory()
    history.add({"name": "fields · b", "time": 1.0, "columns": ["x"], "values": [[0.5]], "region": "b",
                 "x": "coupling pass"})
    (chart,) = history.charts()
    assert chart["data"][0]["line"]["color"] == _REGION.color("b")
    assert chart["layout"]["xaxis"]["title"]["text"] == "coupling pass"


def test_the_wire_carries_a_coupling_node_s_mapping_only_when_known():
    plain = op_dict(Op("couple_a.x", "action", lambda ctx: None, exchange=Exchange("b.solve", ("x",), "b")))
    assert "mapping" not in plain["exchange"]
    told = op_dict(Op("couple_a.x", "action", lambda ctx: None,
                      exchange=Exchange("b.solve", ("x",), "b", PointMapping("identity", ("x",), None, 2))))
    assert told["exchange"]["mapping"] == {"kind": "identity", "mapped": ["x"], "source_points": None,
                                           "target_points": 2}


def test_the_app_shows_a_token_per_region_and_who_waits_for_whom():
    app = StepperApp(get_server("regions_tokens", client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D")
    model = app.model
    model.graph({"activity": "exec", "ops": [op_dict(o) for g in _pair() for o in g]})
    model.reindex("multiRun")
    couple_a, solve_b = "a/time_loop/a.couple", "b/time_loop/b.couple/solve"
    test_b = {"kind": "test", "node": "b/time_loop/b.couple", "depth": 1, "iter": 2,
              "stack": [["b/time_loop", 1]], "region": "b"}
    waiting_a = {"kind": "test", "node": couple_a, "depth": 1, "iter": 2, "stack": [["a/time_loop", 1]], "region": "a"}
    ev = {**test_b, "regions": [
        {"region": "a", "state": "waiting", "event": waiting_a, "waiting_for": "b"},
        {"region": "b", "state": "paused", "event": test_b, "waiting_for": None}]}
    app._on_paused({"type": "paused", "event": ev, "finished": False, "failed": False,
                    "summary": {"scalars": {}, "fields": [], "init_done": []},
                    "calls": {}, "last_ms": {}, "total_ms": {}, "writes_seen": {}})
    s = app.state

    assert s.cur_nodes == [] and s.cur_tests == ["b/time_loop/b.couple"]
    assert s.wait_nodes == [couple_a] and s.waiting == {"a": "waiting for b"}
    assert set(s.active_loops) == {"a/time_loop", "b/time_loop", couple_a, "b/time_loop/b.couple"}
    assert s.loop_iter[couple_a] == 2 and s.loop_iter["b/time_loop/b.couple"] == 2
    assert s.status == "paused before a: waiting for b · b: ◇ b.couple test #2"
    assert s.callstack[0] == "a: ⏳ in a.couple, waiting for b"
    # a running token moves its own region only
    app._on_running({"type": "running", "event": {"kind": "exec", "node": solve_b, "depth": 2, "iter": None,
                                                    "stack": [["b/time_loop", 1], ["b/time_loop/b.couple", 2]],
                                                    "region": "b"}})
    assert s.cur_nodes == [solve_b] and s.wait_nodes == [couple_a]
    # a single-region solver: one token, as before
    app._on_paused({"type": "paused", "event": {"kind": "exec", "node": "x", "depth": 0, "iter": None, "stack": []},
                    "finished": False, "failed": False, "summary": {"scalars": {}, "fields": [], "init_done": []},
                    "calls": {}, "last_ms": {}, "total_ms": {}, "writes_seen": {}})
    assert (s.cur_nodes, s.cur_tests, s.waiting) == (["x"], [], {})
