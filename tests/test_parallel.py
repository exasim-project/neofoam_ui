"""Rank 0 and a follower in lockstep, without MPI: the launcher's rank variables are faked."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import numpy as np
from rank_toy import RankHeat

from neofoam_ui.engine import Session
from neofoam_ui.parallel import rank_info

HELPERS = Path(__file__).with_name("helpers")
SPEC = "rank_toy:RankHeat"


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _worker(rank, size, port, **kw):
    env = {**os.environ, "OMPI_COMM_WORLD_RANK": str(rank), "OMPI_COMM_WORLD_SIZE": str(size),
           "PYTHONPATH": os.pathsep.join([str(HELPERS), os.environ.get("PYTHONPATH", "")])}
    return subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", SPEC, "--sync", f"127.0.0.1:{port}"], env=env,
                            text=True, **kw)


def test_rank_info_from_environment(monkeypatch):
    for var in ("OMPI_COMM_WORLD_RANK", "OMPI_COMM_WORLD_SIZE", "PMIX_RANK", "PMIX_SIZE",
                "PMI_RANK", "PMI_SIZE"):
        monkeypatch.delenv(var, raising=False)
    assert rank_info() == (0, 1)
    monkeypatch.setenv("PMI_RANK", "3")
    monkeypatch.setenv("PMI_SIZE", "4")
    assert rank_info() == (3, 4)


def test_follower_steps_in_lockstep_and_stats_are_global(monkeypatch):
    port = _port()
    root = _worker(0, 2, port, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    follower = _worker(1, 2, port, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def send(cmd):
        root.stdin.write(json.dumps(cmd) + "\n")
        root.stdin.flush()

    def paused():
        for line in root.stdout:
            msg = json.loads(line)
            if msg["type"] == "paused":
                return msg
        raise AssertionError("rank 0 ended early")

    try:
        paused()
        send({"cmd": "step", "mode": "skip_init"})
        first = paused()
        assert first["event"]["node"] == "time_loop"
        send({"cmd": "breakpoints", "ids": ["time_loop/write_output"]})
        send({"cmd": "step", "mode": "run"})
        msg = paused()
        assert msg["event"]["node"] == "time_loop/write_output"

        # The same run in-process, for the per-rank values.

        local = Session(RankHeat(), lambda m: None)
        local.start()
        local.breakpoints = {"time_loop/write_output"}
        local.advance("run")
        T = local.solver.fields(local.ctx)["T"]  # rank 0's values; rank 1 has T + 100
        row = next(r for r in msg["summary"]["fields"] if r["name"] == "T")
        assert row["min"] == float(T.min())
        assert row["max"] == float(T.max()) + 100.0  # the follower is at the same event
        assert np.isclose(row["mean"], float(T.mean()) + 50.0)

        send({"cmd": "stop"})
        assert follower.wait(timeout=20) == 0
        assert root.wait(timeout=20) == 0
    finally:
        for p in (root, follower):
            p.kill()
            p.wait()
