import numpy as np
import pytest

from neofoam_ui.engine import InitStep, Session, topological_order
from neofoam_ui.toy.heat1d import Heat1D, run_plain


def make_session():
    messages = []
    session = Session(Heat1D(), messages.append)
    session.start()
    return session, messages


def ev(session):
    e = session.event
    return (e.kind, e.node, e.iter)


def test_starts_paused_before_first_init_step():
    session, messages = make_session()
    assert ev(session) == ("init", "init:mesh", None)
    assert messages[0]["type"] == "graph" and messages[0]["activity"] == "init"
    assert [s["id"] for s in messages[0]["steps"]][:2] == ["init:mesh", "init:controls"]
    assert messages[-1]["type"] == "paused"


def test_init_order_respects_dependencies():
    names = [s.name for s in topological_order(Heat1D().init_steps())]
    assert names.index("mesh") < names.index("fields.T") < names.index("models.heatSource")


def test_init_cycle_is_reported():
    steps = [InitStep("a", lambda r: 0, ["b"]), InitStep("b", lambda r: 0, ["a"])]
    with pytest.raises(ValueError, match="cycle"):
        topological_order(steps)


def test_step_into_walks_init_then_loop_test_then_body():
    session, messages = make_session()
    for _ in range(5):
        session.advance("into")
    assert ev(session) == ("test", "time_loop", 1)
    assert any(m["type"] == "graph" and m["activity"] == "exec" for m in messages)
    session.advance("into")
    assert ev(session) == ("exec", "time_loop/set_time_step", None)
    assert session.event.stack == (("time_loop", 1),)


def test_skip_init_and_step_over_whole_inner_loop():
    session, _ = make_session()
    session.advance("skip_init")
    assert ev(session) == ("test", "time_loop", 1)
    session.advance("into")  # time_loop test → set_time_step
    for _ in range(3):
        session.advance("over")  # set_time_step, increment_time, store_old_time
    assert ev(session) == ("test", "time_loop/inner_loop", 1)
    session.advance("over")  # the whole corrector loop
    assert ev(session) == ("exec", "time_loop/write_output", None)
    assert session.calls["time_loop/inner_loop/solve"] == 2


def test_step_out_finishes_loop_iteration():
    session, _ = make_session()
    session.advance("run_to", "time_loop/inner_loop/assemble")
    assert ev(session) == ("exec", "time_loop/inner_loop/assemble", None)
    session.advance("out")
    assert ev(session) == ("test", "time_loop/inner_loop", 2)


def test_breakpoint_stops_run_and_is_reported():
    session, messages = make_session()
    session.breakpoints = {"time_loop/inner_loop/solve"}
    session.advance("run")
    assert ev(session) == ("exec", "time_loop/inner_loop/solve", None)
    assert messages[-2] == {"type": "breakpoint", "node": "time_loop/inner_loop/solve"}
    session.advance("run")  # second outer corrector hits it again
    assert session.event.stack == (("time_loop", 1), ("time_loop/inner_loop", 2))


def test_next_time_step():
    session, _ = make_session()
    session.advance("time_step")
    assert ev(session) == ("test", "time_loop", 1)
    session.advance("time_step")
    assert ev(session) == ("test", "time_loop", 2)
    assert session.ctx.time_index == 1


def test_stepping_gives_same_result_as_plain_run():
    session, messages = make_session()
    while not session.finished:
        session.advance("into")
    ref = run_plain()
    np.testing.assert_array_equal(session.ctx.fields["T"], ref.fields["T"])
    assert session.ctx.time == pytest.approx(0.05)
    assert any(m["type"] == "stdout" or m.get("kind") == "exec" for m in messages)


def test_written_by_tracks_observed_writes():
    session, messages = make_session()
    session.advance("run_to", "time_loop/inner_loop/apply_source")  # paused after solve
    rows = {r["name"]: r for r in messages[-1]["summary"]["fields"]}
    assert rows["T"]["written_by"] == "solve" and rows["T"]["changed"]
    assert rows["flux"]["written_by"] == "assemble" and not rows["flux"]["changed"]
    assert rows["kappa"]["written_by"] == "init"


def test_error_in_op_is_reported():
    solver = Heat1D()
    orig = solver.execution_graph

    def broken(ctx):
        ops = orig(ctx)
        ops[0].children[0].func = lambda ctx: 1 / 0
        return ops

    solver.execution_graph = broken
    messages = []
    session = Session(solver, messages.append)
    session.advance("run")
    assert session.failed and session.finished
    err = next(m for m in messages if m["type"] == "error")
    assert "ZeroDivisionError" in err["traceback"]
