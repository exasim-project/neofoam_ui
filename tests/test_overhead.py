"""What the UI costs a running solver: no checksums in free runs, no events nobody watches,
frames at their interval, output that never blocks the solver (unless "wait for UI")."""

import json
import os
import select
import subprocess
import sys
import threading
import time
import types

import pytest

from neofoam_ui.engine import _tracking as engine
from neofoam_ui.engine import UNTRACKED, Session
from neofoam_ui.toy.heat1d import Heat1D

HELPERS = os.path.join(os.path.dirname(__file__), "helpers")
sys.path.insert(0, HELPERS)
from self_driven import SelfDriven  # noqa: E402


def of(messages, kind):
    return [m for m in messages if m["type"] == kind]


@pytest.fixture
def prints(monkeypatch):
    """Counts the field checksums."""
    calls = []
    real = engine._fingerprint
    monkeypatch.setattr(engine, "_fingerprint", lambda arr: calls.append(1) or real(arr))
    return calls


# -- change tracking ------------------------------------------------------------------------


@pytest.mark.parametrize("solver", [Heat1D, SelfDriven])
def test_no_checksums_in_a_free_run_and_the_stats_say_so(prints, solver):
    messages = []
    session = Session(solver(), messages.append)
    session.start()
    session.advance("skip_init")
    session.breakpoints = {"time_loop/write_output"}
    prints.clear()
    session.advance("run")
    assert session.event.node == "time_loop/write_output" and session.calls["time_loop/inner_loop/solve"] > 0
    assert prints == []
    rows = {r["name"]: r for r in of(messages, "paused")[-1]["summary"]["fields"]}
    assert rows["T"]["written_by"] == UNTRACKED and not rows["T"]["changed"]
    seen = dict(session.writes_seen)
    session.breakpoints = set()
    session.advance("time_step")  # a free run too
    assert prints == [] and session.writes_seen == seen
    # A single step tracks again (fresh checksums first): who wrote what is known.
    session.advance("into")  # time_loop test → set_time_step
    session.advance("into")
    assert prints
    session.advance("run_to", "time_loop/inner_loop/apply_source")  # near: tracked, after solve
    rows = {r["name"]: r for r in of(messages, "paused")[-1]["summary"]["fields"]}
    assert rows["T"]["written_by"] == "solve" and rows["T"]["changed"]
    assert rows["kappa"]["written_by"] == UNTRACKED  # not written since the free run


def test_a_far_run_to_stops_tracking_at_the_second_time_step(prints):
    session = Session(Heat1D(), lambda m: None)
    session.start()
    session.advance("skip_init")
    session.advance("run_to", "time_loop/write_output")  # in this time step: tracked
    assert prints and session.stepping
    prints.clear()
    before = dict(session.calls)
    session.advance("run_to", "no/such/node")  # never there: on to the end
    assert session.finished and not session.stepping
    actions = sum(n - before.get(k, 0) for k, n in session.calls.items()
                  if k in {"time_loop/set_time_step", "time_loop/write_output"}
                  or k.startswith("time_loop/inner_loop/"))
    n_fields = len(session.solver.fields(session.ctx))
    assert 0 < len(prints) < actions * n_fields / 3  # the first time step only


# -- free runs of a self-driving solver: no hand-over per event ------------------------------


def counting(session):
    """Count the events the solver thread hands to the session."""
    queue = session.program_thread._events
    handed = []
    put = queue.put
    queue.put = lambda item, *a, **k: (handed.append(item), put(item, *a, **k))[1]
    return handed


def test_a_free_run_hands_over_only_the_time_steps():
    session = Session(SelfDriven(), lambda m: None)
    session.start()
    session.advance("skip_init")
    handed = counting(session)
    session.advance("run")
    assert session.finished
    events = sum(session.calls.values())
    steps = session.calls["time_loop"]
    assert events > 5 * steps and len(handed) <= steps + 1  # loop tests (+ the end)


def test_breakpoints_and_targets_still_stop_a_free_run():
    session = Session(SelfDriven(), lambda m: None)
    session.start()
    session.breakpoints = {"time_loop/inner_loop/solve"}
    session.advance("run")
    assert session.event.node == "time_loop/inner_loop/solve"
    session.breakpoints = set()
    session.advance("run_to", "time_loop/write_output")
    assert session.event.node == "time_loop/write_output"


def test_a_serial_pause_stops_at_the_next_event_not_the_next_time_step():
    session = None

    def emit(msg):  # the reader thread pressing Pause while the run goes
        if msg["type"] == "log" and msg["name"] == "assemble":
            session.pause_requested = True

    session = Session(SelfDriven(), emit)
    session.start()
    session.advance("skip_init")
    session.advance("run")
    assert session.event.node == "time_loop/inner_loop/solve"


