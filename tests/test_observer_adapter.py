"""The NeoFOAM observer adapter maps hook calls to graph node ids (no neofoam needed)."""

from types import SimpleNamespace

from neofoam_ui.adapters.neofoam._observed import _Stepper
from neofoam_ui.engine import assign_ids


class FakeOp:
    """Just enough of neofoam's Operation: metadata, kind, children, and hook calls on run."""

    def __init__(self, name, children=None, iterations=0):
        self.metadata = SimpleNamespace(op_name=name, name=name, domain_name=None,
                                        operation_number=None, description="")
        self.operation_type = "iterative" if children is not None else "sequential"
        self.sub_operations = children or []
        self.iterations = iterations
        self.func = SimpleNamespace(func=lambda ctx: None)

    def run(self, obs, ctx=None):
        if self.operation_type == "sequential":
            obs.before_operation(self, ctx)
            obs.after_operation(self, ctx)
            return
        i = 1
        while True:
            obs.before_loop_test(self, ctx, i)
            go = i <= self.iterations
            obs.after_loop_test(self, ctx, i, go)
            if not go:
                return
            for child in self.sub_operations:
                child.run(obs, ctx)
            i += 1


class FakeSession:
    def __init__(self):
        self.ctx, self.ops, self.records = None, [], []

    def set_ops(self, ops):
        assign_ids(ops)
        self.ops = ops

    def set_context(self, ctx):
        self.ctx = ctx

    def record(self, kind, node, name, ms, **extra):
        self.records.append((kind, node))


def _run(ops):
    session, events = FakeSession(), []
    stepper = _Stepper(session, events.append)
    stepper.operations(SimpleNamespace(ops=ops))
    for op in ops:
        op.run(stepper, ctx=object())
    return session, [(e.kind, e.node, e.iter) for e in events]


def test_same_operation_twice_maps_to_both_nodes():
    shared, x = FakeOp("shared"), FakeOp("x")
    loop = FakeOp("loop", [x, x], iterations=2)
    session, events = _run([shared, loop, shared])
    assert events == [
        ("exec", "shared", None),
        ("test", "loop", 1), ("exec", "loop/x", None), ("exec", "loop/x#2", None),
        ("test", "loop", 2), ("exec", "loop/x", None), ("exec", "loop/x#2", None),
        ("test", "loop", 3),
        ("exec", "shared#2", None),
    ]
    assert [r for r in session.records if r[0] == "exec"].count(("exec", "loop/x#2")) == 2


def test_nested_loops_and_untracked_inner_ops():
    gate_inner = FakeOp("inner_only")
    gate = FakeOp("gate")
    gate.run = lambda obs, ctx=None: (obs.before_operation(gate, ctx), gate_inner.run(obs, ctx),
                                      obs.after_operation(gate, ctx))
    inner = FakeOp("inner", [gate], iterations=1)
    outer = FakeOp("outer", [inner], iterations=2)
    _, events = _run([outer])
    assert events == [
        ("test", "outer", 1), ("test", "outer/inner", 1), ("exec", "outer/inner/gate", None),
        ("test", "outer/inner", 2),
        ("test", "outer", 2), ("test", "outer/inner", 1), ("exec", "outer/inner/gate", None),
        ("test", "outer/inner", 2),
        ("test", "outer", 3),
    ]


# -- multi-region solvers (NeoFOAM multiRun) ---------------------------------------------------


class RegionSession(FakeSession):
    """A FakeSession that also keeps init steps, results and emitted messages."""

    def __init__(self):
        super().__init__()
        self.init, self.results, self.emitted = [], {}, []

    def emit(self, msg):
        self.emitted.append(msg)

    def observe_result(self, value):
        pass


def _step(name, region=None, depends_on=()):
    return SimpleNamespace(name=name, region=region, depends_on=list(depends_on), category="field",
                           initializer=lambda ctx: None)


