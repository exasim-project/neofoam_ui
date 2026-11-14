"""Every test sees no user config file (``XDG_CONFIG_HOME`` is an empty directory, also for its
worker subprocesses) and starts with Catalyst settings read from the config files."""

import pytest

from neofoam_ui import catalyst


@pytest.fixture(autouse=True)
def no_user_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    catalyst.use(None)
    yield
    catalyst.use(None)
