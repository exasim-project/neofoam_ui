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
