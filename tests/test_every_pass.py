"""Run an op marked final in every pass: the toggle reaches its own region's ``ctx.run_every_pass``
(no neofoam needed: the fake multiRun of ``two_regions`` through the real observer adapter)."""

from types import SimpleNamespace

import pytest
from trame.app import get_server
from two_regions import TwoRegions, _region_graph

from neofoam_ui.adapters.neofoam._observed import ObservedNeoFoamSolver, _Stepper
from neofoam_ui.adapters.neofoam._ops import _convert
from neofoam_ui._ui._layout._dag import _build_flow
from neofoam_ui.app import StepperApp
from neofoam_ui.engine import Op, PassControl, Session, assign_ids
from neofoam_ui.engine._wire import _op_dict
from neofoam_ui.protocol import COMMANDS
from neofoam_ui.toy.heat1d import Heat1D
from neofoam_ui.worker._commands import _handle, _Worker


class _FinalOps(TwoRegions):
    """``two_regions`` with a final op ``io.write`` in each region's coupling loop (see
    :func:`_graph_with_write`); hands the choice over as the observed adapter does."""

    def run_every_pass(self, region, ops):
        self.stepper.run_every_pass(region, ops)


def _graph_with_write(region, partner, transport, passes, fail_at):
    ops = _region_graph(region, partner, transport, passes, fail_at)
    couple = ops[0].sub_operations[-1]
    write = type(couple)("write")
    write.metadata.domain_name, write.metadata.final = "io", True
    couple.sub_operations.append(write)
    return ops


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr("two_regions._region_graph", _graph_with_write)
    solver = _FinalOps()
    contexts = {}
    original = _Stepper.operations

    def operations(self, operations, ctx=None):  # every region's context gets its own set
        ctx.run_every_pass = set()
        contexts[ctx.region] = ctx
        original(self, operations, ctx)

    monkeypatch.setattr(_Stepper, "operations", operations)
    messages = []
    s = Session(solver, messages.append)
    s.start()
    s.advance("skip_init")  # both regions report their graphs
    s.contexts = contexts
    return s


def test_the_final_op_is_marked_on_the_wire():
    meta = SimpleNamespace(op_name="write", name="io.write", domain_name="io", operation_number=None,
                           description="", final=True)
    op = _convert(SimpleNamespace(metadata=meta, operation_type="sequential", run=lambda ctx: None,
                                  func=lambda ctx: None), {})
    assert op.final and _op_dict(op)["final"] is True
    assert "final" not in _op_dict(Op("x", "action", lambda ctx: None))  # optional: final ops only


def test_a_toggle_names_the_op_in_its_own_regions_context_only(session):
    a, b = session.contexts["a"], session.contexts["b"]
    assert a.run_every_pass == set() and b.run_every_pass == set()
    session.set_every_pass(["a/time_loop/a.couple/write"])
    assert a.run_every_pass == {"io.write"} and b.run_every_pass == set()
    before = a.run_every_pass
    session.set_every_pass(["a/time_loop/a.couple/write", "b/time_loop/b.couple/write"])
    assert a.run_every_pass == {"io.write"} and b.run_every_pass == {"io.write"}
    assert a.run_every_pass is not before  # handed over as a new set, never changed in place
    session.set_every_pass(["b/time_loop/b.couple/write"])
    assert a.run_every_pass == set() and b.run_every_pass == {"io.write"}
    session.set_every_pass([])
    assert a.run_every_pass == set() and b.run_every_pass == set()


def test_only_final_ops_reach_the_context(session):
    a = session.contexts["a"]
    session.set_every_pass(["a/time_loop/a.couple/solve", "a/time_loop/a.couple", "no/such/op"])
    assert a.run_every_pass == set()


def test_a_choice_made_before_the_graph_is_applied_when_it_comes(monkeypatch):
    monkeypatch.setattr("two_regions._region_graph", _graph_with_write)
    seen = {}
    original = _Stepper.operations

    def operations(self, operations, ctx=None):
        ctx.run_every_pass = set()
        seen[ctx.region] = ctx
        original(self, operations, ctx)

    monkeypatch.setattr(_Stepper, "operations", operations)
    s = Session(_FinalOps(), lambda m: None)
    s.start()
    s.set_every_pass(["b/time_loop/b.couple/write"])  # (as the UI sends it to a new worker)
    s.advance("skip_init")
    assert seen["a"].run_every_pass == set() and seen["b"].run_every_pass == {"io.write"}


def test_a_solver_that_cannot_and_mpi_refuse():
    s = Session(Heat1D(), lambda m: None)
    assert not isinstance(s.solver, PassControl)
    with pytest.raises(ValueError, match="cannot"):
        s.set_every_pass(["time_loop/write_output"])
    s.solver = SimpleNamespace(run_every_pass=lambda region, ops: None)
    s.peers = object()
    with pytest.raises(ValueError, match="MPI"):
        s.set_every_pass([])


