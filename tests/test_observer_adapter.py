"""The NeoFOAM observer adapter maps hook calls to graph node ids (no neofoam needed)."""

from types import SimpleNamespace

from neofoam_ui.adapters.neofoam import _Stepper
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
