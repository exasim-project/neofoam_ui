"""The field view's data mode: Catalyst Live into this process's ParaView, drawn by trame.

Needs the UI under the ``pvpython`` of a ParaView with web modules (trame-vtk). The worker's
pipeline publishes to Live only for frames in data mode; only the extracted sources travel:
the inputs of the shown scene's Displays (each frame names them, ``report["extracts"]``, with
each Display's settings). Each Display is drawn here as the solver would: coloured by its field
(map, range, log scale, bar) or solid, with its opacity and line width.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Sequence

from paraview import live, servermanager, simple
from trame.app import asynchronous
from trame.widgets import paraview as paraview_widgets
from vtkmodules.vtkRenderingFreeType import vtkMathTextUtilities

from neofoam_ui.scenes import camera_fit

__all__ = ()

#: Seconds between two looks at the Live socket: the solver waits for this process at every
#: data frame (the Live handshake), so it is short.
_POLL = 0.02
_COMPONENT = {"mag": "Magnitude", "0": "X", "1": "Y", "2": "Z"}
#: The colour maps of a Display, by ParaView preset (as ``pipelines/view.py`` draws them).
CMAPS = {"viridis": "Viridis (matplotlib)", "coolwarm": "Cool to Warm", "inferno": "Inferno (matplotlib)",
         "jet": "Jet", "grayscale": "Grayscale"}
LOG_OFF = "log scale off: the colour range reaches 0 or below"
#: Until a frame names the extracts: the built-in Field scene's surface.
_FIELD_SURFACE = [{"id": "display", "source": "Field:surface", "field": None}]


def _magnitude(field: str) -> str:
    """``|U|``: VTK's matplotlib text takes "|" for a table cell separator, so as math there."""
    math = vtkMathTextUtilities.GetInstance()
    return f"$\\vert${field}$\\vert$" if math is not None and math.IsAvailable() else f"|{field}|"


def _cells(source: Any) -> int:
    return int(source.GetDataInformation().GetNumberOfCells()) if source is not None else 0