def test_region_init_steps_are_added_with_the_region_in_their_name():
    session = RegionSession()
    stepper = _Stepper(session, lambda event: None)
    stepper.init_steps([_step("time")])
    stepper.init_steps([_step("mesh", "a"), _step("fields.x", "a", ["mesh"])])
    stepper.init_steps([_step("fields.x", "b")])

    assert [(s.id, s.region, s.depends_on) for s in session.init] == [
        ("init:time", None, []),
        ("init:a.mesh", "a", []),
        ("init:a.fields.x", "a", ["a.mesh"]),
        ("init:b.fields.x", "b", []),
    ]
    graphs = [m for m in session.emitted if m["type"] == "graph"]
    assert len(graphs) == 2 and graphs[-1]["steps"][-1]["region"] == "b"
    stepper.after_init_step(_step("fields.x", "b"), {}, 7)
    assert session.results == {"b.fields.x": 7}


def test_each_region_reports_its_own_graph_and_keeps_its_own_stack():
    session = RegionSession()
    session.region_ops = {}
    session.set_region_ops = lambda region, ops, rank=None: (assign_ids(ops, f"{region}/"),
                                                             session.region_ops.__setitem__(region, ops))
    events = []
    stepper = _Stepper(session, events.append)
    graphs = {r: FakeOp("time_loop", [FakeOp("solve")], iterations=1) for r in ("a", "b")}
    ctxs = {r: SimpleNamespace(region=r, models={}) for r in ("a", "b")}
    for r in ("a", "b"):
        stepper.operations(SimpleNamespace(ops=[graphs[r]]), ctxs[r])
    # interleaved, as from two threads: each region's events stay its own
    loop_a, loop_b = graphs["a"], graphs["b"]
    stepper.before_loop_test(loop_a, ctxs["a"], 1)
    stepper.before_loop_test(loop_b, ctxs["b"], 1)
    stepper.after_loop_test(loop_b, ctxs["b"], 1, True)
    stepper.after_loop_test(loop_a, ctxs["a"], 1, True)
    stepper.before_operation(loop_a.sub_operations[0], ctxs["a"])
    stepper.before_operation(loop_b.sub_operations[0], ctxs["b"])

    assert [(e.region, e.kind, e.node, e.stack) for e in events] == [
        ("a", "test", "a/time_loop", ()), ("b", "test", "b/time_loop", ()),
        ("a", "exec", "a/time_loop/solve", (("a/time_loop", 1),)),
        ("b", "exec", "b/time_loop/solve", (("b/time_loop", 1),)),
    ]
    assert set(session.ctx) == {"a", "b"} and session.ctx["b"] is ctxs["b"]


def test_multi_region_fields_and_times_are_named_by_region():
    import numpy as np  # noqa: PLC0415

    from neofoam_ui.adapters.neofoam._fields import _FieldAccess, _RegionContexts  # noqa: PLC0415

    def region(x):
        return SimpleNamespace(fields={"x": np.array([x])}, mesh=None,
                               time=SimpleNamespace(value=x, delta_t=1.0, index=1, write_time=False))

    contexts = _RegionContexts(a=region(2.0), b=region(1.0))
    access = _FieldAccess()

    assert {k: v.tolist() for k, v in access.fields(contexts).items()} == {"a.x": [2.0], "b.x": [1.0]}
    assert access.scalars(contexts)["a.time"] == 2.0 and access.scalars(contexts)["b.time"] == 1.0
    assert [(r.name, r.mesh(contexts)) for r in access.regions(contexts)] == [("a", None), ("b", None)]
    assert [r.name for r in access.regions(SimpleNamespace(fields={}, models={}))] == ["region0"]


def test_a_loop_driven_by_a_bound_method_shows_its_class():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415

    class Predicate:
        def _next_pass(self, ctx):
            return False

    loop = FakeOp("loop", [])
    loop.func = SimpleNamespace(func=Predicate()._next_pass)
    assert _convert(loop, {}).test == "Predicate()"


class Model:
    """Just enough of a pydantic model class: its field names."""

    def __init__(self, *fields):
        self.model_fields = dict.fromkeys(fields)


