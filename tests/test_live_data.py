"""The field view's data mode end to end: a solver's Catalyst publishes to Live, the UI's
listener (``_ui/_live.py``, ``helpers/live_listener.py`` under ``pvpython``) gets the extracted
source: first the ``Field`` scene's surface, then another scene's (the whole grid or a slice);
only the source asked for travels (the one the shown scene's report names), and every data
frame brings the solver's current values.

Opt-in: needs ``NEOFOAM_TEST_CATALYST`` (its ``bin/pvpython`` hosts the listener; the solver side
runs in ``NEOFOAM_PYTHON``, the interpreter ParaView was built for). Two processes, as in the
stepper: Live connects a solver to a separate ParaView."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

PREFIX = os.environ.get("NEOFOAM_TEST_CATALYST")
PVPYTHON = Path(PREFIX or "/nowhere") / "bin" / "pvpython"
pytestmark = pytest.mark.skipif(not PVPYTHON.is_file(), reason="set NEOFOAM_TEST_CATALYST to an install with pvpython")
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
SOLVER = HERE / "helpers" / "live_solver.py"
SCENES = HERE / "scenes" / "cases" / "live" / "system" / "scenes"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _said(lines):
    """The listener's JSON lines (pvpython and Live print other lines as well)."""
    return (json.loads(line) for line in lines if line.startswith("{"))


@pytest.mark.parametrize("scene, then, cells", [
    ("volume", "grid", 4),
    # normal to x at 60 % of the four cells: through the third one only, two triangles
    ("slice-x-60", "slice-x-60:cut", 2),
])
def test_the_ui_gets_the_field_surface_then_the_source_the_scene_names(scene, then, cells):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env.update(PYTHONPATH=str(SRC), HWLOC_COMPONENTS="-gl")
    listener = subprocess.Popen([str(PVPYTHON), "--venv", sys.prefix, str(HERE / "helpers" / "live_listener.py"),
                                 str(port), then], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        assert "listening" in next(_said(listener.stdout))
        solver = subprocess.run([os.environ.get("NEOFOAM_PYTHON", sys.executable), str(SOLVER), PREFIX,
                                 str(SCENES / f"{scene}.json"), f"localhost:{port}"],
                                capture_output=True, text=True, timeout=300, env=env)
    finally:
        listener.kill()
    held = list(_said(listener.stdout))
    listener.wait()
    assert solver.returncode == 0, solver.stderr[-3000:]
    # the frames name what the UI extracts: the Field scene's surface, then the scene's display input
    assert [line["extracts"] for line in _said(solver.stdout.splitlines())] == [["Field:surface"]] + [[then]] * 3
    # four unit hexahedra with their own points: 24 boundary quads, 4 cells; T = the frame's number
    assert held[0] == {"source": "Field:surface", "cells": 24, "T": [0.0, 0.0]}
    assert held[-1]["source"] == then and held[-1]["cells"] == cells and held[-1]["T"][0] > 0
