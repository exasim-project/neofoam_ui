"""Mesh geometry for the in-situ view: OpenFOAM polyMesh files → Catalyst cells.

Only numpy — the module runs in the solver worker (any interpreter with numpy).

A *geometry* is a plain dict describing this rank's cells, in cell order:

* ``{"kind": "polyhedral", "points": (P, 3), "offsets", "conn", "cell_offsets",
  "cell_faces"}`` — the polyMesh: face ``f`` is
  ``points[conn[offsets[f]:offsets[f + 1]]]``, cell ``i`` the faces
  ``cell_faces[cell_offsets[i]:cell_offsets[i + 1]]`` (see :func:`polyhedral`);
* ``{"kind": "line", "x": (n,)}`` — 1D: cell centres on a line;
* ``{"kind": "points", "points": (n, 3)}`` — cell centres only.

Optional key: ``note``. :mod:`neofoam_ui.catalyst` turns one into a conduit
Blueprint mesh; the arrays are handed over without copying.
"""

from __future__ import annotations

import gzip
import os
import re
from typing import Any, Optional

import numpy as np

__all__ = ["cell_centres_geometry", "cell_count", "polyhedral", "read_polymesh"]

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
    """``points``, ``offsets``/``conn`` (faces), ``owner`` and ``neighbour`` of a polyMesh dir."""
    points = _read_points(os.path.join(directory, "points"))
    offsets, conn = _read_faces(os.path.join(directory, "faces"))
    owner = _read_labels(os.path.join(directory, "owner"))
    try:
        neighbour = _read_labels(os.path.join(directory, "neighbour"))
    except FileNotFoundError:
        neighbour = np.zeros(0, dtype=owner.dtype)
    return {"points": points, "offsets": offsets, "conn": conn, "owner": owner,
            "neighbour": neighbour}


# -- geometries --------------------------------------------------------------------------


def polyhedral(mesh: dict[str, np.ndarray], n_cells: Optional[int] = None) -> dict[str, Any]:
    """The polyMesh as polyhedral cells: each cell lists its faces (owner and neighbour side)."""
    owner, neighbour = mesh["owner"], mesh["neighbour"]
    n_cells = int(owner.max()) + 1 if n_cells is None else n_cells
    cells = np.concatenate([owner, neighbour])
    faces = np.concatenate([np.arange(len(owner)), np.arange(len(neighbour))])
    order = np.argsort(cells, kind="stable")
    counts = np.bincount(cells, minlength=n_cells)
    if len(counts) != n_cells or not counts.all():
        raise ValueError(f"polyMesh has {int(cells.max()) + 1} cells, the solver {n_cells}")
    return {"kind": "polyhedral", "points": np.ascontiguousarray(mesh["points"], dtype=float),
            "offsets": mesh["offsets"], "conn": mesh["conn"],
            "cell_offsets": np.concatenate([[0], np.cumsum(counts)]),
            "cell_faces": faces[order]}


def cell_centres_geometry(centres: np.ndarray, note: str = "") -> dict[str, Any]:
    """Cell centres only (no polyMesh files): a point cloud, or a line in 1D."""
    c = np.asarray(centres, dtype=float).reshape(len(centres), -1)
    if c.shape[1] == 1:
        return {"kind": "line", "x": c[:, 0]}
    pts = np.zeros((len(c), 3))
    pts[:, : c.shape[1]] = c
    return {"kind": "points", "points": pts, **({"note": note} if note else {})}


def merged(geoms: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Several polyhedral geometries as one (cells in the order given); ``None`` unless all
    are polyhedral. Point, face and cell numbers of each part are shifted past the previous."""
    if not geoms or any(g["kind"] != "polyhedral" for g in geoms):
        return None
    points, conn, offsets, cell_faces, cell_offsets = [], [], [np.zeros(1, np.int64)], [], [np.zeros(1, np.int64)]
    n_points = n_conn = n_faces = n_cell_faces = 0
    for g in geoms:
        points.append(g["points"])
        conn.append(np.asarray(g["conn"], np.int64) + n_points)
        offsets.append(np.asarray(g["offsets"][1:], np.int64) + n_conn)
        cell_faces.append(np.asarray(g["cell_faces"], np.int64) + n_faces)
        cell_offsets.append(np.asarray(g["cell_offsets"][1:], np.int64) + n_cell_faces)
        n_points += len(g["points"])
        n_conn += len(g["conn"])
        n_faces += len(g["offsets"]) - 1
        n_cell_faces += len(g["cell_faces"])
    return {"kind": "polyhedral", "points": np.ascontiguousarray(np.concatenate(points), dtype=float),
            "offsets": np.concatenate(offsets), "conn": np.concatenate(conn),
            "cell_offsets": np.concatenate(cell_offsets), "cell_faces": np.concatenate(cell_faces)}


def cell_count(geom: dict[str, Any]) -> int:
    if geom["kind"] == "polyhedral":
        return len(geom["cell_offsets"]) - 1
    return len(geom["x"] if geom["kind"] == "line" else geom["points"])