def _coupling(kind="nearest-neighbor", points=(2, 3)):
    """Fake NeoFOAM coupling ops of regions a and b (public read API: region, label, sides,
    exchange_of() with name / partner / mapping.kind, partner() as a label, is_leader,
    controller); b's leads."""
    sides = {r: SimpleNamespace(region=r, patch=f"to_{p}", receives=Model("T", "h"), mapped=("T",),
                                points=n)
             for (r, p), n in zip((("a", "b"), ("b", "a")), points)}

    class CouplingOp:
        def __init__(self, region, partner):
            self.region, self.label, self.sides = region, f"{region}.couple", (sides[region],)
            self.is_leader = region == "b"
            self.controller = SimpleNamespace(label="b.couple", parallel=False, residuals={}) \
                if self.is_leader else None
            self._exchange = SimpleNamespace(name="a-b", partner=(partner, f"to_{region}"),
                                             mapping=SimpleNamespace(kind=kind))
            self.func = self._test

        def exchange_of(self, side):
            return self._exchange

        def partner(self, side):
            return ".".join(self._exchange.partner)

        def _test(self, ctx):
            return False

    return CouplingOp("a", "b"), CouplingOp("b", "a")


def _loop(name, func, children=(), iterations=0):
    op = FakeOp(name, list(children), iterations=iterations)
    op.func = func
    return op


def _region_ctx(region, parallel=False, **fields):
    coupling = SimpleNamespace(coupling=SimpleNamespace(parallel=parallel, group=("a", "b"), transport=None))
    return SimpleNamespace(region=region, models={"coupling": coupling}, fields=fields,
                           time=SimpleNamespace(value=0.0))


def test_a_coupling_op_is_a_loop_whose_test_exchanges_one_per_side():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415
    from neofoam_ui.engine import Exchange, PointMapping  # noqa: PLC0415

    a, b = _coupling()
    ops = [_convert(_loop(f"{r}.couple", c, [FakeOp("solve")]), {}, _region_ctx(r, parallel=True))
           for r, c in (("a", a), ("b", b))]
    # a's scheme from its region's coupling, the leader's from its controller
    assert [(o.kind, o.test, o.tag) for o in ops] == [("loop", "exchange · parallel", None),
                                                      ("loop", "exchange · serial", "leader")]
    assert ops[0].exchanges == [
        Exchange("b.to_a", ("T", "h"), "b", PointMapping("nearest-neighbor", ("T",), None, 2), "a-b")]
    assert ops[1].exchanges[0].partner == "a" and ops[1].exchange is None


def test_a_coupling_op_s_mapping_tells_kind_and_points():
    from neofoam_ui.adapters.neofoam._ops import _mapping  # noqa: PLC0415
    from neofoam_ui.engine import PointMapping  # noqa: PLC0415

    a, _b = _coupling(kind="identity", points=(None, None))
    side = a.sides[0]
    assert _mapping(a.exchange_of(side), side) == PointMapping("identity", ("T",), None, None)
    bare = SimpleNamespace()
    assert _mapping(SimpleNamespace(), bare) is None


def _recording(session):
    session.records = []
    session.record = lambda kind, node, name, ms, **extra: session.records.append((kind, node, extra))
    session.set_region_ops = lambda region, ops, rank=None: assign_ids(ops, f"{region}/")
    return session


def test_the_leader_s_test_reports_its_controller_s_residuals():
    _a, b = _coupling()
    op = _loop("b.couple", b, [FakeOp("solve")], iterations=1)
    session = _recording(RegionSession())
    stepper = _Stepper(session, lambda event: None)
    ctx = _region_ctx("b")
    stepper.operations(SimpleNamespace(ops=[op]), ctx)
    stepper.before_loop_test(op, ctx, 1)
    stepper.after_loop_test(op, ctx, 1, True)
    b.controller.residuals = {"x": 0.25}
    stepper.before_loop_test(op, ctx, 2)
    stepper.after_loop_test(op, ctx, 2, False)

    tests = [r for r in session.records if r[0] == "test"]
    assert [r[2].get("residuals") for r in tests] == [None, {"x": 0.25}]
    assert {r[2]["region"] for r in tests} == {"b"}


