"""The field view's data mode: Catalyst Live into this process's ParaView, drawn by trame.

Needs the UI under the ``pvpython`` of a ParaView with web modules (trame-vtk). The worker's
pipeline publishes to Live only for frames in data mode; only the extracted source travels.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from paraview import live, simple
from trame.app import asynchronous
from trame.widgets import paraview as paraview_widgets

__all__ = ()

#: Seconds between two looks at the Live socket: the solver waits for this process at every
#: data frame (the Live handshake), so it is short.
_POLL = 0.02
_COMPONENT = {"mag": "Magnitude", "0": "X", "1": "Y", "2": "Z"}
#: The colour maps the pane offers, by ParaView preset (as ``pipelines/view.py`` draws them).
CMAPS = {"viridis": "Viridis (matplotlib)", "coolwarm": "Cool to Warm", "inferno": "Inferno (matplotlib)",
         "jet": "Jet", "grayscale": "Grayscale"}
LOG_OFF = "log scale off: the colour range reaches 0 or below"


def _look(camera: str, bounds: tuple[float, ...]) -> tuple[int, float]:
    """The axis looked along and the side looked from: a preset (``+x`` …), else the thinnest axis."""
    if camera in ("+x", "-x", "+y", "-y", "+z", "-z"):
        return "xyz".index(camera[1]), 1.0 if camera[0] == "+" else -1.0
    extent = [bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]]
    return extent.index(min(extent)), 1.0


class _LiveData:
    """One Live listener (started on the first :meth:`listen`) and the view that draws its data."""

    def __init__(self, port: int, on_status: Callable[[str], None]) -> None:
        #: What the worker connects to (``NEOFOAM_UI_CATALYST_DATA``).
        self.url = f"localhost:{port}"
        self._port = port
        self._on_status = on_status
        self.view = simple.CreateRenderView()
        self.view.OrientationAxesVisibility = 0
        self._link: Any = None
        self._connected = False
        self._reconnect = False
        #: The source of the solver's pipeline to extract (``surface`` or ``grid``).
        self.name = "surface"
        self._source: Any = None
        self._colour: tuple[Optional[str], str] = (None, "mag")
        self._fitted = False
        self._widget: Any = None
        self._style: tuple[str, Optional[tuple[float, float]], bool] = ("viridis", None, False)
        self._camera: tuple[str, float] = ("reset", 1.0)
        #: Why a colour setting is not applied (a log scale over values ≤ 0), else ``""``.
        self.note = ""

    def widget(self) -> None:
        """The interactive view (inside the pane's template)."""
        self._widget = paraview_widgets.VtkRemoteView(self.view, interactive_ratio=1)

    def listen(self) -> None:
        """Start listening for the solver (once; again after each disconnection)."""
        if self._link is not None:
            return
        self._link = live.ConnectToCatalyst("localhost", self._port)
        self._link.AddObserver("ConnectionCreatedEvent", self._on_connected)
        self._link.AddObserver("ConnectionClosedEvent", self._on_closed)
        self._link.AddObserver("UpdateEvent", lambda *_: self._draw())
        self._on_status(f"waiting for the solver on Catalyst Live ({self.url})")
        asynchronous.create_task(self._poll())

    def extract(self, name: str) -> None:
        """Extract ``name`` instead (from the next data frame on); the other one stops travelling."""
        if name == self.name:
            return
        self.name = name
        if self._connected:
            self._drop()
            self._source = live.ExtractCatalystData(self._link, name)

    def show(self, field: Optional[str], component: str) -> bool:
        """Colour by ``field`` (``component``: ``mag`` or an index); whether there is data to show."""
        self._colour = (field, component)
        return self._draw()

    def style(self, cmap: str, value_range: Optional[tuple[float, float]], log: bool) -> bool:
        """Colour map, fixed range (``None``: the data's) and log scale; whether there is data."""
        self._style = (cmap, value_range, log)
        return self._draw()

    def look(self, camera: str, zoom: float) -> None:
        """Look from a preset side (``+x`` … ``-z``; ``reset``: along the thinnest axis), fitted, zoomed."""
        self._camera = (camera, zoom)
        if self._fit() and self._widget is not None:
            self._widget.update()

    async def _poll(self) -> None:
        while self._link is not None:
            live.ProcessServerNotifications()
            if self._reconnect:  # not inside the link's own callback
                self._reconnect, self._link = False, None
                self.listen()
                return
            await asyncio.sleep(_POLL)

    def _on_connected(self, *_: Any) -> None:
        self._connected = True
        self._source = live.ExtractCatalystData(self._link, self.name)
        self._on_status("connected")

    def _on_closed(self, *_: Any) -> None:
        self._connected = False
        self._drop()
        self._fitted = False
        self._reconnect = True

    def _drop(self) -> None:
        if self._source is None:
            return
        simple.Hide(self._source, self.view)
        if self._connected:
            self._link.RemoveExtract(self._source.SMProxy)
        simple.Delete(self._source)
        self._source = None

    def _draw(self) -> bool:
        """Show the extracted data, coloured by the viewed field; fit the camera once."""
        source = self._source
        if source is None:
            return False
        source.UpdatePipeline()
        if not source.GetDataInformation().GetNumberOfCells():
            return False
        display = simple.Show(source, self.view)
        if self._colour[0] in source.CellData.keys():
            self._colour_by(display, source)
        if not self._fitted:
            self._fitted = self._fit()
        if self._widget is not None:
            self._widget.update()
        return True

    def _colour_by(self, display: Any, source: Any) -> None:
        field, component = self._colour
        cmap, value_range, log = self._style
        ncomp = source.CellData[field].GetNumberOfComponents()
        if ncomp > 1:
            simple.ColorBy(display, ("CELLS", field, _COMPONENT.get(component, "Magnitude")))
        else:
            simple.ColorBy(display, ("CELLS", field))
        lut = simple.GetColorTransferFunction(field)
        lut.ApplyPreset(CMAPS.get(cmap, CMAPS["viridis"]), True)
        index = -1 if ncomp == 1 or component == "mag" else int(component)
        lo, hi = value_range or source.CellData[field].GetRange(index)
        use_log = log and lo > 0
        self.note = LOG_OFF if log and not use_log else ""
        lut.UseLogScale = 0  # a log scale would clamp a range reaching 0 or below
        lut.RescaleTransferFunction(lo, hi if hi > lo else lo + 1e-30)
        lut.UseLogScale = int(use_log)
        simple.HideUnusedScalarBars(self.view)
        display.SetScalarBarVisibility(self.view, True)
        bar = simple.GetScalarBar(lut, self.view)
        name = "xyz"[index] if ncomp == 3 else str(index)
        bar.Title = field if ncomp == 1 else (f"|{field}|" if index < 0 else f"{field}.{name}")
        bar.ComponentTitle = ""

    def _fit(self) -> bool:
        """Camera of :meth:`look` on the extracted data; whether there was data to fit."""
        if self._source is None or not self._source.GetDataInformation().GetNumberOfCells():
            return False
        b = self._source.GetDataInformation().GetBounds()
        camera, zoom = self._camera
        axis, sign = _look(camera, b)
        centre = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
        position = list(centre)
        position[axis] += sign * 10 * max(b[1] - b[0], b[3] - b[2], b[5] - b[4], 1e-30)
        up = [0.0, 0.0, 0.0]
        up[1 if axis != 1 else 2] = 1.0
        self.view.CameraFocalPoint, self.view.CameraPosition, self.view.CameraViewUp = centre, position, up
        self.view.ResetCamera(False)
        self.view.GetActiveCamera().Zoom(zoom)
        return True
