"""Stepping a real NeoFOAM solver gives the same fields as ``run()``.

Opt-in: needs a NeoFOAM interpreter and a sourced OpenFOAM, e.g.::

    source ~/OpenFOAM/OpenFOAM-v2406/etc/bashrc
    NEOFOAM_PYTHON=~/libsAndApps/NeoFOAM/.venv/bin/python pixi run pytest tests/test_neofoam_adapter.py
"""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

PYTHON = os.environ.get("NEOFOAM_PYTHON")
pytestmark = pytest.mark.skipif(
    not PYTHON or not os.environ.get("WM_PROJECT_DIR"),
    reason="set NEOFOAM_PYTHON and source OpenFOAM to run the NeoFOAM adapter test",
)
SRC = Path(__file__).resolve().parents[1] / "src"
SCRIPT = Path(__file__).with_name("neofoam") / "compare_runs.py"


def _pitzdaily(tmp: Path) -> Path:
    # <NeoFOAM>/.venv/bin/python — not resolved: the venv python is a symlink.
    tutorial = Path(os.path.abspath(PYTHON)).parents[2] / "tutorials" / "pitzDaily"
    case = tmp / "pitzDaily"
    shutil.copytree(tutorial, case, ignore=shutil.ignore_patterns("__pycache__", "0"))
    shutil.copytree(case / "0.orig", case / "0")
    control = case / "system" / "controlDict"
    text = control.read_text()
    text = "\n".join(
        "endTime         0.0005;" if line.startswith("endTime") else line for line in text.splitlines()
    )
    control.write_text(text + "\n")
    subprocess.run(["blockMesh"], cwd=case, check=True, capture_output=True)
    return case


def _env(**extra: str) -> dict:
    path = os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))
    return {**os.environ, "PYTHONPATH": path, **extra}


def _run(case: Path, mode: str, out: Path, **env: str) -> dict:
    subprocess.run([PYTHON, str(SCRIPT), mode, str(out)], cwd=case, env=_env(**env), check=True,
                   capture_output=True, timeout=600)
    return dict(np.load(out))


def _has_observer() -> bool:
    probe = "import neofoam.framework.execution_observer"
    return subprocess.run([PYTHON, "-c", probe], env=_env(), capture_output=True).returncode == 0


@pytest.mark.parametrize("ui_mode", ["tree", "observed"])
def test_stepping_matches_plain_run(tmp_path, ui_mode):
    if ui_mode == "observed" and not _has_observer():
        pytest.skip("this NeoFOAM has no execution observer")
    plain = _run(_pitzdaily(tmp_path / "a"), "plain", tmp_path / "plain.npz")
    stepped = _run(_pitzdaily(tmp_path / "b"), "step", tmp_path / "step.npz",
                   NEOFOAM_UI_MODE=ui_mode)
    assert set(plain) == {"U", "p", "phi"}
    for name in plain:
        np.testing.assert_array_equal(stepped[name], plain[name], err_msg=name)
