import json
import subprocess
import sys


def test_worker_protocol_roundtrip():
    proc = subprocess.Popen(
        [sys.executable, "-m", "neofoam_ui.worker", "neofoam_ui.toy.heat1d:Heat1D"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    def send(cmd):
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

    def until(kind):
        seen = []
        for line in proc.stdout:
            msg = json.loads(line)
            seen.append(msg)
            if msg["type"] == kind:
                return seen
        raise AssertionError(f"worker ended before {kind!r}: {seen}")

    try:
        first = until("paused")
        assert first[0]["type"] == "graph" and first[0]["activity"] == "init"
        assert first[-1]["event"]["node"] == "init:mesh"

        send({"cmd": "breakpoints", "ids": ["time_loop/write_output"]})
        send({"cmd": "step", "mode": "run"})
        seen = until("paused")
        assert seen[-1]["event"]["node"] == "time_loop/write_output"
        assert any(m["type"] == "graph" and m["activity"] == "exec" for m in seen)

        send({"cmd": "breakpoints", "ids": []})
        send({"cmd": "step", "mode": "run"})
        seen = until("paused")
        assert seen[-1]["finished"]
        # write_output prints; the print must arrive as a protocol message.
        assert any(m["type"] == "stdout" and "Writing fields" in m["text"] for m in seen)

        send({"cmd": "stop"})
        until("stopped")
    finally:
        proc.kill()
        proc.wait()


def test_bad_commands_are_reported_and_stop_still_works():
    proc = subprocess.Popen(
        [sys.executable, "-m", "neofoam_ui.worker", "neofoam_ui.toy.heat1d:Heat1D"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        for bad in ("not json", '{"no": "cmd"}', '{"cmd": "delay", "seconds": "x"}',
                    '{"cmd": "step", "mode": "sideways"}'):
            proc.stdin.write(bad + "\n")
        proc.stdin.write('{"cmd": "stop"}\n')
        proc.stdin.flush()
        kinds = [json.loads(line)["type"] for line in proc.stdout]
        assert kinds.count("log_error") == 4
        assert kinds[-1] == "stopped"
        assert proc.wait(timeout=20) == 0
    finally:
        proc.kill()
        proc.wait()
