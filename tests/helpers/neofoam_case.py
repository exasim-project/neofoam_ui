"""A short pitzDaily case of the NeoFOAM tutorials, meshed, for the tests that run NeoFOAM."""

import os
import shutil
import subprocess
from pathlib import Path


def pitzdaily(tmp: Path, python: str) -> Path:
    """pitzDaily up to ``endTime 0.0005`` in ``tmp``, from the NeoFOAM of ``python``."""
    # <NeoFOAM>/.venv/bin/python — not resolved: the venv python is a symlink.
    tutorial = Path(os.path.abspath(python)).parents[2] / "tutorials" / "pitzDaily"
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
