"""Every test sees no user config file (``XDG_CONFIG_HOME`` is an empty directory, also for its
worker subprocesses) and starts with Catalyst settings read from the config files.

The case wizard and the agent import NeoFOAM in-process, so their tests run only in an
environment with ``neofoam`` installed (e.g. NeoFOAM's venv); elsewhere they are not collected."""

from importlib import metadata

import pytest

from neofoam_ui import catalyst


def _neofoam_installed() -> bool:
    """True when the ``neofoam`` distribution is installed (``tests/neofoam/`` is no package of it)."""
    try:
        metadata.distribution("neofoam")
    except metadata.PackageNotFoundError:
        return False
    return True


collect_ignore = [] if _neofoam_installed() else ["wizard", "agent"]


@pytest.fixture(autouse=True)
def no_user_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    catalyst.use(None)
    yield
    catalyst.use(None)
