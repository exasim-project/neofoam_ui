"""Where Catalyst is, where its images go, and which pipeline script runs (environment)."""

from __future__ import annotations

import os
from pathlib import Path

import neofoam_ui
from neofoam_ui.protocol import CATALYST_ENV as ENV
from neofoam_ui.protocol import CATALYST_OUT_ENV as OUT_ENV
from neofoam_ui.protocol import CATALYST_SCRIPT_ENV as SCRIPT_ENV

__all__ = ()

#: The Catalyst channel the solver's cells are published on (``pipelines/view.py`` reads it).
_CHANNEL = "grid"
#: The stepper's own pipeline script, loaded by path (package root, not this module's dir).
_PIPELINE = str(Path(neofoam_ui.__file__).resolve().parent / "pipelines" / "view.py")


def configured() -> bool:
    """A Catalyst install and an image directory are set."""
    return bool(os.environ.get(ENV)) and bool(os.environ.get(OUT_ENV))


def custom_script() -> bool:
    """A pipeline script other than the stepper's own (it may use any field)."""
    return bool(os.environ.get(SCRIPT_ENV))
