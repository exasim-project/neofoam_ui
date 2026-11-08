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


def _exchange(kind="nearest-neighbor", points=(2, 3)):
    """A fake NeoFOAM exchange of a and b (public read API: sides, mapping.kind)."""
    a = SimpleNamespace(region="a", op="a.solve_T", receives=Model("T", "h"), mapped=("T",),
                        points=points[0])
    b = SimpleNamespace(region="b", op="b.solve_T", receives=Model("T", "h"), mapped=("T",),
                        points=points[1])
    return SimpleNamespace(sides=(a, b), mapping=SimpleNamespace(kind=kind))


class CouplingNode:
    """Hands the side its partner's data (NeoFOAM's CouplingNode: side, partner, exchange)."""

    def __init__(self, exchange, index):
        self.exchange, self.side, self.partner = exchange, exchange.sides[index], exchange.sides[1 - index]

    def __call__(self, ctx):
        return None


class MappingNode(CouplingNode):
    """Maps the partner's data onto the side's points."""


def test_a_coupling_node_names_the_partner_op_whose_data_it_hands_over():
    from neofoam_ui.adapters.neofoam._ops import _convert  # noqa: PLC0415
    from neofoam_ui.engine import Exchange, PointMapping  # noqa: PLC0415

    exchange = _exchange()
    nodes = [FakeOp(f"{r}.couple_to_{p}") for r, p in (("a", "b"), ("b", "a"))]
    for i, node in enumerate(nodes):
        node.func = SimpleNamespace(func=CouplingNode(exchange, i))
    assert [_convert(node, {}).exchange for node in nodes] == [
        Exchange("b.solve_T", ("T", "h"), "b", PointMapping("nearest-neighbor", ("T",), 3, 2)),
        Exchange("a.solve_T", ("T", "h"), "a", PointMapping("nearest-neighbor", ("T",), 2, 3))]
    # a plain op has none
    assert _convert(FakeOp("a.solve_T"), {}).exchange is None


def test_a_coupling_node_tells_how_it_maps_its_partner_s_data():
    from neofoam_ui.adapters.neofoam._ops import _mapping  # noqa: PLC0415
    from neofoam_ui.engine import PointMapping  # noqa: PLC0415

    exchange = _exchange(kind="identity", points=(None, None))
    side, partner = exchange.sides
    # the exchange's mapping kind; a side without coordinates: its points are unknown
    assert _mapping(exchange, side, partner) == PointMapping("identity", ("T",), None, None)
    # nothing known at all: no mapping
    bare = SimpleNamespace()
    assert _mapping(SimpleNamespace(), bare, bare) is None


def test_a_mapping_node_is_a_node_that_names_the_coupling_node_it_feeds():
    exchange = _exchange()
    exchange.sides[0].node = "a.couple_to_b"
    mapping, couple, solve = FakeOp("map_a-b_to_a"), FakeOp("a.couple_to_b"), FakeOp("a.solve_T")
    mapping.func = SimpleNamespace(func=MappingNode(exchange, 0))
    couple.func = SimpleNamespace(func=CouplingNode(exchange, 0))
    loop = FakeOp("loop", [mapping, couple, solve], iterations=1)
    session = FakeSession()
    stepper = _Stepper(session, lambda event: None)

    stepper.operations(SimpleNamespace(ops=[loop]))
    loop.run(stepper)

    children = session.ops[0].children
    assert [(c.name, c.maps, c.exchange is not None) for c in children] == [
        ("map_a-b_to_a", "a.couple_to_b", False), ("a.couple_to_b", None, True), ("a.solve_T", None, False)]
    # it runs as its own node: recorded (and paused on) before the coupling node
    assert [node for kind, node in session.records if kind == "exec"] == [c.id for c in children]


def test_a_coupling_node_reports_what_it_hands_its_side():
    import numpy as np  # noqa: PLC0415

    from neofoam_ui.adapters.neofoam._observed import _exchanged  # noqa: PLC0415

    exchange = _exchange()
    node = FakeOp("b.couple_to_a")
    node.func = SimpleNamespace(func=CouplingNode(exchange, 1))
    side = exchange.sides[1]
    # what NeoFOAM's coupling_exchanged hook reported for the side (a pydantic model iterates
    # as (name, value))
    handed = {id(side): [("T", np.array([1.5])), ("h", 2.0)]}
    assert _exchanged(node, handed) == {"from": "a", "to": "b", "data": {"T": "1.5", "h": "2"}}
    assert handed == {}
    handed[id(side)] = [("T", np.array([0.5, 1.0]))]
    assert _exchanged(node, handed)["data"] == {"T": "mean 0.75 (n=2)"}
    # nothing reported, or another op: none
    assert _exchanged(node, {}) is None
    assert _exchanged(FakeOp("a.solve_x"), handed) is None


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
