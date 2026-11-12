"""Stepping a real NeoFOAM solver gives the same fields as ``run()``.

Opt-in: needs a NeoFOAM interpreter and a sourced OpenFOAM, e.g.::

    source ~/OpenFOAM/OpenFOAM-v2406/etc/bashrc
    NEOFOAM_PYTHON=~/libsAndApps/NeoFOAM/.venv/bin/python pixi run pytest tests/test_neofoam_adapter.py
"""

import csv
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import numpy as np
import pytest
from neofoam_case import pitzdaily

PYTHON = os.environ.get("NEOFOAM_PYTHON")
pytestmark = pytest.mark.skipif(
    not PYTHON or not os.environ.get("WM_PROJECT_DIR"),
    reason="set NEOFOAM_PYTHON and source OpenFOAM to run the NeoFOAM adapter test",
)
SRC = Path(__file__).resolve().parents[1] / "src"
SCRIPT = Path(__file__).with_name("neofoam") / "compare_runs.py"


def _pitzdaily(tmp: Path) -> Path:
    return pitzdaily(tmp, PYTHON)


def _env(**extra: str) -> dict:
    path = os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))
    return {**os.environ, "PYTHONPATH": path, **extra}


def _run(case: Path, mode: str, out: Path, **env: str) -> dict:
    subprocess.run([PYTHON, str(SCRIPT), mode, str(out)], cwd=case, env=_env(**env), check=True,
                   capture_output=True, timeout=600)
    return dict(np.load(out))


@pytest.mark.parametrize("step", ["step", "run"])
def test_stepping_matches_plain_run(tmp_path, step):
    plain = _run(_pitzdaily(tmp_path / "a"), "plain", tmp_path / "plain.npz")
    stepped = _run(_pitzdaily(tmp_path / "b"), step, tmp_path / "step.npz")
    assert set(plain) == {"U", "p", "phi"}
    for name in plain:
        np.testing.assert_array_equal(stepped[name], plain[name], err_msg=name)


def _has_postprocess() -> bool:
    probe = "import neofoam.postprocess.model"
    return subprocess.run([PYTHON, "-c", probe], env=_env(), capture_output=True).returncode == 0


def test_post_processing_tables_reach_the_ui(tmp_path):
    """The example tables (examples/pitzDaily): a diagram each, and rows equal to the CSVs."""
    if not _has_postprocess():
        pytest.skip("this NeoFOAM has no post-processing tables")
    case = _pitzdaily(tmp_path)
    examples = Path(__file__).resolve().parents[1] / "examples" / "pitzDaily" / "system"
    for spec in examples.glob("postProcess.*"):
        shutil.copy(spec, case / "system")
    commands = json.dumps({"cmd": "step", "mode": "run"}) + "\n"
    proc = subprocess.Popen([PYTHON, "-u", "-m", "neofoam_ui.worker",
                             "neofoam_ui.adapters.neofoam:incompressible_fluid"],
                            cwd=case, env=_env(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    proc.stdin.write(commands)
    proc.stdin.flush()
    messages = []
    for line in proc.stdout:
        msg = json.loads(line)
        messages.append(msg)
        if msg["type"] == "paused" and msg["finished"]:
            break
    proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
    proc.stdin.flush()
    proc.wait(timeout=60)

    declared = [m for m in messages if m["type"] == "pptables"]
    assert len(declared) == 1
    names = {t["name"] for t in declared[0]["tables"]}
    assert {"p_mean", "recirculation_Ux", "magU_along_x", "pressure_force_lowerWall"} <= names
    rows = [m for m in messages if m["type"] == "pptable"]
    for name in names:
        mine = [m for m in rows if m["name"] == name]
        with open(case / "postProcessing" / f"{name}.csv") as handle:
            lines = list(csv.reader(handle))[1:]
        last = mine[-1]
        n_rows = len(last["values"])
        assert len(lines) == len(mine) * n_rows, name
        # the CSV puts the group labels first; the values follow
        for row, labels, line in zip(last["values"], last["groups"], lines[-n_rows:]):
            assert [float(v) for v in line[1 + len(labels):]] == pytest.approx(row, rel=1e-12), name


def test_mpi_free_runs_keep_the_ranks_in_step(tmp_path):
    """mpirun -np 2: free runs (events passed on each rank), a breakpoint, a Pause,
    single steps; every pause has the stats of both ranks, and the run ends cleanly."""
    if not shutil.which("mpirun"):
        pytest.skip("needs mpirun")
    case = _pitzdaily(tmp_path)
    for entry, value in (("numberOfSubdomains", "2"), ("method", "scotch")):
        subprocess.run(["foamDictionary", "-entry", entry, "-set", value, "system/decomposeParDict"],
                       cwd=case, check=True, capture_output=True)
    subprocess.run(["decomposePar", "-force"], cwd=case, check=True, capture_output=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(["mpirun", "-np", "2", PYTHON, "-u", "-m", "neofoam_ui.worker",
                             "neofoam_ui.adapters.neofoam:incompressible_fluid"],
                            cwd=case, env=_env(NEOFOAM_UI_SYNC=f"127.0.0.1:{port}"),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=open(tmp_path / "stderr", "w"), text=True)
    graph = []

    def send(cmd):
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

    def paused():
        for line in proc.stdout:
            msg = json.loads(line)
            if msg["type"] == "graph" and msg["activity"] == "exec":
                graph.extend(msg["ops"])
            if msg["type"] == "paused":
                return msg
        raise AssertionError("the worker ended")

    try:
        paused()
        send({"cmd": "step", "mode": "skip_init"})
        paused()
        loop = next(op for op in graph if op["kind"] == "loop")
        inner = [c for c in loop["children"] if c["kind"] == "action"][-1]  # late in a time step
        send({"cmd": "breakpoints", "ids": [inner["id"]]})
        send({"cmd": "step", "mode": "run"})
        bp = paused()
        send({"cmd": "breakpoints", "ids": []})
        send({"cmd": "step", "mode": "time_step"})
        step = paused()
        send({"cmd": "step", "mode": "into"})
        into = paused()
        send({"cmd": "step", "mode": "run"})
        end = paused()
        assert bp["event"]["node"] == inner["id"]
        assert step["event"]["kind"] == "test" and step["event"]["depth"] == 0
        assert into["event"]["node"] != step["event"]["node"]
        assert end["finished"] and not end["failed"]
        send({"cmd": "stop"})
        assert proc.wait(timeout=60) == 0
        assert "no longer followed" not in (tmp_path / "stderr").read_text()  # both ranks in step
    finally:
        proc.kill()
        proc.wait()