def test_the_worker_command_sets_the_choice(session):
    assert "every_pass" in COMMANDS
    worker = _Worker(session, out=None, acks=None, queue=None)
    _handle(worker, {"cmd": "every_pass", "ids": ["a/time_loop/a.couple/write"]})
    assert session.every_pass == {"a/time_loop/a.couple/write"}
    assert session.contexts["a"].run_every_pass == {"io.write"}


def test_the_stepper_adapter_without_neofoam_support_does_nothing():
    stepper = _Stepper(SimpleNamespace(), lambda ev: None)
    stepper.run_of("a").ctx = SimpleNamespace()  # an older NeoFOAM: no run_every_pass
    stepper.run_every_pass("a", [Op("write", "action", lambda ctx: None, domain="io", final=True)])
    assert not hasattr(stepper.runs["a"].ctx, "run_every_pass")
    solver = ObservedNeoFoamSolver("x", lambda argv: None)
    solver.run_every_pass("a", [])  # not running: nothing
    calls = []
    solver._stepper = SimpleNamespace(run_every_pass=lambda region, ops: calls.append((region, ops)))
    solver.run_every_pass("a", [])
    assert calls == [("a", [])]


def test_the_loop_test_reports_the_final_pass():
    records = []
    session = SimpleNamespace(ctx=None, set_ops=lambda ops: None, set_context=lambda ctx: None,
                              record=lambda kind, node, name, ms, **extra: records.append((node, extra)))
    stepper = _Stepper(session, lambda ev: None)
    loop = SimpleNamespace(metadata=SimpleNamespace(op_name="couple", name="couple", domain_name=None,
                                                    operation_number=None, description=""),
                           operation_type="iterative", sub_operations=[],
                           func=SimpleNamespace(func=lambda ctx: None))
    ctx = SimpleNamespace(loops=[SimpleNamespace(final=False)])
    stepper.operations(SimpleNamespace(ops=[loop]))
    stepper.before_loop_test(loop, ctx, 1)
    stepper.after_loop_test(loop, ctx, 1, True)
    ctx.loops[-1] = SimpleNamespace(final=True)  # (the test marked the pass it lets in)
    stepper.before_loop_test(loop, ctx, 2)
    stepper.after_loop_test(loop, ctx, 2, True)
    assert [extra.get("final") for _, extra in records] == [None, True]


# -- the UI ---------------------------------------------------------------------------------------


def _ui(name):
    """The app with a two-region graph: ``<r>/time_loop/couple/{solve, write}``, write final."""
    app = StepperApp(get_server(name, client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D")
    sent = []
    app.send = sent.append
    pane = next(p for p in app.panes if p.name == "dag")
    pane.app = app

    def op(name, region, final=False, children=()):
        return Op(name, "loop" if children else "action", lambda ctx: None, list(children),
                  domain="io" if final else None, region=region, final=final)

    graph = []
    for r in ("a", "b"):
        ops = [op("time_loop", r, children=[op("couple", r, children=[op("solve", r), op("write", r, True)])])]
        assign_ids(ops, f"{r}/")  # (as Session.set_region_ops)
        graph += [_op_dict(o) for o in ops]
    app.model.graph({"activity": "exec", "ops": graph})
    app.model.reindex("multiRun")
    return app, pane, sent


def test_the_box_toggle_sends_its_regions_choice_and_only_for_final_ops():
    app, pane, sent = _ui("every_pass_toggle")
    write_a, write_b = "a/time_loop/couple/write", "b/time_loop/couple/write"
    pane.toggle_every_pass("a/time_loop/couple/solve")  # not final: no toggle
    assert app.state.every_pass == [] and sent == []
    pane.toggle_every_pass(write_a)
    assert app.state.every_pass == [write_a] and sent[-1] == {"cmd": "every_pass", "ids": [write_a]}
    pane.toggle_every_pass(write_b)
    assert app.state.every_pass == [write_a, write_b]
    pane.toggle_every_pass(write_a)
    assert app.state.every_pass == [write_b] and sent[-1] == {"cmd": "every_pass", "ids": [write_b]}


def test_the_box_shows_the_final_flag_and_the_loop_its_final_pass():
    app, pane, _ = _ui("every_pass_box")
    from_layout = {n["id"]: n["data"] for n in _flow(app)}
    assert from_layout["a/time_loop/couple/write"]["final"] is True
    assert from_layout["a/time_loop/couple/solve"]["final"] is False
    log = {"kind": "test", "node": "a/time_loop/couple", "name": "couple", "ms": 0.1, "iter": 2,
           "stack": [["a/time_loop", 1]], "region": "a", "result": True}
    pane._on_log({**log, "final": True})
    assert app.state.loop_final == {"a/time_loop/couple": True}
    pane._on_log({**log, "iter": 3, "result": False})  # the loop is left
    assert app.state.loop_final == {}


def _flow(app):
    graph = _build_flow([], app.model.ops, "multiRun", frozenset(), "exec", ("a", "b"), frozenset())
    return [n for n in graph["nodes"] if "final" in (n.get("data") or {})]