def test_a_coupling_op_s_test_records_what_it_handed_each_side():
    import numpy as np  # noqa: PLC0415

    a, _b = _coupling()
    op = _loop("a.couple", a, [FakeOp("solve")])
    session = _recording(RegionSession())
    stepper = _Stepper(session, lambda event: None)
    ctx = _region_ctx("a")
    stepper.operations(SimpleNamespace(ops=[op]), ctx)

    stepper.before_loop_test(op, ctx, 1)
    # NeoFOAM's coupling_exchanged, from the test (a pydantic model iterates as (name, value))
    stepper.coupling_exchanged(a, a.sides[0], [("T", np.array([0.5, 1.0])), ("h", 2.0)])
    stepper.after_loop_test(op, ctx, 1, False)

    assert [extra.get("exchanges") for kind, _node, extra in session.records if kind == "test"] == [
        [{"from": "b", "to": "a", "exchange": "a-b", "data": {"T": "mean 0.75 (n=2)", "h": "2"}}]]
    assert stepper.runs["a"].handed == {}


def test_a_coupling_op_s_test_reports_its_region_s_fields():
    import numpy as np  # noqa: PLC0415

    emitted = []
    session = _recording(RegionSession())
    session.emit = emitted.append
    stepper = _Stepper(session, lambda event: None)
    for region, values, coupling in (("a", [1.5], _coupling()[0]), ("b", [0.25, 0.75], _coupling()[1])):
        ctx = _region_ctx(region, x=np.array(values))
        op = _loop(f"{region}.couple", coupling, iterations=1)
        stepper.operations(SimpleNamespace(ops=[op]), ctx)
        for i in (1, 2):
            stepper.before_loop_test(op, ctx, i)
            stepper.after_loop_test(op, ctx, i, i == 1)

    tables = [m for m in emitted if m["type"] == "pptable"]
    assert [(m["name"], m["time"], m["values"], m["region"]) for m in tables] == [
        ("fields · a", 1.0, [[1.5]], "a"), ("fields · a", 2.0, [[1.5]], "a"),
        ("fields · b", 1.0, [[0.5]], "b"), ("fields · b", 2.0, [[0.5]], "b")]


def test_a_receive_on_the_transport_shows_its_region_waiting_for_the_sender():
    calls = []

    class Lanes:
        def waiting(self, region, source, tag):
            from contextlib import contextmanager  # noqa: PLC0415

            @contextmanager
            def wait():
                calls.append(("wait", region, source, tag))
                yield
                calls.append(("woke", region))

            return wait()

        def delivered(self, source, target, tag):
            calls.append(("sent", source, target, tag))

    transport = SimpleNamespace(receive=lambda s, t, tag: 42, send=lambda s, t, tag, m: None)
    stepper = _Stepper(RegionSession(), lambda event: None, Lanes())
    stepper._watch(transport)
    stepper._watch(transport)  # (once)
    transport.send("b", "a", "x1", 1.0)
    assert transport.receive("b", "a", "x1") == 42
    assert calls == [("sent", "b", "a", "x1"), ("wait", "a", "b", "x1"), ("woke", "a")]


def test_a_coupling_outcome_is_a_table_row_per_time_step():
    session = RegionSession()
    stepper = _Stepper(session, lambda event: None)
    controller = SimpleNamespace(label="b.couple")
    for passes, converged in [(20, True), (5, False)]:
        stepper.coupling_finished(controller, SimpleNamespace(passes=passes, converged=converged,
                                                              residuals={"x": 1e-7}))

    assert [(m["name"], m["time"], m["columns"], m["values"]) for m in session.emitted] == [
        ("coupling · b.couple", 1.0, ["passes", "converged", "residual x"], [[20.0, 1.0, 1e-7]]),
        ("coupling · b.couple", 2.0, ["passes", "converged", "residual x"], [[5.0, 0.0, 1e-7]]),
    ]
