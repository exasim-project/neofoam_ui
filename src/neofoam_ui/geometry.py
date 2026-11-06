"""Mesh geometry for the field view: OpenFOAM polyMesh files → drawable cells.

Only numpy — the module runs in the solver worker (any interpreter with numpy).

A *geometry* is a plain dict, one entry per cell, in cell order:

* ``{"kind": "line", "x": (n,)}`` — 1D: a value per position;
* ``{"kind": "polygons", "points": (P, 2), "offsets": (n + 1,), "conn": (k,)}`` —
  2D: cell ``i`` is the polygon ``points[conn[offsets[i]:offsets[i + 1]]]``;
* ``{"kind": "points", "points": (n, 2)}`` — anything else: cell centres.

Optional keys: ``axes`` (names of the two drawn axes) and ``note``.
:func:`encode_geometry` turns one into a JSON-able message.
"""

from __future__ import annotations

import base64
import gzip
import os
import re
from typing import Any, Optional

import numpy as np

__all__ = ["cell_centres_geometry", "encode_geometry", "encode_values", "front_polygons",
           "read_polymesh"]

_AXES = ("x", "y", "z")


# -- polyMesh files -------------------------------------------------------------------


def _read(path: str) -> bytes:
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    if os.path.exists(path + ".gz"):
        with gzip.open(path + ".gz", "rb") as f:
            return f.read()
    raise FileNotFoundError(path)


def _header(data: bytes) -> tuple[dict[str, str], int]:
    """FoamFile header entries and the offset just after it."""
    m = re.search(rb"FoamFile\s*\{(.*?)\}", data, re.S)
    if not m:
        return {}, 0
    entries = dict(re.findall(r"(\w+)\s+([^;]*);", m.group(1).decode(errors="replace")))
    return {k: v.strip().strip('"') for k, v in entries.items()}, m.end()


def _list_start(data: bytes, pos: int) -> tuple[int, int]:
    """``N (`` after ``pos`` (comments skipped): ``(N, offset after '(')``."""
    m = re.compile(rb"(?:\s|//[^\n]*\n|/\*.*?\*/)*(\d+)\s*\(", re.S).match(data, pos)
    if not m:
        raise ValueError("no list found (uniform 'N{value}' lists are not supported)")
    return int(m.group(1)), m.end()


def _sizes(header: dict[str, str]) -> tuple[np.dtype, np.dtype]:
    arch = header.get("arch", "")
    label = np.dtype("<i8") if "label=64" in arch else np.dtype("<i4")
    scalar = np.dtype("<f4") if "scalar=32" in arch else np.dtype("<f8")
    return label, scalar


def _binary(data: bytes, pos: int, dtype: np.dtype, width: int = 1) -> tuple[np.ndarray, int]:
    n, start = _list_start(data, pos)
    end = start + n * width * dtype.itemsize
    arr = np.frombuffer(data[start:end], dtype=dtype)
    return (arr.reshape(n, width) if width > 1 else arr), end + 1  # skip ')'


def _ascii_numbers(data: bytes, pos: int) -> tuple[int, list[bytes]]:
    n, start = _list_start(data, pos)
    body = data[start:].rsplit(b")", 1)[0]
    return n, body.replace(b"(", b" ").replace(b")", b" ").split()


def _read_points(path: str) -> np.ndarray:
    data = _read(path)
    header, pos = _header(data)
    if header.get("format") == "binary":
        return _binary(data, pos, _sizes(header)[1], 3)[0].astype(float)
    n, tokens = _ascii_numbers(data, pos)
    return np.array(tokens[: 3 * n], dtype=float).reshape(n, 3)


def _read_labels(path: str) -> np.ndarray:
    data = _read(path)
    header, pos = _header(data)
    if header.get("format") == "binary":
        return _binary(data, pos, _sizes(header)[0])[0].astype(np.int64)
    n, tokens = _ascii_numbers(data, pos)
    return np.array(tokens[:n], dtype=np.int64)


def _read_faces(path: str) -> tuple[np.ndarray, np.ndarray]:
    """``(offsets, conn)`` of a ``faceList`` (ASCII) or ``faceCompactList`` (binary)."""
    data = _read(path)
    header, pos = _header(data)
    if header.get("format") == "binary":
        label = _sizes(header)[0]
        offsets, pos = _binary(data, pos, label)
        conn, _ = _binary(data, pos, label)
        return offsets.astype(np.int64), conn.astype(np.int64)
    n, tokens = _ascii_numbers(data, pos)
    flat = np.array(tokens, dtype=np.int64)
    counts = np.empty(n, dtype=np.int64)
    keep = np.ones(len(flat), dtype=bool)
    i = 0
    for f in range(n):  # "4(a b c d)" → 4 a b c d
        counts[f] = flat[i]
        keep[i] = False
        i += 1 + flat[i]
    offsets = np.concatenate([[0], np.cumsum(counts)])
    return offsets, flat[keep]


