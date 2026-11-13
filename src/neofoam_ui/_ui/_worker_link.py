"""The UI's end of the worker: start it (serial or under ``mpirun``), read it, command it, stop it."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable, Optional

import neofoam_ui
from neofoam_ui.protocol import CATALYST_DATA_ENV, CATALYST_ENV, SYNC_ENV

__all__ = ()

#: The directory holding the package: put on the worker's PYTHONPATH (its interpreter may be
#: another venv). The package root, not this module's directory.
_SRC = str(Path(neofoam_ui.__file__).resolve().parent.parent)


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class _WorkerLink:
    """One worker process at a time."""

    def __init__(self, solver_spec: str, case: Optional[str], python: str, np: int, mpirun: list[str],
                 catalyst: Optional[str], sync_host: str) -> None:
        self.solver_spec = solver_spec
        self.case = case
        self.python = python
        self.np = np
        self.mpirun = mpirun
        self.catalyst = catalyst
        self.sync_host = sync_host
        #: ``host:port`` the field view's data mode listens on with Catalyst Live (``None``: none).
        self.data_url: Optional[str] = None
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._on_message: Callable[[dict[str, Any]], None] = lambda msg: None
        self._on_stderr: Callable[[str], None] = lambda line: None
        self._on_exit: Callable[[bool], None] = lambda current: None

    def bind(self, on_message: Callable[[dict[str, Any]], None], on_stderr: Callable[[str], None],
             on_exit: Callable[[bool], None]) -> None:
        """Where the worker's messages, stderr lines and end go (``on_exit(still the current one)``)."""
        self._on_message, self._on_stderr, self._on_exit = on_message, on_stderr, on_exit

    async def spawn(self) -> None:
        """Start a worker (then :meth:`read` it)."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (_SRC, env.get("PYTHONPATH")) if p)
        cmd = [self.python, "-u", "-m", "neofoam_ui.worker", self.solver_spec]
        if self.catalyst:
            env[CATALYST_ENV] = self.catalyst
            if self.data_url:
                env[CATALYST_DATA_ENV] = self.data_url
        if self.np > 1:
            env[SYNC_ENV] = f"{self.sync_host}:{_free_port(self.sync_host)}"
            cmd = [*self.mpirun, "-np", str(self.np), *cmd]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.case,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1 << 24,
        )

    def read(self, create_task: Callable[[Any], Any]) -> None:
        """Start reading the current worker's stdout and stderr (``create_task`` runs a coroutine)."""
        assert self.proc is not None
        create_task(self._read_stderr(self.proc))
        create_task(self._read_stdout(self.proc))

    async def _read_stdout(self, proc: Any) -> None:
        assert proc.stdout is not None
        async for line in proc.stdout:
            if self.proc is not proc:  # replaced by a restart: drop the old worker's messages
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._on_message(msg)
        self._on_exit(self.proc is proc)

    async def _read_stderr(self, proc: Any) -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            if self.proc is not proc:
                break
            self._on_stderr(line.decode(errors="replace").rstrip())

    def send(self, cmd: dict[str, Any]) -> None:
        """One command line to the worker; dropped while there is none (or it exited)."""
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            return
        self.proc.stdin.write((json.dumps(cmd) + "\n").encode())

    async def terminate(self) -> None:
        """Stop the current worker (SIGTERM, then SIGKILL after 10 s) and forget it."""
        old, self.proc = self.proc, None
        if old is not None and old.returncode is None:
            # SIGTERM first: mpirun forwards it to the ranks, SIGKILL would orphan them.
            old.terminate()
            try:
                await asyncio.wait_for(old.wait(), timeout=10)
            except asyncio.TimeoutError:
                old.kill()
                await old.wait()
