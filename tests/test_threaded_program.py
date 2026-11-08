"""A solver that drives itself (as NeoFOAM does through its execution observer)."""

import threading

import numpy as np
import pytest
from self_driven import SelfDriven

from neofoam_ui.engine import Session
from neofoam_ui.toy.heat1d import Heat1D


def events(solver, mode="into"):
    session = Session(solver, lambda m: None)
    session.start()
    seen = [session.event.to_dict()]
    while not session.finished:
        session.advance(mode)
        seen.append(session.event.to_dict())
    return session, seen


def test_same_events_and_result_as_tree_mode():
    tree, tree_events = events(Heat1D())
    driven, driven_events = events(SelfDriven())
    assert driven_events == tree_events
    assert driven.calls == tree.calls
    np.testing.assert_array_equal(driven.solver.fields(driven.ctx)["T"],
                                  tree.solver.fields(tree.ctx)["T"])


def test_init_graph_is_known_at_start():
    messages = []
    session = Session(SelfDriven(), messages.append)
    session.start()
    assert messages[0]["type"] == "graph" and messages[0]["activity"] == "init"
    assert session.event.node == "init:mesh"


def test_step_modes_work_on_a_self_driven_solver():
    session = Session(SelfDriven(), lambda m: None)
    session.start()
    session.advance("skip_init")
    session.advance("time_step")
    assert (session.event.kind, session.event.node, session.event.iter) == ("test", "time_loop", 2)


def test_error_in_thread_is_reported():
    messages = []
    session = Session(SelfDriven(fail_at="time_loop/inner_loop/solve"), messages.append)
    session.start()
    session.advance("run")
    assert session.failed
    error = next(m for m in messages if m["type"] == "error")
    assert "boom" in error["traceback"]


def test_closing_unwinds_the_solver_thread():
    solver = SelfDriven()
    session = Session(solver, lambda m: None)
    session.start()
    session._gen.close()
    solver.thread._thread.join(timeout=5)
    assert not solver.thread._thread.is_alive()


@pytest.mark.parametrize("mode", ["over", "out", "run"])
def test_other_modes_match_tree_mode(mode):
    assert events(SelfDriven(), mode)[1] == events(Heat1D(), mode)[1]


@pytest.mark.parametrize("finish", [False, True])
def test_calls_run_on_the_solver_thread(finish):
    solver = SelfDriven()
    session = Session(solver, lambda m: None)
    session.start()
    if finish:
        session.advance("run")
        assert session.finished
    names = []
    assert session.in_solver_thread(lambda: names.append(threading.current_thread().name), 5)
    assert names == ["solver"]


def test_tree_mode_calls_run_here():
    session = Session(Heat1D(), lambda m: None)
    names = []
    assert session.in_solver_thread(lambda: names.append(threading.current_thread().name))
    assert names == [threading.current_thread().name]
