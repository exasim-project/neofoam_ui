"""The config files: the case file over the user file, ``~`` in paths, and errors naming file and key.

The files are under ``tests/config``: ``user`` and ``home/.config`` are user config
directories, ``cases/<name>/system/neofoam_ui.yaml`` case files."""

from pathlib import Path

import pytest

from neofoam_ui.config import CatalystConfig, Config, UiConfig, WorkerConfig, load_config

CONFIG = Path(__file__).parent / "config"
CASES = CONFIG / "cases"


@pytest.mark.parametrize("case", ["no_file", "empty"])
def test_load_config_without_settings_gives_the_defaults(tmp_path, case):
    assert load_config(CASES / case if case != "no_file" else tmp_path) == Config()


def test_load_config_case_file_over_user_file(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(CONFIG / "user"))
    assert load_config(CASES / "override") == Config(
        worker=WorkerConfig(python="/opt/neofoam/bin/python", np=2),
        catalyst=CatalystConfig(prefix="/case/catalyst", script="pipelines/mine.py", live="localhost:22222"),
        ui=UiConfig(delay_ms=120, wait_ui=True, port=9000))


@pytest.mark.parametrize("variable, value, np", [
    ("XDG_CONFIG_HOME", CONFIG / "user", 2),
    ("HOME", CONFIG / "home", 3),  # no XDG_CONFIG_HOME: ~/.config
])
def test_load_config_finds_the_user_file(monkeypatch, tmp_path, variable, value, np):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv(variable, str(value))
    assert load_config(tmp_path).worker.np == np


@pytest.mark.parametrize("case, message", [
    ("unknown_key", "unknown key catalyst.prefx"),
    ("unknown_section", "unknown section 'paraview'"),
    ("wrong_type", "worker.np: Input should be a valid integer"),
    ("not_a_mapping", "expected sections (worker, catalyst, ui), got list"),
])
def test_load_config_rejects_a_bad_file_naming_file_and_key(case, message):
    with pytest.raises(ValueError) as error:
        load_config(CASES / case)
    assert str(error.value).startswith(f"{CASES / case / 'system' / 'neofoam_ui.yaml'}: {message}")


def test_load_config_expands_the_home_directory_in_paths_only(monkeypatch):
    monkeypatch.setenv("HOME", "/home/me")
    config = load_config(CASES / "home_paths")
    assert config.worker.python == "/home/me/neofoam/.venv/bin/python"
    assert (config.catalyst.prefix, config.catalyst.script, config.catalyst.out) == (
        "/home/me/catalyst", "$PIPELINES/mine.py", "postProcessing/mine")
    assert config.worker.mpirun == "~/bin/mpirun"
