"""Field view pane: the solver's field, rendered in situ (image) or sent by Catalyst Live (data).

The solver ranks hand their cells and fields to ParaView through Catalyst 2
(:mod:`neofoam_ui.catalyst`). In image mode rank 0 sends one PNG per frame with the
``frame`` message, shown as a data URL, rendered at the pane's size with the pane's camera
and colours; no field data passes through the UI. In data mode (only under ``pvpython``,
:mod:`neofoam_ui._ui._live`) Catalyst Live sends the shown source (surface, slice, clip or
the whole grid) and this process draws it interactively, with the same camera and colours.
The display options travel with the ``view`` command (``pipelines/view.py`` names them).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from typing import Any, Mapping, Optional

from neofoam_ui._ui._worker_link import _free_port
from neofoam_ui._ui.panes._field_view import _FIELD_CSS, _field_pane
from neofoam_ui.extensions import BasePane, PaneContext, PaneToggle
from neofoam_ui.protocol import CATALYST_LIVE_ENV

__all__ = ()

#: Seconds a camera, colour or cut setting must rest before the ranks render it (a slider, the
#: mouse wheel: each frame at a pause is one collective render).
_SETTLE = 0.25
_ZOOM = (0.25, 50.0)
#: Display settings and their defaults; they survive a restart (the camera, zoom, colours, cut).
_DEFAULTS = {"fv_show": "surface", "fv_axis": None, "fv_pos": 50, "fv_flip": False, "fv_camera": "reset",
             "fv_zoom": 1.0, "fv_range": "auto", "fv_lo": None, "fv_hi": None, "fv_cmap": "viridis",
             "fv_log": False, "fv_bounds": None, "fv_size": None}


def _paraview_web() -> bool:
    """ParaView with its web modules is importable (``pvpython`` of a full ParaView): what the
    interactive view needs. A Catalyst-only build (``PARAVIEW_ENABLE_WEB=OFF``) has none."""
    if importlib.util.find_spec("paraview") is None:
        return False
    return importlib.util.find_spec("paraview.modules.vtkPVClientWeb") is not None


# Without them the pane offers image mode only.
_LIVE = importlib.import_module("neofoam_ui._ui._live") if _paraview_web() else None


def _component_items(ncomp: int) -> list[dict[str, Any]]:
    if ncomp <= 1:
        return []
    names = "xyz" if ncomp == 3 else [str(i) for i in range(ncomp)]
    return [{"title": "magnitude", "value": "mag"}] + [
        {"title": n, "value": str(i)} for i, n in enumerate(names)]


def _fixed_range(s: Any) -> Optional[tuple[float, float]]:
    """The fixed colour range, if set and valid."""
    if s.fv_range != "fixed":
        return None
    try:
        lo, hi = float(s.fv_lo), float(s.fv_hi)
    except (TypeError, ValueError):  # a half-typed number
        return None
    return (lo, hi) if hi > lo else None


def _cut_options(s: Any) -> dict[str, str]:
    """What is shown (volume only in data mode) and where the cut is."""
    show = s.fv_show if s.fv_mode == "data" or s.fv_show != "volume" else "surface"
    if show == "surface":
        return {}
    options = {"show": show}
    if show in ("slice", "clip"):
        options["pos"] = f"{float(s.fv_pos) / 100:.4g}"
        if s.fv_axis:
            options["axis"] = s.fv_axis
        if show == "clip" and s.fv_flip:
            options["flip"] = "1"
    return options


def _image_options(s: Any) -> dict[str, str]:
    """Image size, camera and colours: rendered by the ranks in image mode."""
    options = {"camera": s.fv_camera, "zoom": f"{float(s.fv_zoom):.4g}"}
    if s.fv_size:
        width, height, scale = s.fv_size
        options.update(width=str(int(width)), height=str(int(height)), scale=f"{float(scale):.3g}")
    if s.fv_cmap != "viridis":
        options["cmap"] = s.fv_cmap
    if s.fv_log:
        options["log"] = "1"
    fixed = _fixed_range(s)
    if fixed:
        options["range"] = f"{fixed[0]:.9g}:{fixed[1]:.9g}"
    return options


def _options(s: Any) -> dict[str, str]:
    """The display options of the view request; data mode draws camera and colours itself."""
    options = _cut_options(s)
    return options if s.fv_mode == "data" else {**options, **_image_options(s)}


class _FieldPane(BasePane):
    """The field view; it also asks the worker for the frames every pane needs (``view``)."""

    name = "field"
    toggle = PaneToggle("fv_on", "CFD 3D", "mdi-cube-outline",
                        "a solver field on the mesh (Catalyst), refreshed at every pause")
    css = _FIELD_CSS

    def __init__(self, ctx: PaneContext) -> None:
        super().__init__(ctx)
        #: Last view request sent to the worker (so the worker's own choice is not echoed back).
        self._view_req: Optional[tuple[Any, ...]] = None
        self._settle: Optional[asyncio.TimerHandle] = None
        #: Render at every pause even with the panes closed (a ParaView is attached via Live).
        desktop = os.environ.get(CATALYST_LIVE_ENV, "off")
        self.live = desktop != "off"
        #: The data mode's Live listener and view (``None``: image mode only, see ``fv_data_why``).
        self.data: Any = None
        if self.live:
            self._data_why = f"Catalyst Live goes to the ParaView at {desktop} ({CATALYST_LIVE_ENV})"
        elif _LIVE is None:
            self._data_why = ("data mode needs the UI under the pvpython of a ParaView with web modules: "
                              "pvpython --venv <env> -m neofoam_ui.app …")
        else:
            self._data_why = ""
            self.data = _LIVE._LiveData(_free_port(), self._data_status)
        state = ctx.state
        state.change("fv_field", "fv_comp")(self._on_field)
        state.change("fv_mode")(self._on_mode)
        state.change("fv_show")(self._on_show)
        state.change("fv_axis", "fv_pos", "fv_flip")(lambda **_: self._soon())
        state.change("fv_range", "fv_lo", "fv_hi", "fv_cmap", "fv_log")(lambda **_: self._on_colours())
        state.change("fv_live")(lambda fv_live, **_: ctx.send({"cmd": "live", "on": bool(fv_live)}))
        state.change("fv_every", "fv_secs")(lambda **_: self._send_frames())
        ctx.server.trigger("nf_fv_size")(self._on_size)
        ctx.server.trigger("nf_fv_camera")(self.look)
        ctx.server.trigger("nf_fv_zoom")(self.zoom)

    def reset(self) -> None:
        s = self.ctx.state
        # The toggle, the choice and the display settings survive a restart.
        s.fv_on = bool(s.fv_on)
        s.fv_field = s.fv_field or None
        s.fv_comp = s.fv_comp or "mag"
        s.fv_fields = []
        s.fv_comps = []
        s.fv_src = None
        s.fv_mode = s.fv_mode if s.fv_mode == "data" and self.data is not None else "image"
        for key, default in _DEFAULTS.items():
            if s[key] is None:
                s[key] = default
        s.fv_data_ok = self.data is not None
        s.fv_data_why = self._data_why
        s.fv_data_shown = False
        s.fv_live = True if s.fv_live is None else bool(s.fv_live)
        # Live frames at most every fv_every time steps and fv_secs seconds apart.
        s.fv_every = s.fv_every if s.fv_every is not None else 1
        s.fv_secs = s.fv_secs if s.fv_secs is not None else 0.5
        s.fv_msg = "the field view starts once the solver context exists (after initialization)"

    @property
    def data_url(self) -> Optional[str]:
        """``host:port`` the data mode listens on (``None`` without data mode)."""
        return self.data.url if self.data is not None else None

    def build(self) -> None:
        _field_pane(self.data)

    def handlers(self) -> Mapping[str, Any]:
        return {"frame": self._on_frame}

    def spawned(self) -> None:
        self.ctx.send({"cmd": "live", "on": bool(self.ctx.state.fv_live)})
        self._send_frames()
        self._view_req = None  # a new worker has none
        self.send_view()

    def layout_changed(self) -> None:
        self.send_view()

    def wants_frames(self) -> bool:
        return bool(self.ctx.state.fv_on)

    def send_view(self) -> None:
        """Tell the worker what to render (and how); nothing at pauses when no pane wants frames."""
        s = self.ctx.state
        on = bool(self.ctx.wants_frames() or self.live)
        options = _options(s)
        req = (True, s.fv_field, s.fv_comp, s.fv_mode, tuple(sorted(options.items()))) if on else (False,)
        if req == self._view_req:
            return
        self._view_req = req
        self.ctx.send({"cmd": "view", "on": on, "field": s.fv_field, "component": s.fv_comp, "mode": s.fv_mode,
                       "options": options})

    def look(self, camera: str) -> None:
        """A camera preset (``reset``, ``+x`` … ``-z``), at zoom 1."""
        s = self.ctx.state
        s.fv_camera, s.fv_zoom = camera, 1.0
        self._camera_changed()

    def zoom(self, factor: float) -> None:
        """Zoom by ``factor`` (bounded)."""
        s = self.ctx.state
        s.fv_zoom = min(max(float(s.fv_zoom) * float(factor), _ZOOM[0]), _ZOOM[1])
        self._camera_changed()

    def _camera_changed(self) -> None:
        s = self.ctx.state
        if s.fv_mode == "data" and self.data is not None:
            self.data.look(s.fv_camera, float(s.fv_zoom))
            return
        self._soon()

    def _soon(self) -> None:
        """:meth:`send_view` once the settings rested for :data:`_SETTLE` seconds."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no event loop (a script driving the pane): now
            self.send_view()
            return
        if self._settle is not None:
            self._settle.cancel()
        self._settle = loop.call_later(_SETTLE, self.send_view)

    def _on_size(self, width: int, height: int, scale: float) -> None:
        self.ctx.state.fv_size = [width, height, scale]
        self.send_view()  # already settled in the browser

    def _on_field(self, **_: Any) -> None:
        self.ctx.state.fv_range = "auto"  # a fixed range belongs to the field it was set for
        self.send_view()

    def _on_mode(self, fv_mode: str, **_: Any) -> None:
        s = self.ctx.state
        if fv_mode == "image" and s.fv_show == "volume":
            s.fv_show = "surface"  # an image shows the volume's surface anyway
        if fv_mode == "data" and self.data is not None:
            self.data.listen()  # before the worker's next frame tries to connect
            self._style_data()
            self.data.look(s.fv_camera, float(s.fv_zoom))
        self.send_view()

    def _on_show(self, fv_show: str, **_: Any) -> None:
        if self.data is not None:
            self.data.extract("grid" if fv_show == "volume" else fv_show)
        self.send_view()

    def _on_colours(self) -> None:
        if self.ctx.state.fv_mode == "data" and self.data is not None:
            self._style_data()
            self._show_note(self.data.note)
            return
        self._soon()

    def _style_data(self) -> None:
        s = self.ctx.state
        self.data.style(s.fv_cmap, _fixed_range(s), bool(s.fv_log))

    def _show_note(self, note: str) -> None:
        s = self.ctx.state
        base = str(s.fv_msg or "").split(" · ⚠ ")[0]
        s.fv_msg = f"{base} · ⚠ {note}" if note else base

    def _data_status(self, text: str) -> None:
        with self.ctx.state:
            if not self.ctx.state.fv_data_shown:
                self.ctx.state.fv_msg = text

    def _send_frames(self) -> None:
        s = self.ctx.state
        try:
            every, secs = max(1, int(s.fv_every)), max(0.0, float(s.fv_secs))
        except (TypeError, ValueError):  # a half-typed number
            return
        self.ctx.send({"cmd": "frames", "every": every, "seconds": secs})

    def _adopt_report(self, report: Mapping[str, Any]) -> list[str]:
        """The domain bounds and default cut axis of a frame; its notes."""
        s = self.ctx.state
        if report.get("bounds"):
            s.fv_bounds = report["bounds"]
        if s.fv_axis is None and report.get("axis"):
            s.fv_axis = report["axis"]
        return list(report.get("notes") or [])

    def _on_frame(self, msg: dict[str, Any]) -> None:
        s = self.ctx.state
        if msg.get("error"):
            s.fv_msg = msg["error"]
            s.fv_src = None
            s.fv_data_shown = False
            return
        if self._view_req is None or self._view_req[:4] != (True, *msg.get("view", (msg["field"], msg["component"]))):
            return  # rendered for an earlier choice (a live frame in flight): keep the user's
        fields = msg["fields"]
        s.fv_fields = [{"title": f["name"] + (" (vector)" if f["ncomp"] == 3 else f" ({f['ncomp']} comp.)" if f["ncomp"] > 1 else ""),
                        "value": f["name"]} for f in fields]
        ncomp = next((f["ncomp"] for f in fields if f["name"] == msg["field"]), 1)
        s.fv_comps = _component_items(ncomp)
        # The worker may have picked the field (none chosen yet / not cell-sized): adopt it.
        # Echoing it back is harmless (the same frame is not rendered twice).
        s.fv_field, s.fv_comp = msg["field"], msg["component"]
        notes = self._adopt_report(msg.get("report") or {})
        if msg["mode"] == "data":
            s.fv_data_shown = self.data.show(msg["field"], msg["component"])
            notes += [self.data.note] if self.data.note else []
            how = f"data via Catalyst Live ({'volume' if s.fv_show == 'volume' else s.fv_show})"
        else:
            s.fv_src = "data:image/png;base64," + msg["image"]
            how = "rendered in situ (Catalyst)"
        t = msg.get("time")
        ranks = f"{self.ctx.np} ranks" if self.ctx.np > 1 else ""
        parts = [f"t = {t:.6g}" if isinstance(t, (int, float)) else "", how, ranks]
        s.fv_msg = " · ".join(p for p in parts + [f"⚠ {n}" for n in notes] if p)