def read_polymesh(directory: str) -> dict[str, np.ndarray]:
    """``points``, ``offsets``/``conn`` (faces), ``owner`` and ``n_internal`` of a polyMesh dir."""
    points = _read_points(os.path.join(directory, "points"))
    offsets, conn = _read_faces(os.path.join(directory, "faces"))
    owner = _read_labels(os.path.join(directory, "owner"))
    try:
        n_internal = len(_read_labels(os.path.join(directory, "neighbour")))
    except FileNotFoundError:
        n_internal = 0
    return {"points": points, "offsets": offsets, "conn": conn, "owner": owner,
            "n_internal": np.int64(n_internal)}


# -- geometries --------------------------------------------------------------------------


def front_polygons(mesh: dict[str, np.ndarray], n_cells: Optional[int] = None,
                   tol: float = 1e-9) -> Optional[dict[str, Any]]:
    """One polygon per cell for a one-cell-thick (2D) mesh, else ``None``.

    The thin axis is the one whose point coordinates take only two values; the
    boundary faces on its lower plane are the cells' front faces.
    """
    points, offsets, conn, owner = mesh["points"], mesh["offsets"], mesh["conn"], mesh["owner"]
    n_cells = int(owner.max()) + 1 if n_cells is None else n_cells
    extent = points.max(axis=0) - points.min(axis=0)
    scale = float(extent.max()) or 1.0
    thin = int(np.argmin(extent))
    lo = points[:, thin].min()
    on_lo = np.abs(points[:, thin] - lo) <= tol * scale
    on_hi = np.abs(points[:, thin] - points[:, thin].max()) <= tol * scale
    if not np.all(on_lo | on_hi) or extent[thin] <= tol * scale:
        return None  # not one cell thick
    n_faces = len(offsets) - 1
    sizes = np.diff(offsets)
    face_of = np.repeat(np.arange(n_faces), sizes)
    lo_faces = np.bincount(face_of, weights=on_lo[conn], minlength=n_faces) == sizes
    lo_faces[: int(mesh["n_internal"])] = False
    front = np.flatnonzero(lo_faces)
    cells = owner[front]
    if len(front) != n_cells or len(np.unique(cells)) != n_cells:
        return None
    front = front[np.argsort(cells)]
    keep = [a for a in range(3) if a != thin]
    counts = sizes[front]
    idx = np.concatenate([conn[offsets[f]:offsets[f + 1]] for f in front])
    used, local = np.unique(idx, return_inverse=True)
    return {"kind": "polygons", "points": points[used][:, keep],
            "offsets": np.concatenate([[0], np.cumsum(counts)]), "conn": local,
            "axes": [_AXES[a] for a in keep]}


def cell_centres_geometry(centres: np.ndarray, note: str = "") -> dict[str, Any]:
    """Cell centres projected onto their two widest axes."""
    c = np.asarray(centres, dtype=float).reshape(len(centres), -1)
    if c.shape[1] == 1:
        return {"kind": "line", "x": c[:, 0]}
    keep = sorted(np.argsort(c.max(axis=0) - c.min(axis=0))[-2:])
    return {"kind": "points", "points": c[:, keep], "axes": [_AXES[a] for a in keep],
            **({"note": note} if note else {})}


# -- wire format ----------------------------------------------------------------------------


def _b64(arr: Any, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=dtype).tobytes()).decode()


def encode_geometry(geom: dict[str, Any]) -> dict[str, Any]:
    """Geometry → JSON-able dict (float32 / int32 arrays as base64, little endian)."""
    kind = geom["kind"]
    out: dict[str, Any] = {"kind": kind, "axes": list(geom.get("axes", ["x", "y"])),
                           "note": geom.get("note", "")}
    if kind == "line":
        x = np.asarray(geom["x"], dtype=float)
        out.update(n=len(x), x=_b64(x, "<f4"), bounds=[float(x.min()), float(x.max())])
        return out
    pts = np.asarray(geom["points"], dtype=float)
    out.update(points=_b64(pts, "<f4"),
               bounds=[float(pts[:, 0].min()), float(pts[:, 0].max()),
                       float(pts[:, 1].min()), float(pts[:, 1].max())])
    if kind == "polygons":
        out.update(n=len(geom["offsets"]) - 1, offsets=_b64(geom["offsets"], "<i4"),
                   conn=_b64(geom["conn"], "<i4"))
    elif kind == "points":
        out["n"] = len(pts)
    else:
        raise ValueError(f"unknown geometry kind {kind!r}")
    return out


def encode_values(values: np.ndarray) -> str:
    return _b64(values, "<f4")
