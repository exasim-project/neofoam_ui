"""A NeoFOAM case's ``catalyst`` in-situ entry renders from inside the running solver.

The case (``tests/neofoam/cases/insitu_view``) asks for ``p`` at every time step of a
pitzDaily up to 5e-4 (adjustable deltaT, so about six steps: the frames are counted by their
times, which must rise step by step to the end time or past it). Under the stepper the solver's
``insitu`` operation draws through the field view, so the frames reach the UI while a free
run goes on, with no view open; Catalyst is faked inside the worker for that
(``helpers/fake_catalyst_fluid.py``). The same entry
without the stepper writes images of its own, which needs a real Catalyst + ParaView.

Opt-in: needs a NeoFOAM interpreter (``NEOFOAM_PYTHON``) and a sourced OpenFOAM; the image
tests also ``NEOFOAM_UI_CATALYST``. The worker is a subprocess: one solver per process.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from neofoam_case import pitzdaily

PYTHON = os.environ.get("NEOFOAM_PYTHON")
CATALYST = os.environ.get("NEOFOAM_UI_CATALYST")
pytestmark = pytest.mark.skipif(
    not PYTHON or not os.environ.get("WM_PROJECT_DIR"),
    reason="set NEOFOAM_PYTHON and source OpenFOAM to run NeoFOAM",
)
needs_catalyst = pytest.mark.skipif(not CATALYST, reason="set NEOFOAM_UI_CATALYST to render with Catalyst")
TESTS = Path(__file__).resolve().parent
SRC = TESTS.parent / "src"
CASE = TESTS / "neofoam" / "cases" / "insitu_view"
#: The case's end time (``helpers/neofoam_case.py``).
END_TIME = 5e-4


def _case(tmp: Path) -> Path:
    case = pitzdaily(tmp, PYTHON)
    shutil.copytree(CASE, case, dirs_exist_ok=True)
    return case


def _env(**extra: str) -> dict:
    path = os.pathsep.join(filter(None, [str(SRC), str(TESTS / "helpers"), os.environ.get("PYTHONPATH")]))
    return {**os.environ, "PYTHONPATH": path, **extra}


def _run_worker(case: Path, solver: str, **env: str) -> list[dict]:
    """Every message of a worker that runs ``solver`` to the end."""
    proc = subprocess.Popen([PYTHON, "-u", "-m", "neofoam_ui.worker", solver], cwd=case, env=_env(**env),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    proc.stdin.write(json.dumps({"cmd": "step", "mode": "run"}) + "\n")
    proc.stdin.flush()
    messages = []
    for line in proc.stdout:
        messages.append(json.loads(line))
        if messages[-1]["type"] == "paused" and messages[-1]["finished"]:
            break
    proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
    proc.stdin.flush()
    proc.wait(timeout=60)
    return messages


def _frames(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["type"] == "frame"]


def test_the_solver_operation_sends_a_frame_per_time_step_while_running(tmp_path):
    messages = _run_worker(_case(tmp_path), "fake_catalyst_fluid:FakeViewFluid")
    times = [f["time"] for f in _frames(messages)]
    assert len(times) > 1 and times == sorted(set(times)) and times[-1] >= END_TIME


def test_the_solver_operation_draws_the_field_of_the_entry(tmp_path):
    messages = _run_worker(_case(tmp_path), "fake_catalyst_fluid:FakeViewFluid")
    assert {f["field"] for f in _frames(messages)} == {"p"}


def test_the_insitu_operation_is_a_node_of_the_graph(tmp_path):
    messages = _run_worker(_case(tmp_path), "fake_catalyst_fluid:FakeViewFluid")
    graph = next(m for m in messages if m["type"] == "graph" and m["activity"] == "exec")
    time_loop = next(op for op in graph["ops"] if op["name"] == "time_loop")
    assert [op["name"] for op in time_loop["children"]][-2:] == ["post_process", "insitu"]


@needs_catalyst
def test_the_stepper_receives_catalyst_images_while_running(tmp_path):
    out = tmp_path / "images"
    messages = _run_worker(_case(tmp_path), "neofoam_ui.adapters.neofoam:incompressible_fluid",
                           NEOFOAM_UI_CATALYST_OUT=str(out))
    frames = _frames(messages)
    assert frames and all((out / f["file"]).is_file() for f in frames[-3:])
    assert "pipeline" in frames[0]


@needs_catalyst
def test_a_plain_solver_run_writes_the_images_of_the_entry(tmp_path):
    case = _case(tmp_path)
    run = ("from neofoam_ui.adapters.neofoam import register_insitu\n"
           "register_insitu()\n"
           "from neofoam.solver.incompressibleFluid.incompressibleFluid import run\n"
           "run(['neofoam'])\n")
    # No check of the exit code: NeoFOAM has no end-of-run hook to finalize Catalyst before
    # OpenFOAM finalizes MPI, so ParaView's static destructors crash the interpreter's exit
    # (the stepper's worker exits without them).
    subprocess.run([PYTHON, "-c", run], cwd=case, env=_env(), capture_output=True, timeout=600)
    images = sorted(p.name for p in (case / "postProcessing" / "view").glob("view_*.png"))
    assert len(images) > 1 and images[0] == "view_000000.png"
