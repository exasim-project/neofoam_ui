"""The UI's side of a cheap run: options, and telling the worker who watches the DAG."""

import asyncio
import json

from trame.app import get_server

from neofoam_ui.app import StepperApp, parse_args


def test_delay_option_and_environment(monkeypatch):
    monkeypatch.delenv("NEOFOAM_UI_DELAY_MS", raising=False)
    monkeypatch.delenv("NEOFOAM_UI_WAIT", raising=False)
    args = parse_args([])
    assert args.delay == 0 and not args.wait_ui and args.sync_host == "127.0.0.1"
    assert parse_args(["--delay", "250", "--wait-ui", "--sync-host", "10.0.0.1"]).delay == 250
    monkeypatch.setenv("NEOFOAM_UI_DELAY_MS", "120")
    monkeypatch.setenv("NEOFOAM_UI_WAIT", "1")
    args = parse_args([])
    assert args.delay == 120 and args.wait_ui


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
