"""In-situ field view through Catalyst 2: the data stays in the solver.

At a pause every rank describes *its* cells and fields as a conduit Blueprint
mesh (arrays passed without copying) and calls ``catalyst_execute``. The
ParaView implementation runs ``pipelines/view.py`` on all ranks together and
writes one PNG of the whole domain; only that file name goes to the UI.
Catalyst Live (``NEOFOAM_UI_CATALYST_LIVE=host:port``) lets a desktop ParaView
attach to the same pipeline. Rank 0 also describes the live ParaView pipeline
(:meth:`InSitu.describe`: sources, filters, displays, views, extractors and the
time each took) for the UI's post-processing diagram — settings only, no data.

Configuration (environment of the worker):

* ``NEOFOAM_UI_CATALYST`` — install prefix holding ``lib/libcatalyst.so``,
  ``lib/catalyst/libcatalyst-paraview.so`` and the ``catalyst`` /
  ``catalyst_conduit`` / ``paraview`` Python packages under
  ``lib/python3.X/site-packages``. ParaView must be built for this interpreter
  (shared Python modules, same NumPy major, same MPI as the solver).
* ``NEOFOAM_UI_CATALYST_OUT`` — directory for the images (set by the UI).
* ``NEOFOAM_UI_CATALYST_SCRIPT`` — another pipeline script (optional). The default
  pipeline gets only the viewed field; another script gets every cell field.

Collective: under MPI every rank must call :meth:`InSitu.execute` for the same
pause, on the thread that initialised MPI.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import sys
from time import perf_counter
from typing import Any, Iterator, Optional

import numpy as np

__all__ = ["ENV", "OUT_ENV", "InSitu", "blueprint", "configured", "custom_script", "describe_pipeline"]

from neofoam_ui.protocol import CATALYST_ENV as ENV
from neofoam_ui.protocol import CATALYST_OUT_ENV as OUT_ENV
from neofoam_ui.protocol import CATALYST_SCRIPT_ENV as SCRIPT_ENV

CHANNEL = "grid"
PIPELINE = os.path.join(os.path.dirname(__file__), "pipelines", "view.py")


def configured() -> bool:
    return bool(os.environ.get(ENV)) and bool(os.environ.get(OUT_ENV))


def custom_script() -> bool:
    """A pipeline script other than the stepper's own (it may use any field)."""
    return bool(os.environ.get(SCRIPT_ENV))


_FE_ALL_EXCEPT = 0x3D  # glibc x86: invalid | divbyzero | overflow | underflow | inexact
_libm: Any = None


@contextlib.contextmanager
def _no_fp_traps() -> Iterator[None]:
    """OpenFOAM traps FP exceptions (``FOAM_SIGFPE``); VTK/ParaView overflow on purpose."""
    global _libm
    if _libm is None:
        name = ctypes.util.find_library("m")
        _libm = ctypes.CDLL(name) if name else False
    if not _libm:
        yield
        return
    traps = _libm.fegetexcept()
    _libm.fedisableexcept(_FE_ALL_EXCEPT)
    try:
        yield
    finally:
        _libm.feclearexcept(_FE_ALL_EXCEPT)  # do not fire on what ParaView left behind
        if traps > 0:
            _libm.feenableexcept(traps)


def _import(prefix: str) -> tuple[Any, Any]:
    site = os.path.join(prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                        "site-packages")
    if site not in sys.path:
        sys.path.append(site)  # last: the solver's own numpy etc. win
    import catalyst  # noqa: PLC0415
    import catalyst_conduit  # noqa: PLC0415

    return catalyst, catalyst_conduit


def _external(node: Any, path: str, arr: np.ndarray, keep: list[np.ndarray]) -> None:
    arr = np.ascontiguousarray(arr)
    keep.append(arr)  # conduit only points at it until execute returns
    node[path].set_external(arr)


def _interleaved(node: Any, path: str, arr: np.ndarray, names: Any,
                 keep: list[np.ndarray]) -> None:
    """Components as strided views into one (n, k) array: VTK keeps it as one AOS array."""
    import json  # noqa: PLC0415

    from catalyst_conduit import Schema  # noqa: PLC0415

    arr = np.ascontiguousarray(arr, dtype=np.float64)
    keep.append(arr)
    n, k = arr.shape
    layout = {str(c): {"dtype": "float64", "number_of_elements": n, "offset": 8 * i, "stride": 8 * k}
              for i, c in enumerate(names)}
    node[path].set_external(Schema(json.dumps(layout)), arr)


