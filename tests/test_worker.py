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
