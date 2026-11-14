"""The UI's side of a cheap run: options, and telling the worker who watches the DAG."""

import asyncio
import json
from pathlib import Path

import click
import pytest

from trame.app import get_server

from neofoam_ui.app import StepperApp, parse_args

CONFIG = Path(__file__).parent / "config"


def test_parse_args_defaults_and_options():
    ui, worker = parse_args([]).config.ui, parse_args([]).config.worker
    assert ui.delay_ms == 0 and not ui.wait_ui and worker.sync_host == "127.0.0.1"
    config = parse_args(["--delay", "250", "--wait-ui", "--sync-host", "10.0.0.1"]).config
    assert config.ui.delay_ms == 250 and config.ui.wait_ui and config.worker.sync_host == "10.0.0.1"


def test_parse_args_options_over_the_case_config(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(CONFIG / "user"))
    case = str(CONFIG / "cases" / "override")
    config = parse_args(["--case", case]).config
    assert (config.ui.delay_ms, config.ui.wait_ui, config.ui.port, config.catalyst.prefix) == (
        120, True, 9000, "/case/catalyst")
    config = parse_args(["--case", case, "--delay", "250", "--no-wait-ui", "--catalyst", "/cli/catalyst"]).config
    assert (config.ui.delay_ms, config.ui.wait_ui, config.catalyst.prefix, config.catalyst.script) == (
        250, False, "/cli/catalyst", "pipelines/mine.py")


def test_parse_args_expands_the_home_directory_of_a_path_option(monkeypatch):
    monkeypatch.setenv("HOME", "/home/me")
    assert parse_args(["--catalyst", "~/catalyst"]).config.catalyst.prefix == "/home/me/catalyst"


@pytest.mark.parametrize("argv", [["--bogus"], ["--np", "two"], ["extra"]])
def test_parse_args_refuses_what_it_does_not_know(argv):
    with pytest.raises(click.UsageError):
        parse_args(argv)


def test_parse_args_passes_trame_args_through():
    assert parse_args(["--trame-args", "--debug"]).solver == "neofoam_ui.toy.heat1d:Heat1D"


def test_app_tells_the_worker_who_watches():
    app = StepperApp(get_server("overhead_watch", client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D",
                     delay_ms=40)
    sent = []
    app.link.send = sent.append
    assert app.state.delay_ms == 40 and app.state.dag_on
    app.viewer(True)  # a browser tab shows the app
    assert sent[-1] == {"cmd": "watch", "on": True}
    app.state.dag_on = False  # the DAG pane closed (the state listener calls this)
    app.dag.send_watch()
    assert sent[-1] == {"cmd": "watch", "on": False}
    app.state.dag_on = True
    app.dag.send_watch()
    assert sent[-1] == {"cmd": "watch", "on": True}
    app.viewer(False)  # ... and is gone
    assert sent[-1] == {"cmd": "watch", "on": False}


def test_state_is_pushed_at_a_capped_rate_and_syncs_are_acknowledged():
    app = StepperApp(get_server("overhead_push", client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D")
    pushes = []
    real = app.pusher.push
    app.pusher.push = lambda: (pushes.append(1), real())[1]
    written = []

    class Stdin:
        def write(self, data):
            written.append(json.loads(data))

    async def feed():
        proc = type("Proc", (), {})()
        proc.stdout, proc.stdin, proc.returncode = asyncio.StreamReader(), Stdin(), None
        app.proc = proc
        log = {"type": "log", "kind": "exec", "node": "a", "name": "a", "ms": 0.1, "stack": [], "time": 0.0}
        for _ in range(500):
            proc.stdout.feed_data((json.dumps(log) + "\n").encode())
        proc.stdout.feed_data((json.dumps({"type": "sync", "seq": 3}) + "\n").encode())
        proc.stdout.feed_data(b'{"type": "stdout", "text": "last"}\n')
        proc.stdout.feed_eof()
        await app.link._read_stdout(proc)

    asyncio.run(feed())
    assert len(pushes) < 20  # not one per message
    assert written == [{"cmd": "ack", "seq": 3}]  # after everything before it was pushed
    texts = [line["t"] for line in app.state.logs]
    assert texts[0] == "last" and len(texts) == 500  # the console keeps its newest CONSOLE_MAX
