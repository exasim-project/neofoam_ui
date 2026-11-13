"""The field view's data mode end to end: a solver's Catalyst publishes to Live, the UI's
listener (``_ui/_live.py``, ``helpers/live_listener.py`` under ``pvpython``) gets the extracted
source: first the surface, then — picked in the UI — the whole grid or a slice; only the source
asked for travels, and every data frame brings the solver's current values.

Opt-in: needs ``NEOFOAM_UI_CATALYST`` (its ``bin/pvpython`` hosts the listener; the solver side
runs in ``NEOFOAM_PYTHON``, the interpreter ParaView was built for). Two processes, as in the
stepper: Live connects a solver to a separate ParaView."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from neofoam_ui.protocol import CATALYST_DATA_ENV

PREFIX = os.environ.get("NEOFOAM_UI_CATALYST")
PVPYTHON = Path(PREFIX or "/nowhere") / "bin" / "pvpython"
pytestmark = pytest.mark.skipif(not PVPYTHON.is_file(), reason="set NEOFOAM_UI_CATALYST to an install with pvpython")
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
SOLVER = HERE / "helpers" / "live_solver.py"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _said(lines):
    """The listener's JSON lines (pvpython and Live print other lines as well)."""
    return (json.loads(line) for line in lines if line.startswith("{"))


@pytest.mark.parametrize("then, options, cells", [
    ("grid", {"show": "volume"}, 4),
    # normal to x at 60 % of the four cells: through the third one only, two triangles
    ("slice", {"show": "slice", "axis": "x", "pos": "0.6"}, 2),
])
def test_the_ui_gets_the_surface_then_the_source_it_picked(then, options, cells):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env.update(PYTHONPATH=str(SRC), HWLOC_COMPONENTS="-gl")
    listener = subprocess.Popen([str(PVPYTHON), "--venv", sys.prefix, str(HERE / "helpers" / "live_listener.py"),
                                 str(port), then], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        assert "listening" in next(_said(listener.stdout))
        solver = subprocess.run([os.environ.get("NEOFOAM_PYTHON", sys.executable), str(SOLVER), PREFIX,
                                 json.dumps(options)],
                                capture_output=True, text=True, timeout=300,
                                env={**env, CATALYST_DATA_ENV: f"localhost:{port}"})
    finally:
        listener.kill()
    held = list(_said(listener.stdout))
    listener.wait()
    assert solver.returncode == 0, solver.stderr[-3000:]
    # four unit hexahedra with their own points: 24 boundary quads, 4 cells; T = the frame's number
    assert held[0] == {"source": "surface", "cells": 24, "T": [0.0, 0.0]}
    assert held[-1]["source"] == then and held[-1]["cells"] == cells and held[-1]["T"][0] > 0