# -- what a run reports: the DAG pane and the delay --------------------------------------------


def run_messages(watch, delay=0.0, solver=SelfDriven):
    """The messages of a Run, and how many events it ran."""
    messages = []
    session = Session(solver(), messages.append)
    session.start()
    session.advance("skip_init")
    session.watch, session.delay = watch, delay
    n, before = len(messages), sum(session.calls.values())
    session.advance("run")
    return session, messages[n:], sum(session.calls.values()) - before


@pytest.mark.parametrize("solver", [Heat1D, SelfDriven])
def test_with_the_dag_pane_closed_a_run_reports_no_events(solver):
    session, messages, _ = run_messages(watch=False, delay=0.05, solver=solver)  # no delay either
    assert session.finished
    assert not of(messages, "log") and not of(messages, "running")


def test_with_the_dag_pane_open_a_run_reports_its_events_and_running_now_and_then():
    session, messages, events = run_messages(watch=True)
    assert len(of(messages, "log")) == events
    assert len(of(messages, "running")) <= 3  # capped by time, not one per event


def test_a_delay_animates_every_event():
    session, messages, events = run_messages(watch=True, delay=0.001)
    assert len(of(messages, "running")) == events - 1  # every event it passed (not the end)


# -- live frames: interval and retention ---------------------------------------------------------


class FakeInSitu:
    def __init__(self):
        self.step = 0

    def execute(self, geom, fields, time, params):
        self.step += 1
        return f"view_{self.step - 1:06d}.png"


@pytest.fixture
def fake_catalyst(monkeypatch, tmp_path):
    from neofoam_ui import catalyst

    monkeypatch.setenv(catalyst.ENV, "/nowhere")
    monkeypatch.setenv(catalyst.OUT_ENV, str(tmp_path))
    monkeypatch.setattr(catalyst, "InSitu", FakeInSitu)


@pytest.mark.parametrize("every, seconds, expected", [(1, 0, "all"), (3, 0, "third"), (1, 1e9, "none")])
def test_live_frames_at_their_interval(fake_catalyst, every, seconds, expected):
    messages = []
    session = Session(Heat1D(), messages.append)
    session.start()
    session.advance("skip_init")
    session.set_view("T")
    session.frame_every, session.frame_seconds = every, seconds
    n = len(of(messages, "frame"))
    session.advance("run")
    frames = len(of(messages, "frame")) - n  # live ones, and the final pause's if values changed
    steps = session.calls["time_loop"] - 1  # the loop tests after the first
    assert frames == {"all": steps, "third": steps // 3 + 1, "none": 1}[expected]


def test_only_the_newest_images_are_kept(tmp_path):
    from neofoam_ui.catalyst import InSitu

    insitu = types.SimpleNamespace(out_dir=str(tmp_path), step=0, _pruned=0)
    for step in range(30):
        (tmp_path / f"view_{step:06d}.png").write_bytes(b"png")
        insitu.step = step + 1
        InSitu.prune(insitu, 5)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"view_{s:06d}.png" for s in range(25, 30)]


# -- post-processing rows: converted on rank 0 only -------------------------------------------------


