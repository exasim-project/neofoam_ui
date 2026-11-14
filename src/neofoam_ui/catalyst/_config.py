"""Where Catalyst is, which pipeline script runs and where it publishes (config files, or the worker's)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import neofoam_ui
from neofoam_ui.config import CatalystConfig, load_config

__all__ = ()

#: The Catalyst channel the solver's cells are published on (``pipelines/view.py`` reads it).
_CHANNEL = "grid"
#: The stepper's own pipeline script, loaded by path (package root, not this module's dir).
_PIPELINE = str(Path(neofoam_ui.__file__).resolve().parent / "pipelines" / "view.py")
#: This process's settings (``None``: read the config files of the working directory) and the
#: ``host:port`` the stepper UI's data mode listens on (``None``: no data mode).
_STATE: dict[str, Any] = {"settings": None, "data_url": None}


def use(settings: Optional[CatalystConfig], data_url: Optional[str] = None) -> None:
    """Set this process's Catalyst settings; ``None``: those of the config files of the working directory.

    The worker calls it at start with the config files under its command line (``--catalyst``,
    ``--data``); a solver's own in-situ entry outside the stepper never does. Call it before the
    first :class:`InSitu`; not thread-safe.

    Example::

        catalyst.use(CatalystConfig(prefix="/opt/paraview-catalyst"), data_url="localhost:22223")
    """
    _STATE["settings"], _STATE["data_url"] = settings, data_url


def settings() -> CatalystConfig:
    """This process's Catalyst settings (see :func:`use`)."""
    if _STATE["settings"] is None:
        _STATE["settings"] = load_config().catalyst
    return _STATE["settings"]


def _data_url() -> Optional[str]:
    return _STATE["data_url"]


def configured() -> bool:
    """A Catalyst install is set."""
    return settings().prefix is not None


def custom_script() -> bool:
    """A pipeline script other than the stepper's own (it may use any field)."""
    return settings().script is not None