class _LiveData:
    """One Live listener (started on the first :meth:`listen`) and the view that draws its data."""

    def __init__(self, port: int, on_status: Callable[[str], None]) -> None:
        #: What the worker connects to (its ``--data``).
        self.url = f"localhost:{port}"
        self._port = port
        self._on_status = on_status
        # ParaView resets a view's camera at its first render: here the data may come after the
        # fit (a slower solver, MPI), and the reset would replace the scene's camera
        simple._DisableFirstRenderCameraReset()
        self.view = simple.CreateRenderView()
        self.view.OrientationAxesVisibility = 0
        self._link: Any = None
        self._connected = False
        self._reconnect = False
        #: The Displays to draw (a frame's ``extracts``) and what Live extracts for them, by name.
        self.specs: list[dict[str, Any]] = list(_FIELD_SURFACE)
        self.sources: dict[str, Any] = {}
        self._reps: dict[str, Any] = {}
        self._widget: Any = None
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

    def show(self, specs: list[dict[str, Any]]) -> bool:
        """Draw these Displays (from the next data frame on, for a source not extracted yet);
        whether there is data to show."""
        if specs:
            self.specs = specs
            self._extract()
        return self._draw()

    @property
    def requested(self) -> str:
        """The sources the Displays need (comma-separated): a new one needs another data frame."""
        return ",".join(sorted({spec["source"] for spec in self.specs}))

    def fit(self, camera: str, zoom: float, bounds: Sequence[float], size: Sequence[float]) -> None:
        """The scene's View: look from ``camera`` at ``zoom``, the domain (``bounds``) fitted to a
        view of ``size`` pixels, as the solver's image does; then render it (a still image)."""
        fit = camera_fit(camera, zoom, bounds, size)
        view = self.view
        view.CameraFocalPoint, view.CameraPosition, view.CameraViewUp = fit["focal"], fit["position"], fit["up"]
        view.CameraParallelProjection = 1
        view.CameraParallelScale = fit["scale"]
        if self._widget is not None:
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
        self._extract()
        self._on_status("connected")

    def _on_closed(self, *_: Any) -> None:
        self._connected = False
        for name in list(self.sources):
            self._drop(name)
        self._reconnect = True

    def _extract(self) -> None:
        """Ask Live for the sources the Displays need; drop the others."""
        if not self._connected:
            return
        wanted = {spec["source"] for spec in self.specs}
        for name in set(self.sources) - wanted:
            self._drop(name)
        for name in wanted - set(self.sources):
            self.sources[name] = live.ExtractCatalystData(self._link, name)

    def _drop(self, name: str) -> None:
        source = self.sources.pop(name)
        for display_id in [k for k, rep in self._reps.items() if rep.Input == source]:
            self._forget(display_id)
        if self._connected:
            self._link.RemoveExtract(source.SMProxy)
        simple.Delete(source)

    def _forget(self, display_id: str) -> None:
        rep = self._reps.pop(display_id)
        rep.SetScalarBarVisibility(self.view, False)
        self.view.Representations.remove(rep)
        simple.Delete(rep)

    def _draw(self) -> bool:
        """Show each Display with data; fit the camera once."""
        shown = False
        for display_id in set(self._reps) - {spec["id"] for spec in self.specs}:
            self._forget(display_id)
        for spec in self.specs:
            source = self.sources.get(spec["source"])
            if source is None:
                continue
            source.UpdatePipeline()
            if not _cells(source):
                continue
            self._style(self._rep(spec["id"], source), source, spec)
            shown = True
        simple.HideUnusedScalarBars(self.view)  # those of Displays gone (another scene)
        if not shown:
            return False
        if self._widget is not None:
            self._widget.update()
        return True

    def _rep(self, display_id: str, source: Any) -> Any:
        rep = self._reps.get(display_id)
        if rep is not None and rep.Input != source:
            self._forget(display_id)
            rep = None
        if rep is None:  # one each: two Displays may draw one source
            rep = self._reps[display_id] = servermanager.CreateRepresentation(source, self.view)
        return rep

    def _style(self, rep: Any, source: Any, spec: dict[str, Any]) -> None:
        rep.Visibility = 1
        rep.Opacity = float(spec.get("opacity", 1.0))
        rep.LineWidth = float(spec.get("line_width", 1.0))
        rep.AmbientColor = rep.DiffuseColor = list(spec.get("color", [1.0, 1.0, 1.0]))
        field = spec.get("field")
        association = "CELLS" if field in source.CellData.keys() else "POINTS"
        arrays = source.CellData if association == "CELLS" else source.PointData
        if not field or field not in arrays.keys():
            rep.ColorArrayName = ["POINTS", ""]
            rep.SetScalarBarVisibility(self.view, False)
            return
        self._colour_by(rep, arrays[field], association, spec)

    def _colour_by(self, rep: Any, array: Any, association: str, spec: dict[str, Any]) -> None:
        field, component = array.GetName(), spec.get("component", "mag")
        ncomp = array.GetNumberOfComponents()
        if ncomp > 1:
            simple.ColorBy(rep, (association, field, _COMPONENT.get(component, "Magnitude")), separate=True)
        else:
            simple.ColorBy(rep, (association, field), separate=True)
        lut = rep.LookupTable
        lut.ApplyPreset(CMAPS.get(spec.get("colormap", "viridis"), CMAPS["viridis"]), True)
        index = -1 if ncomp == 1 or component == "mag" else int(component)
        fixed = spec.get("range", "auto")
        lo, hi = fixed if fixed != "auto" else array.GetRange(index)
        log = bool(spec.get("log")) and lo > 0
        self.note = LOG_OFF if spec.get("log") and not log else ""
        lut.UseLogScale = 0  # a log scale would clamp a range reaching 0 or below
        lut.RescaleTransferFunction(lo, hi if hi > lo else lo + 1e-30)
        lut.UseLogScale = int(log)
        rep.SetScalarBarVisibility(self.view, bool(spec.get("bar", True)))
        bar = simple.GetScalarBar(lut, self.view)
        name = "xyz"[index] if ncomp == 3 else str(index)
        bar.Title = field if ncomp == 1 else (_magnitude(field) if index < 0 else f"{field}.{name}")
        bar.ComponentTitle = ""