@pytest.mark.parametrize("rank", [0, 1])
def test_post_processing_rows_are_converted_on_rank_0_only(monkeypatch, rank):
    import test_pptables  # noqa: PLC0415 - its fake PostProcessor

    from neofoam_ui.adapters.neofoam import _FieldsMixin

    model = types.ModuleType("neofoam.postprocess.model")
    model.PostProcessor = test_pptables.PostProcessor
    for name, module in (("neofoam", types.ModuleType("neofoam")),
                         ("neofoam.postprocess", types.ModuleType("neofoam.postprocess")),
                         ("neofoam.postprocess.model", model)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("OMPI_COMM_WORLD_RANK", str(rank))
    monkeypatch.setenv("OMPI_COMM_WORLD_SIZE", "2")
    pp, messages = test_pptables.processor(), []
    _FieldsMixin().observe(pp, messages.append)
    pp.write(test_pptables.Ctx(test_pptables.Time(0.1, 1)))
    kinds = [m["type"] for m in messages]
    assert kinds == (["pptables", "pptable"] if rank == 0 else [])
    assert len(pp.writers["p_mean"].rows) == 1  # the CSV is written either way


# -- worker output ----------------------------------------------------------------------------------


def test_thinning_keeps_every_critical_message_in_order():
    from neofoam_ui.worker import LOSSY, thin

    batch = []
    for i in range(300):
        batch.append({"type": "log", "i": i})
        batch.append({"type": "stdout", "text": str(i)})
        if i % 50 == 0:
            batch += [{"type": "pptable", "i": i}, '{"type": "frame"}']
    batch.append('{"type": "paused"}')
    out = thin(batch)
    critical = [m for m in batch if isinstance(m, str) or m["type"] == "pptable"]
    assert [m for m in out if isinstance(m, str) or m["type"] == "pptable"] == critical
    logs = [m["i"] for m in out if isinstance(m, dict) and m["type"] == "log"]
    assert logs == list(range(300 - LOSSY["log"], 300))  # the newest
    (skipped,) = [m for m in out if isinstance(m, dict) and m["type"] == "skipped"]
    assert skipped == {"type": "skipped", "log": 300 - LOSSY["log"], "stdout": 300 - LOSSY["stdout"]}
    assert out[-1] == '{"type": "paused"}'
    assert thin(batch, on=False) == batch


class SlowStream:
    """A UI that takes 50 ms per read."""

    def __init__(self):
        self.lines = []

    def write(self, text):
        time.sleep(0.05)
        self.lines += text.splitlines()

    def flush(self):
        pass


def test_a_slow_ui_does_not_block_the_solver():
    from neofoam_ui.worker import _Out

    stream = SlowStream()
    out = _Out(stream)
    t0 = time.perf_counter()
    for i in range(2000):
        out({"type": "log", "i": i})
        if i % 100 == 0:
            out({"type": "pptable", "i": i})
    out({"type": "paused"})
    assert time.perf_counter() - t0 < 0.5  # 2000 blocking writes would take 100 s
    out.drain()
    msgs = [json.loads(line) for line in stream.lines]
    assert [m["i"] for m in msgs if m["type"] == "pptable"] == list(range(0, 2000, 100))
    assert msgs[-1]["type"] == "paused" and of(msgs, "skipped")
    assert [m["i"] for m in of(msgs, "log")][-1] == 1999


class LineReader:
    """Non-blocking lines from a pipe: what arrived within ``timeout``."""

    def __init__(self, pipe):
        self.fd, self.buf, self.pending = pipe.fileno(), b"", []

    def lines(self, timeout):
        out, end = self.pending, time.monotonic() + timeout
        self.pending = []
        if out:
            return out
        while True:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                return out
            chunk = os.read(self.fd, 1 << 16)
            if not chunk:
                return out
            self.buf += chunk
            *done, self.buf = self.buf.split(b"\n")
            out += [json.loads(line) for line in done if line.strip()]

    def until(self, *kinds, timeout=30):
        seen, end = [], time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.lines(0.2)
            for i, msg in enumerate(got):
                seen.append(msg)
                if msg["type"] in kinds:
                    self.pending = got[i + 1:] + self.pending
                    return seen
        raise AssertionError(f"no {kinds!r} within {timeout} s: {[m['type'] for m in seen]}")


def _worker():
    return subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", "self_driven:SelfDriven"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": os.pathsep.join(
                                [HELPERS, os.environ.get("PYTHONPATH", "")])})


def test_wait_for_ui_blocks_the_solver_until_acknowledged_and_drops_nothing():
    proc = _worker()
    reader = LineReader(proc.stdout)

    def send(cmd):
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

    try:
        reader.until("paused")
        send({"cmd": "wait", "on": True})
        send({"cmd": "step", "mode": "skip_init"})
        reader.until("paused")
        send({"cmd": "step", "mode": "run"})
        seen = []
        while not seen or seen[-1]["type"] != "paused":
            seen += reader.until("sync", "paused")
            if seen[-1]["type"] == "sync":
                assert reader.lines(0.3) == []  # the solver waits for the ack: nothing meanwhile
                send({"cmd": "ack", "seq": seen[-1]["seq"]})
        assert seen[-1]["type"] == "paused" and seen[-1]["finished"]
        assert not of(seen, "skipped")
        calls = seen[-1]["calls"]
        assert len(of(seen, "log")) == sum(calls.values()) - sum(v for k, v in calls.items() if k.startswith("init:"))
        assert len(of(seen, "sync")) == calls["time_loop"] - 1  # every time step after the first
        send({"cmd": "stop"})
        reader.until("stopped")
    finally:
        proc.kill()
        proc.wait()


def test_without_wait_the_solver_runs_ahead_of_a_ui_that_does_not_read():
    proc = _worker()
    reader = LineReader(proc.stdout)
    try:
        reader.until("paused")
        proc.stdin.write(json.dumps({"cmd": "step", "mode": "run"}) + "\n")
        proc.stdin.flush()
        time.sleep(1.0)  # nobody reads: the run must finish regardless
        seen = reader.until("paused")
        assert seen[-1]["finished"] and not of(seen, "sync")
    finally:
        proc.kill()
        proc.wait()


