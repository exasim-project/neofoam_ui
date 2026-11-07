"""This rank's cells and fields as a conduit Blueprint mesh, the arrays passed without copying."""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ()


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


def _blueprint(node: Any, geom: dict[str, Any], fields: dict[str, np.ndarray],
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