def blueprint(node: Any, geom: dict[str, Any], fields: dict[str, np.ndarray],
              keep: list[np.ndarray]) -> None:
    """Fill ``node`` (a channel's ``data``) with ``geom`` and its cell ``fields``."""
    kind = geom["kind"]
    if kind == "line":
        x = np.asarray(geom["x"], dtype=float)
        mid = (x[1:] + x[:-1]) / 2 if len(x) > 1 else x
        first = x[0] - (mid[0] - x[0]) if len(x) > 1 else x[0] - 0.5
        last = x[-1] + (x[-1] - mid[-1]) if len(x) > 1 else x[0] + 0.5
        _external(node, "coordsets/coords/values/x", np.concatenate([[first], mid, [last]]), keep)
        node["coordsets/coords/type"] = "rectilinear"
        node["topologies/mesh/type"] = "rectilinear"
    elif kind in ("polyhedral", "points"):
        pts = np.asarray(geom["points"], dtype=float)
        node["coordsets/coords/type"] = "explicit"
        _interleaved(node, "coordsets/coords/values", pts, "xyz", keep)
        node["topologies/mesh/type"] = "unstructured"
        el = "topologies/mesh/elements"
        if kind == "points":
            node[f"{el}/shape"] = "point"
            _external(node, f"{el}/connectivity", np.arange(len(pts), dtype=np.int64), keep)
        else:
            offsets = np.asarray(geom["offsets"], dtype=np.int64)
            cell_offsets = np.asarray(geom["cell_offsets"], dtype=np.int64)
            node[f"{el}/shape"] = "polyhedral"
            _external(node, f"{el}/connectivity", np.asarray(geom["cell_faces"], dtype=np.int64), keep)
            _external(node, f"{el}/sizes", np.diff(cell_offsets), keep)
            _external(node, f"{el}/offsets", cell_offsets[:-1], keep)
            sub = "topologies/mesh/subelements"
            node[f"{sub}/shape"] = "polygonal"
            _external(node, f"{sub}/connectivity", np.asarray(geom["conn"], dtype=np.int64), keep)
            _external(node, f"{sub}/sizes", np.diff(offsets), keep)
            _external(node, f"{sub}/offsets", offsets[:-1], keep)
    else:
        raise ValueError(f"unknown geometry kind {kind!r}")
    node["topologies/mesh/coordset"] = "coords"
    for name, values in fields.items():
        base = f"fields/{name}"
        node[f"{base}/association"] = "element"
        node[f"{base}/topology"] = "mesh"
        arr = np.asarray(values, dtype=float)
        if arr.ndim == 1:
            _external(node, f"{base}/values", arr, keep)
        else:
            names = "xyz" if arr.shape[1] == 3 else [str(i) for i in range(arr.shape[1])]
            _interleaved(node, f"{base}/values", arr, names, keep)


class InSitu:
    """One Catalyst session (per rank) for the life of the worker."""

    def __init__(self, prefix: Optional[str] = None, out_dir: Optional[str] = None,
                 script: Optional[str] = None) -> None:
        prefix = prefix or os.environ[ENV]
        self.out_dir = out_dir or os.environ[OUT_ENV]
        os.makedirs(self.out_dir, exist_ok=True)
        self._catalyst, self._conduit = _import(prefix)
        init = self._conduit.Node()
        self.script = script or os.environ.get(SCRIPT_ENV) or PIPELINE
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
        node[f"catalyst/channels/{CHANNEL}/type"] = "mesh"
        keep: list[np.ndarray] = []
        blueprint(node[f"catalyst/channels/{CHANNEL}/data"], geom, fields, keep)
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
            desc = describe_pipeline(self._observe)
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


# -- the pipeline as a graph (for the UI's post-processing diagram) ----------------------------

_SKIP = {"Input", "Producer", "Trigger", "Writer", "View", "Enable"}
_MAX_SETTINGS = 4


def _short(value: Any) -> str:
    """A property value as a short string (numbers to 4 digits, proxies by their label)."""
    if hasattr(value, "SMProperty"):  # a property wrapper (e.g. an array selection): its values
        value = list(value)
    if isinstance(value, (list, tuple)):
        text = "[" + ", ".join(_short(v) for v in value[:4]) + (", …" if len(value) > 4 else "") + "]"
    elif isinstance(value, float):
        text = f"{value:.4g}"
    elif hasattr(value, "SMProxy"):
        text = value.GetXMLLabel()
    else:
        text = str(value)
    return text if len(text) <= 48 else text[:47] + "…"


def _enum(value: Any) -> str:
    return str(value).strip("'")  # an enumeration property's value prints quoted


def _settings(proxy: Any) -> list[list[str]]:
    """Up to ``_MAX_SETTINGS`` properties that differ from their defaults (no inputs, no info)."""
    rows: list[list[str]] = []
    for name in proxy.ListProperties():
        if name in _SKIP:
            continue
        prop = proxy.GetProperty(name).SMProperty
        if prop.GetInformationOnly() or prop.IsA("vtkSMInputProperty") or prop.IsValueDefault():
            continue
        rows.append([name, _short(proxy.GetPropertyValue(name))])
        if len(rows) == _MAX_SETTINGS:
            break
    return rows


def _port_proxy(value: Any) -> Any:
    """The proxy behind an input value (a proxy, or an output port of one)."""
    if isinstance(value, (list, tuple)):
        return [_port_proxy(v) for v in value]
    return getattr(value, "Proxy", value) if not hasattr(value, "GetGlobalIDAsString") else value


