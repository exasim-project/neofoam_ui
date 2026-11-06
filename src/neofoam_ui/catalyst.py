"""In-situ field view through Catalyst 2: the data stays in the solver.

At a pause every rank describes *its* cells and fields as a conduit Blueprint
mesh (arrays passed without copying) and calls ``catalyst_execute``. The
ParaView implementation runs ``pipelines/view.py`` on all ranks together and
writes one PNG of the whole domain; only that file name goes to the UI.
Catalyst Live (``NEOFOAM_UI_CATALYST_LIVE=host:port``) lets a desktop ParaView
attach to the same pipeline.

Configuration (environment of the worker):

* ``NEOFOAM_UI_CATALYST`` — install prefix holding ``lib/libcatalyst.so``,
  ``lib/catalyst/libcatalyst-paraview.so`` and the ``catalyst`` /
  ``catalyst_conduit`` / ``paraview`` Python packages under
  ``lib/python3.X/site-packages``. ParaView must be built for this interpreter
  (shared Python modules, same NumPy major, same MPI as the solver).
* ``NEOFOAM_UI_CATALYST_OUT`` — directory for the images (set by the UI).
* ``NEOFOAM_UI_CATALYST_SCRIPT`` — another pipeline script (optional).

Collective: under MPI every rank must call :meth:`InSitu.execute` for the same
pause, on the thread that initialised MPI.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import sys
from typing import Any, Iterator, Optional

import numpy as np

__all__ = ["ENV", "OUT_ENV", "InSitu", "blueprint", "configured"]

ENV = "NEOFOAM_UI_CATALYST"
OUT_ENV = "NEOFOAM_UI_CATALYST_OUT"
SCRIPT_ENV = "NEOFOAM_UI_CATALYST_SCRIPT"
CHANNEL = "grid"
PIPELINE = os.path.join(os.path.dirname(__file__), "pipelines", "view.py")


def configured() -> bool:
    return bool(os.environ.get(ENV)) and bool(os.environ.get(OUT_ENV))


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
        init["catalyst/scripts/view/filename"] = script or os.environ.get(SCRIPT_ENV) or PIPELINE
        init["catalyst/scripts/view/args"].append().set(self.out_dir)
        init["catalyst_load/implementation"] = "paraview"
        init["catalyst_load/search_paths/paraview"] = os.path.join(prefix, "lib", "catalyst")
        with _no_fp_traps():
            self._catalyst.initialize(init)
        self.step = 0

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
        with _no_fp_traps():
            self._catalyst.execute(node)
        name = f"view_{self.step:06d}.png"
        self.step += 1
        return name

    def finalize(self) -> None:
        with _no_fp_traps():
            self._catalyst.finalize(self._conduit.Node())
