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


def test_a_region_op_keeps_the_driver_context():
    driver, region_ctx = object(), object()
    a = FakeOp("a.solve")
    a.metadata.region = "a"
    loop = FakeOp("loop", [a], iterations=1)
    session = RegionSession()
    stepper = _Stepper(session, lambda event: None)
    stepper.operations(SimpleNamespace(ops=[loop]))
    stepper.before_loop_test(loop, driver, 1)
    stepper.after_loop_test(loop, driver, 1, True)
    stepper.before_operation(a, region_ctx)

    assert session.ctx is driver
    assert session.ops[0].children[0].region == "a"


def test_a_coupling_outcome_is_a_table_row_per_loop_run():
    session = RegionSession()
    stepper = _Stepper(session, lambda event: None)
    coupling = SimpleNamespace(label="loop_solving_a.x_b.x")
    for passes, converged in [(20, True), (5, False)]:
        stepper.coupling_finished(coupling, SimpleNamespace(passes=passes, converged=converged,
                                                            residuals={"x": 1e-7}))

    assert [(m["name"], m["time"], m["columns"], m["values"]) for m in session.emitted] == [
        ("coupling · loop_solving_a.x_b.x", 1.0, ["passes", "converged", "residual x"], [[20.0, 1.0, 1e-7]]),
        ("coupling · loop_solving_a.x_b.x", 2.0, ["passes", "converged", "residual x"], [[5.0, 0.0, 1e-7]]),
    ]


def test_multi_region_fields_are_named_by_region():
    import numpy as np  # noqa: PLC0415

    from neofoam_ui.adapters.neofoam._fields import _FieldAccess  # noqa: PLC0415

    region = lambda x: SimpleNamespace(fields={"x": np.array([x])}, mesh=None)  # noqa: E731
    driver = SimpleNamespace(fields={}, models={"regions": {"a": (None, region(2.0)), "b": (None, region(1.0))}})
    access = _FieldAccess()

    assert {k: v.tolist() for k, v in access.fields(driver).items()} == {"a.x": [2.0], "b.x": [1.0]}
    assert [(r.name, r.mesh(driver)) for r in access.regions(driver)] == [("a", None), ("b", None)]
    assert [r.name for r in access.regions(SimpleNamespace(fields={}, models={}))] == ["region0"]


def test_a_loop_driven_by_a_bound_method_shows_its_class():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415

    class CouplingOp:
        def _next_pass(self, ctx):
            return False

    loop = FakeOp("loop", [])
    loop.func = SimpleNamespace(func=CouplingOp()._next_pass)
    assert _convert(loop, {}).test == "CouplingOp()"


def test_a_coupling_loop_names_its_scheme():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415

    class CouplingOp:
        parallel = False

        def _next_pass(self, ctx):
            return False

    loop = FakeOp("loop", [])
    loop.func = SimpleNamespace(func=CouplingOp()._next_pass)
    assert _convert(loop, {}).test == "CouplingOp() · serial"


class Model:
    """Just enough of a pydantic model class: its field names."""

    def __init__(self, *fields):
        self.model_fields = dict.fromkeys(fields)


def _coupling(kind="nearest-neighbor", points=(2, 3)):
    """Fake NeoFOAM coupling ops of regions a and b (public read API: sides, partner(),
    exchange_of(), leader / is_leader, exchange name and mapping.kind); b's leads."""
    a = SimpleNamespace(region="a", coupling_op="a.couple", receives=Model("T", "h"), mapped=("T",),
                        points=points[0])
    b = SimpleNamespace(region="b", coupling_op="b.couple", receives=Model("T", "h"), mapped=("T",),
                        points=points[1])
    exchange = SimpleNamespace(name="a-b", sides=(a, b), mapping=SimpleNamespace(kind=kind))

    class CouplingOp:
        def __init__(self, side, leader=None):
            self.sides, self.label, self.parallel = (side,), f"{side.region}.couple", False
            self.leader = leader or self
            self.residuals = {}
            self.func = self._test

        @property
        def is_leader(self):
            return self.leader is self

        def partner(self, side):
            return b if side is a else a

        def exchange_of(self, side):
            return exchange

        def _test(self, ctx):
            return False

    lead = CouplingOp(b)
    return CouplingOp(a, lead), lead


class CouplingGroup:
    """Repeats the coupling ops while the leader decides to go on."""

    def __init__(self, leader):
        self.leader, self.func = leader, lambda ctx: True


def _loop(name, func, children=(), iterations=0):
    op = FakeOp(name, list(children), iterations=iterations)
    op.func = func
    return op