def _colouring(rep: Any) -> list[list[str]]:
    """What a display shows: representation, coloured array and component, colour range."""
    rows = [["as", _enum(rep.Representation)]] if rep.GetProperty("Representation") is not None else []
    array = list(rep.ColorArrayName) if rep.GetProperty("ColorArrayName") is not None else []
    lut = rep.LookupTable if rep.GetProperty("LookupTable") is not None else None
    if len(array) < 2 or not array[1] or lut is None:
        return rows + [["colour", "solid"]]
    name = array[1]
    mode = _enum(lut.VectorMode)
    comp = (f"|{name}|" if mode == "Magnitude" else f"{name}[{int(lut.VectorComponent)}]") \
        if _ncomp(rep, array) > 1 else name
    points = list(lut.RGBPoints)
    rows.append(["colour", f"{comp} ({str(array[0]).lower()})"])
    if len(points) >= 4:
        rows.append(["range", f"{points[0]:.4g} … {points[-4]:.4g}"])
    return rows


def _ncomp(rep: Any, array: list[Any]) -> int:
    data = rep.Input.PointData if str(array[0]) == "POINTS" else rep.Input.CellData
    arr = data.GetArray(array[1])
    return arr.GetNumberOfComponents() if arr is not None else 1


def describe_pipeline(observe: Any = None) -> dict[str, Any]:
    """Nodes and edges of the ParaView pipeline in this process (``paraview.simple``).

    Node: ``{"id", "kind": source|filter|display|view|extractor, "name", "type",
    "settings": [[key, value], ...]}``; edge: ``{"source", "target"}`` (data flows
    from source to target). ``observe(id, vtk_object)`` is called for what can be timed.
    """
    from paraview import servermanager, simple  # noqa: PLC0415

    pxm = servermanager.ProxyManager()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def gid(proxy: Any) -> str:
        return "pv" + proxy.GetGlobalIDAsString()

    for (name, _), proxy in simple.GetSources().items():
        group = proxy.GetXMLGroup()
        nodes.append({"id": gid(proxy), "kind": "source" if group == "sources" else "filter",
                      "name": name, "type": proxy.GetXMLLabel(), "settings": _settings(proxy)})
        for prop in proxy.ListProperties():
            if proxy.GetProperty(prop).SMProperty.IsA("vtkSMInputProperty"):
                inputs = _port_proxy(proxy.GetPropertyValue(prop))
                for up in inputs if isinstance(inputs, list) else [inputs]:
                    if up is not None:
                        edges.append({"source": gid(up), "target": gid(proxy)})
        if observe is not None:
            observe(gid(proxy), proxy.GetClientSideObject())
    for view in simple.GetViews():
        bars = []
        for rep in view.Representations:
            if rep.GetProperty("Input") is None:  # a colour bar, not a display
                if rep.GetProperty("Title") is not None and rep.Visibility:
                    bars.append(str(rep.Title))
                continue
            if not rep.Visibility or rep.Input is None:
                continue
            nodes.append({"id": gid(rep), "kind": "display",
                          "name": pxm.GetProxyName("representations", rep) or "display",
                          "type": rep.GetXMLLabel(), "settings": _colouring(rep)})
            edges.append({"source": gid(_port_proxy(rep.Input)), "target": gid(rep)})
            edges.append({"source": gid(rep), "target": gid(view)})
            if observe is not None:
                observe(gid(rep), rep.GetClientSideObject())
        settings = [["size", "×".join(str(v) for v in view.ViewSize)]] if view.GetProperty("ViewSize") else []
        settings += [["colour bar", t] for t in bars]
        nodes.append({"id": gid(view), "kind": "view", "name": pxm.GetProxyName("views", view) or "view",
                      "type": view.GetXMLLabel(), "settings": settings})
        if observe is not None and hasattr(view, "GetRenderWindow"):
            observe(gid(view), view.GetRenderWindow())
    for ext in simple.GetExtractors():
        writer = ext.Writer
        settings = [["file", str(writer.FileName)]] if writer.GetProperty("FileName") is not None else []
        if writer.GetProperty("ImageResolution") is not None and any(writer.ImageResolution):
            settings.append(["resolution", "×".join(str(v) for v in writer.ImageResolution)])
        trigger = ext.Trigger
        if trigger is not None and trigger.GetProperty("Frequency") is not None:
            settings.append(["every", f"{int(trigger.Frequency)} step(s)"])
        if not ext.Enable:
            settings.append(["enabled", "no"])
        nodes.append({"id": gid(ext), "kind": "extractor",
                      "name": pxm.GetProxyName("extractors", ext) or "extractor",
                      "type": writer.GetXMLLabel(), "settings": settings})
        if ext.Producer is not None:
            edges.append({"source": gid(_port_proxy(ext.Producer)), "target": gid(ext)})
    known = {n["id"] for n in nodes}
    return {"nodes": nodes, "edges": [e for e in edges if e["source"] in known and e["target"] in known]}
