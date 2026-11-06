"""Publish the field view to ParaView in situ (Catalyst-style script, optional Catalyst Live).

The UI process already receives the geometry (``mesh``) and the viewed field
(``field``) from the worker at every pause; :class:`CatalystPublisher` turns
them into a ``vtkUnstructuredGrid`` and runs a ParaView Catalyst Python script
on it through ParaView's in-process bridge (``paraview.catalyst.bridge``). The
script is an ordinary Catalyst-v2 script (``paraview.simple`` + extractors +
``catalyst.Options``; set ``options.EnableCatalystLive = 1`` to connect a
desktop ParaView of the same version via *Catalyst ▸ Connect*).

Needs ParaView's Python modules in the UI interpreter (conda-forge ``paraview``
≥ 6, e.g. ``pixi run -e catalyst neofoam-ui --catalyst script.py``). Not
libcatalyst / ``catalyst.execute``: conda-forge ParaView ships no Catalyst 2
runtime (see ``plans/03-field-view-and-catalyst.md``).
"""

from __future__ import annotations

import base64
from typing import Any, Optional

import numpy as np

__all__ = ["CatalystPublisher", "decode", "vtk_grid"]


def decode(b64: str, dtype: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype=dtype)


def vtk_grid(mesh: dict[str, Any]) -> Any:
    """``mesh`` message → ``vtkUnstructuredGrid`` (polygons, or one vertex per cell)."""
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray  # noqa: PLC0415
    from vtkmodules.vtkCommonCore import vtkPoints  # noqa: PLC0415
    from vtkmodules.vtkCommonDataModel import (  # noqa: PLC0415
        VTK_POLYGON,
        VTK_VERTEX,
        vtkCellArray,
        vtkUnstructuredGrid,
    )

    if mesh["kind"] == "line":
        x = decode(mesh["x"], "<f4").astype(float)
        xyz = np.stack([x, np.zeros_like(x), np.zeros_like(x)], axis=1)
    else:
        xy = decode(mesh["points"], "<f4").astype(float).reshape(-1, 2)
        xyz = np.concatenate([xy, np.zeros((len(xy), 1))], axis=1)
    if mesh["kind"] == "polygons":
        offsets, conn, cell_type = decode(mesh["offsets"], "<i4"), decode(mesh["conn"], "<i4"), VTK_POLYGON
    else:  # one vertex per cell centre
        offsets, conn, cell_type = np.arange(len(xyz) + 1), np.arange(len(xyz)), VTK_VERTEX
    grid = vtkUnstructuredGrid()
    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(xyz), deep=1))
    grid.SetPoints(points)
    cells = vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(np.asarray(offsets, dtype=np.int64), deep=1),
                  numpy_to_vtkIdTypeArray(np.asarray(conn, dtype=np.int64), deep=1))
    grid.SetCells(cell_type, cells)
    return grid


class CatalystPublisher:
    """Feeds ``mesh`` / ``field`` messages to a Catalyst script as channel ``input``."""

    def __init__(self, script: str) -> None:
        from paraview.catalyst import bridge  # noqa: PLC0415

        self._bridge = bridge
        bridge.initialize()
        bridge.add_pipeline(script, 2)
        self._mesh: Optional[dict[str, Any]] = None
        self.step = 0

    def mesh(self, msg: dict[str, Any]) -> None:
        self._mesh = msg

    def field(self, msg: dict[str, Any], time: Optional[float] = None) -> None:
        """Publish one frame (cell field named after the label, e.g. ``|U|``)."""
        if self._mesh is None or not msg.get("values"):
            return
        from vtkmodules.util.numpy_support import numpy_to_vtk  # noqa: PLC0415

        grid = vtk_grid(self._mesh)
        values = numpy_to_vtk(decode(msg["values"], "<f4").astype(float), deep=1)
        values.SetName(msg.get("label") or msg["field"])
        grid.GetCellData().AddArray(values)
        grid.GetCellData().SetActiveScalars(values.GetName())
        t = time if time is not None else msg.get("time")
        self._bridge.coprocess(float(t if t is not None else self.step), self.step, grid, name="input")
        self.step += 1

    def finalize(self) -> None:
        self._bridge.finalize()
