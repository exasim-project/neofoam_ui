"""One Catalyst session per rank: publish a frame, capture its image, describe the pipeline."""

from __future__ import annotations

import gc
import os
import sys
from time import perf_counter
from typing import Any, Optional

import numpy as np

from neofoam_ui.catalyst._blueprint import _blueprint
from neofoam_ui.catalyst._config import _CHANNEL, _PIPELINE, _data_url, settings
from neofoam_ui.catalyst._describe import _describe_pipeline
from neofoam_ui.catalyst._native import _import, _no_fp_traps

__all__ = ()


class InSitu:
    """One Catalyst session (per rank) for the life of the worker.

    ``prefix`` and ``script`` default to :func:`~neofoam_ui.catalyst.settings`; ``out_dir``: the
    pipeline's extracts go there (the default pipeline then writes its images); ``capture``:
    :meth:`execute` captures the view and returns the image (the stepper; a solver's own
    post-processing writes files only)."""

    def __init__(self, prefix: Optional[str] = None, out_dir: Optional[str] = None,
                 script: Optional[str] = None, capture: bool = True) -> None:
        config = settings()
        prefix = prefix or config.prefix
        if prefix is None:
            raise RuntimeError("no Catalyst install: set catalyst.prefix in the config or pass --catalyst")
        self.out_dir = out_dir
        self.capture = capture
        self._catalyst, self._conduit = _import(prefix)
        init = self._conduit.Node()
        self.script = script or config.script or _PIPELINE
        init["catalyst/scripts/view/filename"] = self.script
        if out_dir is not None:
            os.makedirs(out_dir, exist_ok=True)
        for key, value in (("out", out_dir), ("live", config.live), ("data", _data_url())):
            if value:
                init["catalyst/scripts/view/args"].append().set(f"{key}={value}")
        init["catalyst_load/implementation"] = "paraview"
        init["catalyst_load/search_paths/paraview"] = os.path.join(prefix, "lib", "catalyst")
        with _no_fp_traps():
            self._catalyst.initialize(init)
        self.step = 0
        #: Milliseconds of the last execute: ``total`` and per pipeline node (see :meth:`describe`).
        self.ms: dict[str, float] = {}
        #: What the pipeline script reported of the last execute (its global ``report``, if any).
        self.report: dict[str, Any] = {}
        self._module: Any = None  # the pipeline script's module, once loaded
        self._timed: set[str] = set()  # nodes with timing observers
        self._started: dict[str, float] = {}

    def execute(self, geom: dict[str, Any], fields: dict[str, np.ndarray], time: float,
                params: dict[str, str]) -> Optional[bytes]:
        """Publish this rank's part; the PNG of the whole domain on rank 0 when captured.

        Captured with ``capture`` unless ``params["mode"]`` is ``data`` (Catalyst Live
        carries the data then); collective: every rank captures, IceT composites on rank 0."""
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
        image = None
        with _no_fp_traps():
            self._catalyst.execute(node)
            if self.capture and params.get("mode") != "data":
                image = _capture(self._script_module())
            _release_context()
        self.report = dict(getattr(self._script_module(), "report", None) or {})
        self.ms["total"] = (perf_counter() - start) * 1e3
        self.step += 1
        return image

    def _script_module(self) -> Any:
        """The module Catalyst loaded the pipeline script as (``None`` before the first execute)."""
        if self._module is None:
            self._module = next((m for m in list(sys.modules.values())
                                 if getattr(m, "__file__", None)
                                 and os.path.abspath(m.__file__) == os.path.abspath(self.script)), None)
        return self._module

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
            _release_session(self.script)


def _capture(module: Any) -> Optional[bytes]:
    """The pipeline's shown layout (``shown_layout``: all its cells in one picture), else its shown
    view (``shown_view``; another script: its first render view) as PNG, composited over the ranks
    (collective: every rank renders each view, IceT composites it on rank 0); ``None`` off rank 0
    or with no view to show."""
    from paraview import simple  # noqa: PLC0415 - on sys.path only once Catalyst is loaded
    from paraview.modules.vtkRemotingViews import vtkSMSaveScreenshotProxy  # noqa: PLC0415
    from vtkmodules.vtkIOImage import vtkPNGWriter  # noqa: PLC0415
    from vtkmodules.vtkParallelCore import vtkMultiProcessController  # noqa: PLC0415

    layout = getattr(module, "shown_layout", None) if module is not None else None
    if module is not None and hasattr(module, "shown_view"):
        view = module.shown_view
    else:
        view = next(iter(simple.GetRenderViews()), None)
    if layout is not None:  # at the size the pipeline gave it: no magnification, fonts as set
        image = vtkSMSaveScreenshotProxy.CaptureImage(layout.SMProxy, layout.SMProxy.GetSize())
    elif view is not None:
        image = view.CaptureWindow(1)
    else:
        return None
    controller = vtkMultiProcessController.GetGlobalController()
    if controller is not None and controller.GetLocalProcessId() > 0:
        return None
    writer = vtkPNGWriter()
    writer.WriteToMemoryOn()
    writer.SetInputData(image)
    writer.Write()
    return bytes(memoryview(writer.GetResult()))


def _release_context() -> None:
    """Leave no OpenGL context current on this thread.

    All views render through ParaView's one process window; an EGL context can be current on
    one thread only, and a multi-region solver renders on whichever region thread is held:
    another thread's frame would fail to make it current (``EGL_BAD_ACCESS``) and come out black.
    """
    from paraview.modules.vtkRemotingViews import vtkPVProcessWindow  # noqa: PLC0415 - on sys.path only once Catalyst is loaded

    vtkPVProcessWindow.GetRenderWindow().ReleaseCurrent()


def _release_session(script: str) -> None:
    """Drop Python's references to the ParaView session, so it is freed now, while MPI is up.

    ``catalyst_finalize`` leaves the session (and with it each render view's IceT
    context) alive as long as Python holds it: the pipeline script's module,
    ``paraview.simple``'s proxy constructors, ``servermanager``'s proxy namespaces,
    wrappers and connection. Left to interpreter teardown, it is freed after
    mpi4py's atexit ``MPI_Finalize``, and IceT's ``MPI_Comm_free`` aborts the run.
    """
    from paraview import servermanager, simple  # noqa: PLC0415 - on sys.path only once Catalyst is loaded
    from paraview.simple.session import _remove_functions  # noqa: PLC0415

    path = os.path.abspath(script)
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if file and os.path.abspath(file) == path:
            del sys.modules[name]
            vars(module).clear()
    _remove_functions(vars(simple))
    for name in [k for k, v in vars(servermanager).items() if isinstance(v, servermanager.ProxyNamespace)]:
        delattr(servermanager, name)
    servermanager._pyproxies.clear()
    servermanager.Connections.clear()
    servermanager.ActiveConnection = None
    gc.collect()
