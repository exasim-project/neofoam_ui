"""One Catalyst session per rank: publish a frame, prune old images, describe the pipeline."""

from __future__ import annotations

import contextlib
import os
from time import perf_counter
from typing import Any, Optional

import numpy as np

from neofoam_ui.catalyst._blueprint import _blueprint
from neofoam_ui.catalyst._config import _CHANNEL, _PIPELINE, ENV, OUT_ENV, SCRIPT_ENV
from neofoam_ui.catalyst._describe import _describe_pipeline
from neofoam_ui.catalyst._native import _import, _no_fp_traps

__all__ = ()


class InSitu:
    """One Catalyst session (per rank) for the life of the worker."""

    def __init__(self, prefix: Optional[str] = None, out_dir: Optional[str] = None,
                 script: Optional[str] = None) -> None:
        prefix = prefix or os.environ[ENV]
        self.out_dir = out_dir or os.environ[OUT_ENV]
        os.makedirs(self.out_dir, exist_ok=True)
        self._catalyst, self._conduit = _import(prefix)
        init = self._conduit.Node()
        self.script = script or os.environ.get(SCRIPT_ENV) or _PIPELINE
        init["catalyst/scripts/view/filename"] = self.script
        init["catalyst/scripts/view/args"].append().set(self.out_dir)
        init["catalyst_load/implementation"] = "paraview"
        init["catalyst_load/search_paths/paraview"] = os.path.join(prefix, "lib", "catalyst")
        with _no_fp_traps():
            self._catalyst.initialize(init)
        self.step = 0
        self._pruned = 0  # images below this step are deleted
        #: Milliseconds of the last execute: ``total`` and per pipeline node (see :meth:`describe`).
        self.ms: dict[str, float] = {}
        self._timed: set[str] = set()  # nodes with timing observers
        self._started: dict[str, float] = {}

    def execute(self, geom: dict[str, Any], fields: dict[str, np.ndarray], time: float,
                params: dict[str, str]) -> str:
        """Publish this rank's part; returns the image file name (written by rank 0)."""
        node = self._conduit.Node()
        node["catalyst/state/timestep"] = self.step
        node["catalyst/state/time"] = float(time)
        for key, value in params.items():
            node["catalyst/state/parameters"].append().set(f"{key}={value}")
        node[f"catalyst/channels/{_CHANNEL}/type"] = "mesh"
        keep: list[np.ndarray] = []
        _blueprint(node[f"catalyst/channels/{_CHANNEL}/data"], geom, fields, keep)
        self.ms, self._started = {}, {}
        start = perf_counter()
        with _no_fp_traps():
            self._catalyst.execute(node)
        self.ms["total"] = (perf_counter() - start) * 1e3
        name = f"view_{self.step:06d}.png"
        self.step += 1
        return name

    def prune(self, keep: int) -> None:
        """Delete this session's images but the newest ``keep`` (rank 0 writes them)."""
        for step in range(self._pruned, max(self._pruned, self.step - max(1, keep))):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(self.out_dir, f"view_{step:06d}.png"))
            self._pruned = step + 1

    def describe(self) -> dict[str, Any]:
        """The live ParaView pipeline after the last execute (call on one rank, solver thread).

        Also times the nodes from the next execute on: Start/End observers on each
        source's and display's algorithm and each view's render window (this rank).
        """
        with _no_fp_traps():
            desc = _describe_pipeline(self._observe)
        desc["script"] = os.path.basename(self.script)
        desc["ms"] = {k: round(v, 3) for k, v in self.ms.items()}
        return desc

    def _observe(self, node_id: str, obj: Any) -> None:
        if node_id in self._timed or obj is None:
            return
        self._timed.add(node_id)

        def start(*_: Any) -> None:
            self._started[node_id] = perf_counter()

        def end(*_: Any) -> None:  # a render window may render more than once per execute
            t0 = self._started.pop(node_id, None)
            if t0 is not None:
                self.ms[node_id] = self.ms.get(node_id, 0.0) + (perf_counter() - t0) * 1e3

        obj.AddObserver("StartEvent", start)
        obj.AddObserver("EndEvent", end)

    def finalize(self) -> None:
        with _no_fp_traps():
            self._catalyst.finalize(self._conduit.Node())
