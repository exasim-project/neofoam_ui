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


def test_a_coupling_node_names_the_partner_op_whose_data_it_hands_over():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415
    from neofoam_ui.engine import Exchange  # noqa: PLC0415

    sides = [SimpleNamespace(kinds={"T": "Array", "h": "Array"}, op="a.solve_T", region="a"),
             SimpleNamespace(kinds={"T": "Array", "h": "Array"}, op="b.solve_T", region="b")]

    class CouplingNode:
        """Hands the side its partner's data."""

        def __init__(self, index):
            self.coupling, self.index = SimpleNamespace(sides=sides), index

        def __call__(self, ctx):
            return None

    nodes = [FakeOp(f"couple_{r}.T") for r in ("a", "b")]
    for i, node in enumerate(nodes):
        node.func = SimpleNamespace(func=CouplingNode(i))
    assert [_convert(node, {}).exchange for node in nodes] == [
        Exchange("b.solve_T", ("T", "h"), "b"), Exchange("a.solve_T", ("T", "h"), "a")]
    # a plain op, and a coupling that lacks its partner, have none
    assert _convert(FakeOp("a.solve_T"), {}).exchange is None
    sides.pop()
    assert _convert(nodes[0], {}).exchange is None


def test_a_coupling_loop_test_reports_the_residuals_of_the_last_pass():
    loop = FakeOp("loop", [], iterations=1)
    loop.func = SimpleNamespace(func=lambda ctx: None, residuals={})
    session = RegionSession()
    session.records = []
    session.record = lambda kind, node, name, ms, **extra: session.records.append((kind, node, extra))
    stepper = _Stepper(session, lambda event: None)
    stepper.operations(SimpleNamespace(ops=[loop]))
    stepper.before_loop_test(loop, None, 1)
    stepper.after_loop_test(loop, None, 1, True)
    loop.func.residuals = {"x": 0.25}
    stepper.before_loop_test(loop, None, 2)
    stepper.after_loop_test(loop, None, 2, False)

    assert [r[2].get("residuals") for r in session.records if r[0] == "test"] == [None, {"x": 0.25}]


def test_a_coupling_node_reports_what_its_partner_sends():
    import numpy as np  # noqa: PLC0415

    from neofoam_ui.adapters.neofoam._observed import _exchanged  # noqa: PLC0415

    sides = [SimpleNamespace(kinds={"x": "Array"}, region="a",
                             side=SimpleNamespace(send=lambda: SimpleNamespace(x=np.array([1.5])))),
             SimpleNamespace(kinds={"x": "Array"}, region="b",
                             side=SimpleNamespace(send=lambda: SimpleNamespace(x=np.array([0.5, 1.0]))))]
    node = FakeOp("couple_b.x")
    node.func = SimpleNamespace(func=SimpleNamespace(coupling=SimpleNamespace(sides=sides), index=1))
    assert _exchanged(node) == {"from": "a", "to": "b", "data": {"x": "1.5"}}
    node.func.func.index = 0
    assert _exchanged(node) == {"from": "b", "to": "a", "data": {"x": "mean 0.75 (n=2)"}}
    assert _exchanged(FakeOp("a.solve_x")) is None


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
    loop = FakeOp("loop", [], iterations=1)
    loop.func = SimpleNamespace(func=lambda ctx: None, residuals={})
    stepper.operations(SimpleNamespace(ops=[loop]))
    stepper.before_loop_test(loop, ctx, 1)
    stepper.after_loop_test(loop, ctx, 1, True)

    tables = [m for m in emitted if m["type"] == "pptable"]
    assert [(m["name"], m["time"], m["columns"], m["values"], m["region"]) for m in tables] == [
        ("fields · a", 1.0, ["x"], [[1.5]], "a"), ("fields · b", 1.0, ["x"], [[0.5]], "b")]
