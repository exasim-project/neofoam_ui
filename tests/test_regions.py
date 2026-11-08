"""A multi-region solver (NeoFOAM's multiRun): every region on its own thread, stepped all
together; a region blocked on its partner's data shows as waiting (no neofoam needed)."""

import os
import sys
import time

import pytest

from neofoam_ui.engine import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "helpers"))
from two_regions import TwoRegions  # noqa: E402


def _where(session):
    return {r.region: (r.state, r.event.node if r.event else None, r.waiting_for)
            for r in session.event.regions}


def _session(**kw):
    messages = []
    session = Session(TwoRegions(**kw), messages.append)
    session.start()
    return session, messages


def test_both_regions_start_paused_at_their_first_init_step():
    session, messages = _session()
    assert _where(session) == {"a": ("paused", "init:a.fields", None), "b": ("paused", "init:b.fields", None)}
    paused = next(m for m in messages if m["type"] == "paused")
    assert [r["region"] for r in paused["event"]["regions"]] == ["a", "b"]


def test_into_steps_every_region_and_a_region_waits_for_its_partner():
    session, _ = _session()
    seen = []
    for _ in range(5):
        session.advance("into")
        seen.append(_where(session))
    assert seen == [
        {"a": ("paused", "a/time_loop", None), "b": ("paused", "b/time_loop", None)},
        {"a": ("paused", "a/time_loop/set_time_step", None), "b": ("paused", "b/time_loop/set_time_step", None)},
        # b has one op more (prep): a reaches its coupling op first
        {"a": ("paused", "a/time_loop/a.couple", None), "b": ("paused", "b/time_loop/prep", None)},
        # a's test sent its data and waits for b's; the step does not wait for it
        {"a": ("waiting", "a/time_loop/a.couple", "b"), "b": ("paused", "b/time_loop/b.couple", None)},
        # b's test sends: a's data arrived, both move on
        {"a": ("paused", "a/time_loop/a.couple/solve", None), "b": ("paused", "b/time_loop/b.couple/solve", None)},
    ]


def test_the_paused_message_shows_each_region_and_who_it_waits_for():
    session, messages = _session()
    for _ in range(4):
        session.advance("into")
    regions = {r["region"]: r for r in messages[-1]["event"]["regions"]}
    assert messages[-1]["type"] == "paused"
    assert (regions["a"]["state"], regions["a"]["waiting_for"]) == ("waiting", "b")
    assert regions["b"]["event"]["node"] == "b/time_loop/b.couple"
    assert messages[-1]["event"]["region"] == "b"  # the region paused is the one in focus


def test_each_region_logs_its_own_events_with_its_region():
    session, messages = _session()
    for _ in range(3):
        session.advance("into")
    logs = [(m["region"], m["node"]) for m in messages if m["type"] == "log" and m["kind"] == "exec"]
    assert sorted(logs) == [("a", "a/time_loop/set_time_step"), ("b", "b/time_loop/set_time_step")]
    graph = [m for m in messages if m["type"] == "graph" and m["activity"] == "exec"][-1]
    assert [(op["id"], op["region"]) for op in graph["ops"]] == [("a/time_loop", "a"), ("b/time_loop", "b")]


@pytest.mark.parametrize("mode", ["run", "time_step", "over"])
def test_free_runs_take_every_region_to_the_end(mode):
    session, _ = _session()
    session.advance("skip_init")
    for _ in range(20):
        if session.finished:
            break
        session.advance(mode)
    assert session.finished and not session.failed
    assert session.summary()["scalars"]["a.time"] == 1.0
    assert {r: c.models["solves"] for r, c in session.ctx.items()} == {"a": 2, "b": 2}


def test_a_breakpoint_in_one_region_stops_all():
    session, messages = _session()
    session.breakpoints = {"b/time_loop/b.couple/solve"}
    session.advance("run")
    where = _where(session)
    assert where["b"] == ("paused", "b/time_loop/b.couple/solve", None)
    assert where["a"][0] in ("paused", "waiting")
    assert {"type": "breakpoint", "node": "b/time_loop/b.couple/solve"} in messages
    assert session.event.node == "b/time_loop/b.couple/solve"


def test_run_to_a_node_of_one_region():
    session, _ = _session()
    session.advance("run_to", "a/time_loop/a.couple/solve")
    assert _where(session)["a"] == ("paused", "a/time_loop/a.couple/solve", None)


def test_an_error_in_one_region_ends_the_session_with_it():
    session, messages = _session(fail_at=("b", 2))
    t0 = time.monotonic()
    session.advance("run")
    assert session.failed and session.finished
    error = next(m for m in messages if m["type"] == "error")
    assert "boom in b" in error["traceback"]
    assert time.monotonic() - t0 < 5  # a's thread, held or waiting, did not keep it open


def test_an_error_while_the_partner_is_held_ends_the_session_at_once():
    session, messages = _session(fail_at=("b", 1))
    session.breakpoints = {"a/time_loop/a.couple/solve"}
    session.advance("run")  # a stops at its breakpoint; b fails in its first solve
    for _ in range(5):
        if session.finished:
            break
        session.advance("into")
    assert session.failed
    assert "boom in b" in next(m for m in messages if m["type"] == "error")["traceback"]


def test_writes_are_tracked_per_region():
    session, _ = _session()
    for _ in range(6):
        session.advance("into")  # past both regions' first solve
    assert session.writes_seen["a/time_loop/a.couple/solve"] == {"a.x"}
    assert session.writes_seen["b/time_loop/b.couple/solve"] == {"b.x"}


def test_closing_unwinds_every_region_thread():
    session, _ = _session()
    session.advance("into")
    solver = session.solver
    session._gen.close()
    solver.thread._thread.join(timeout=5)
    assert not solver.thread._thread.is_alive()


def test_a_call_runs_on_a_held_region_thread():
    import threading  # noqa: PLC0415

    session, _ = _session()
    names = []
    assert session.in_solver_thread(lambda: names.append(threading.current_thread().name), 5)
    assert names[0] in ("a", "b")