def test_followers_pass_the_events_of_a_free_run_alike(monkeypatch):
    """Rank 0 and a follower, self-driven (as NeoFOAM), stay in step through free runs."""
    from test_parallel import _port

    from neofoam_ui.parallel import SYNC_ENV

    port = _port()

    def worker(rank, **kw):
        env = {**os.environ, "OMPI_COMM_WORLD_RANK": str(rank), "OMPI_COMM_WORLD_SIZE": "2",
               SYNC_ENV: f"127.0.0.1:{port}",
               "PYTHONPATH": os.pathsep.join([HELPERS, os.environ.get("PYTHONPATH", "")])}
        return subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", "self_driven:RankSelfDriven"],
                                env=env, text=True, **kw)

    root = worker(0, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    follower = worker(1, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    reader = LineReader(root.stdout)

    def send(cmd):
        root.stdin.write(json.dumps(cmd) + "\n")
        root.stdin.flush()

    def mean_T(msg):
        return next(r for r in msg["summary"]["fields"] if r["name"] == "T")["mean"]

    try:
        reader.until("paused")
        send({"cmd": "breakpoints", "ids": ["time_loop/write_output"]})
        send({"cmd": "step", "mode": "run"})
        at_bp = reader.until("paused")[-1]
        assert at_bp["event"]["node"] == "time_loop/write_output"
        send({"cmd": "breakpoints", "ids": []})
        send({"cmd": "step", "mode": "into"})  # single steps after a free run: still in step
        send({"cmd": "step", "mode": "into"})
        reader.until("paused")
        msg = reader.until("paused")[-1]
        assert (msg["event"]["kind"], msg["event"]["node"]) == ("test", "time_loop")  # past post_process
        send({"cmd": "step", "mode": "run"})
        end = reader.until("paused")[-1]
        assert end["finished"] and not end["failed"]

        from self_driven import RankSelfDriven

        means = []
        for rank in (0, 1):  # each rank's run in-process: the global mean is theirs
            monkeypatch.setenv("OMPI_COMM_WORLD_RANK", str(rank))
            monkeypatch.setenv("OMPI_COMM_WORLD_SIZE", "2")
            local = Session(RankSelfDriven(), lambda m: None)
            local.start()
            local.advance("run")
            means.append(float(local.solver.fields(local.ctx)["T"].mean()))
        assert abs(mean_T(end) - sum(means) / 2) < 1e-9
        send({"cmd": "stop"})
        assert follower.wait(timeout=20) == 0
        assert root.wait(timeout=20) == 0
    finally:
        for p in (root, follower):
            p.kill()
            p.wait()


def test_followers_honour_a_pause_at_the_next_time_step():
    """Under MPI a Pause in a free run takes effect at the next time step, on every rank."""
    from test_parallel import _port

    from neofoam_ui.parallel import SYNC_ENV

    port = _port()

    def worker(rank, **kw):
        env = {**os.environ, "OMPI_COMM_WORLD_RANK": str(rank), "OMPI_COMM_WORLD_SIZE": "2",
               SYNC_ENV: f"127.0.0.1:{port}", "SELF_DRIVEN_SLEEP": "0.02",
               "PYTHONPATH": os.pathsep.join([HELPERS, os.environ.get("PYTHONPATH", "")])}
        return subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", "self_driven:RankSelfDriven"],
                                env=env, text=True, **kw)

    root = worker(0, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    follower = worker(1, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    reader = LineReader(root.stdout)

    def send(cmd):
        root.stdin.write(json.dumps(cmd) + "\n")
        root.stdin.flush()

    try:
        reader.until("paused")
        send({"cmd": "step", "mode": "run"})
        threading.Event().wait(0.3)
        send({"cmd": "pause"})
        msg = reader.until("paused")[-1]
        assert not msg["finished"]
        assert msg["event"]["kind"] == "test" and msg["event"]["depth"] == 0  # a time step
        send({"cmd": "step", "mode": "run"})  # and on, together
        assert reader.until("paused")[-1]["finished"]
        send({"cmd": "stop"})
        assert follower.wait(timeout=20) == 0
        assert root.wait(timeout=20) == 0
    finally:
        for p in (root, follower):
            p.kill()
            p.wait()


def test_stepping_over_the_time_loop_is_a_free_run(prints):
    session = Session(SelfDriven(), lambda m: None)
    session.start()
    session.advance("skip_init")
    handed = counting(session)
    prints.clear()
    session.advance("over")  # the whole time loop: to the end
    assert session.finished and prints == [] and len(handed) <= session.calls["time_loop"] + 1