def test_a_coupling_op_is_a_loop_whose_test_exchanges_one_per_side():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415
    from neofoam_ui.engine import Exchange, PointMapping  # noqa: PLC0415

    a, b = _coupling()
    ops = [_convert(_loop(f"{r}.couple", c, [FakeOp(f"{r}.solve")]), {}) for r, c in (("a", a), ("b", b))]
    assert [(o.kind, o.test, o.tag) for o in ops] == [("loop", "exchange · serial", None),
                                                      ("loop", "exchange · serial", "leader")]
    assert ops[0].exchanges == [
        Exchange("b.couple", ("T", "h"), "b", PointMapping("nearest-neighbor", ("T",), 3, 2), "a-b")]
    assert ops[1].exchanges[0].partner == "a" and ops[1].exchange is None


def test_the_coupling_group_names_its_leader_s_decision():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415

    _a, b = _coupling()
    assert _convert(_loop("loop_solving_a.x_b.x", CouplingGroup(b)), {}).test == "b.couple.decide()"


def test_a_coupling_op_s_mapping_tells_kind_and_points():
    from neofoam_ui.adapters.neofoam._ops import _mapping  # noqa: PLC0415
    from neofoam_ui.engine import PointMapping  # noqa: PLC0415

    a, _b = _coupling(kind="identity", points=(None, None))
    side = a.sides[0]
    assert _mapping(a.exchange_of(side), side, a.partner(side)) == PointMapping("identity", ("T",), None, None)
    bare = SimpleNamespace()
    assert _mapping(SimpleNamespace(), bare, bare) is None


def test_the_coupling_group_s_test_reports_its_leader_s_residuals():
    _a, b = _coupling()
    loop = _loop("loop_solving_a.x_b.x", CouplingGroup(b), iterations=1)
    session = RegionSession()
    session.records = []
    session.record = lambda kind, node, name, ms, **extra: session.records.append((kind, node, extra))
    stepper = _Stepper(session, lambda event: None)
    stepper.operations(SimpleNamespace(ops=[loop]))
    stepper.before_loop_test(loop, None, 1)
    stepper.after_loop_test(loop, None, 1, True)
    b.residuals = {"x": 0.25}
    stepper.before_loop_test(loop, None, 2)
    stepper.after_loop_test(loop, None, 2, False)

    assert [r[2].get("residuals") for r in session.records if r[0] == "test"] == [None, {"x": 0.25}]


def test_a_coupling_op_s_test_records_what_it_handed_each_side():
    import numpy as np  # noqa: PLC0415

    a, _b = _coupling()
    op = _loop("a.couple", a, [FakeOp("a.solve")])
    session = RegionSession()
    session.records = []
    session.record = lambda kind, node, name, ms, **extra: session.records.append((kind, extra))
    stepper = _Stepper(session, lambda event: None)
    stepper.operations(SimpleNamespace(ops=[op]))

    stepper.before_loop_test(op, None, 1)
    # NeoFOAM's coupling_exchanged, from the test (a pydantic model iterates as (name, value))
    stepper.coupling_exchanged(a, a.sides[0], [("T", np.array([0.5, 1.0])), ("h", 2.0)])
    stepper.after_loop_test(op, None, 1, False)

    assert [extra.get("exchanges") for kind, extra in session.records if kind == "test"] == [
        [{"from": "b", "to": "a", "exchange": "a-b", "data": {"T": "mean 0.75 (n=2)", "h": "2"}}]]
    assert stepper.handed == {}


def test_a_coupling_loop_test_reports_each_region_s_fields():
    import numpy as np  # noqa: PLC0415

    emitted = []
    session = RegionSession()
    session.records = []
    session.record = lambda *a, **k: None
    session.emit = emitted.append
    stepper = _Stepper(session, lambda event: None)
    regions = {"a": (None, SimpleNamespace(fields={"x": np.array([1.5])})),
               "b": (None, SimpleNamespace(fields={"x": np.array([0.25, 0.75])}))}
    ctx = SimpleNamespace(models={"regions": regions})
    loop = _loop("loop", CouplingGroup(_coupling()[1]), iterations=1)
    stepper.operations(SimpleNamespace(ops=[loop]))
    stepper.before_loop_test(loop, ctx, 1)
    stepper.after_loop_test(loop, ctx, 1, True)

    tables = [m for m in emitted if m["type"] == "pptable"]
    assert [(m["name"], m["time"], m["columns"], m["values"], m["region"]) for m in tables] == [
        ("fields · a", 1.0, ["x"], [[1.5]], "a"), ("fields · b", 1.0, ["x"], [[0.5]], "b")]
