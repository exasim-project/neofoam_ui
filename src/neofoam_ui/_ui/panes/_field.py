"""Field view pane: the solver's field, rendered in situ (image) or sent by Catalyst Live (data).

The solver ranks hand their cells and fields to ParaView through Catalyst 2
(:mod:`neofoam_ui.catalyst`), which draws the current scene (:mod:`neofoam_ui.scenes`, picked
here or in the Scenes panel, :mod:`._scenes`): its filters, displays and camera. In image mode
rank 0 sends one PNG per frame with the ``frame`` message, shown as a data URL, rendered at
the pane's size; no field data passes through the UI. In data mode (only under ``pvpython``,
:mod:`neofoam_ui._ui._live`, and only for the built-in ``Field`` scene for now) Catalyst Live
sends the surface and this process draws it interactively. The scene travels with the
``view`` command's options (``pipelines/view.py`` names them).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from typing import Any, Mapping, Optional

from neofoam_ui._ui._worker_link import _free_port
from neofoam_ui._ui.panes._field_view import _FIELD_CSS, _field_pane
from neofoam_ui._ui.panes._scenes import _SCENES_CSS, _register, _Scenes, _scenes_panel
from neofoam_ui.extensions import BasePane, PaneContext, PaneToggle
from neofoam_ui.protocol import CATALYST_LIVE_ENV
from neofoam_ui.scenes import FIELD

__all__ = ()

#: Seconds a camera setting or another scene must rest before the ranks render it (the mouse
#: wheel: each frame at a pause is one collective render).
_SETTLE = 0.25
_ZOOM = (0.25, 50.0)


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


class _FieldPane(BasePane):
    """The field view; it also asks the worker for the frames every pane needs (``view``)."""

    name = "field"
    toggle = PaneToggle("fv_on", "CFD 3D", "mdi-cube-outline",
                        "a solver field on the mesh (Catalyst), refreshed at every pause")
    css = _FIELD_CSS + _SCENES_CSS

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
        #: The domain's bounds (the last frame's report): what the data view's camera fits.
        self._bounds: Optional[list[float]] = None
        self.scenes = _Scenes(ctx.state, ctx.case, self._on_scene)
        _register(ctx.server, self.scenes)
        ctx.server.trigger("nf_sc_export")(self.export)
        state = ctx.state
        state.change("fv_field", "fv_comp")(lambda **_: self.send_view())
        state.change("fv_mode")(self._on_mode)
        state.change("fv_live")(lambda fv_live, **_: ctx.send({"cmd": "live", "on": bool(fv_live)}))
        state.change("fv_every", "fv_secs")(lambda **_: self._send_frames())
        ctx.server.trigger("nf_fv_size")(self._on_size)
        ctx.server.trigger("nf_fv_camera")(self.look)
        ctx.server.trigger("nf_fv_zoom")(self.zoom)

    def reset(self) -> None:
        s = self.ctx.state
        # The toggle, the choice, the scene and the image size survive a restart.
        s.fv_on = bool(s.fv_on)
        s.fv_field = s.fv_field or None
        s.fv_comp = s.fv_comp or "mag"
        s.fv_fields = []
        s.fv_comps = []
        s.fv_src = None
        s.fv_size = s.fv_size or None
        s.sc_current = s.sc_current or FIELD
        s.sc_rename = False
        s.sc_confirm = False
        s.sc_new_name = ""
        s.sc_msg = ""
        self.scenes.load()
        s.fv_mode = s.fv_mode if s.fv_mode == "data" and self._data_ok() else "image"
        self._data_offer()
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

    def build_scenes(self) -> None:
        """The Scenes side panel."""
        _scenes_panel()

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

    def options(self) -> dict[str, str]:
        """The view options: the current scene and, in image mode, the image's size."""
        s = self.ctx.state
        options = self.scenes.options()
        if s.fv_mode == "data" and self.data is not None:
            # what Live extracts for this process: when it changes, the frame is sent again with them
            options["extracts"] = self.data.requested
        if s.fv_mode != "data" and s.fv_size:
            width, height, scale = s.fv_size
            options.update(width=str(int(width)), height=str(int(height)), scale=f"{float(scale):.3g}")
        return options

    def send_view(self) -> None:
        """Tell the worker what to render (and how); nothing at pauses when no pane wants frames."""
        s = self.ctx.state
        on = bool(self.ctx.wants_frames() or self.live)
        options = self.options()
        req = (True, s.fv_field, s.fv_comp, s.fv_mode, options["scene"], tuple(sorted(options.items()))) \
            if on else (False,)
        if req == self._view_req:
            return
        self._view_req = req
        self.ctx.send({"cmd": "view", "on": on, "field": s.fv_field, "component": s.fv_comp, "mode": s.fv_mode,
                       "options": options})

    def export(self) -> None:
        """Write the current scene as a Catalyst script next to its file (the next frame does)."""
        s = self.ctx.state
        if s.fv_mode == "data":
            s.sc_msg = "export from image mode (data mode draws here, not in the solver)"
            return
        path = self.scenes.export_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        s.sc_msg = f"exporting to {path.name} …"
        self.ctx.send({"cmd": "export", "path": str(path)})

    def look(self, camera: str) -> None:
        """A camera preset (``reset``, ``+x`` … ``-z``), at zoom 1: the scene's View node (unsaved)."""
        self.scenes.edit_view(camera, 1.0)

    def zoom(self, factor: float) -> None:
        """Zoom by ``factor`` (bounded)."""
        props = self.scenes.view_props()
        self.scenes.edit_view(props["camera"], min(max(float(props["zoom"]) * float(factor), _ZOOM[0]), _ZOOM[1]))

    def _refit(self) -> None:
        """Data mode: the data view looks as the scene's View says, fitted to the domain and the pane."""
        s = self.ctx.state
        if s.fv_mode != "data" or self.data is None or self._bounds is None:
            return
        props = self.scenes.view_props()
        width, height = (s.fv_size or [800, 600])[:2]
        self.data.fit(props["camera"], float(props["zoom"]), self._bounds, (width, height))

    def _on_scene(self) -> None:
        """Another scene, or the current one edited: render it once settled."""
        s = self.ctx.state
        if s.fv_mode == "data" and not self._data_ok():
            s.fv_mode = "image"
        self._data_offer()
        self._refit()
        self._soon()

    def _data_ok(self) -> bool:
        return self.data is not None

    def _data_offer(self) -> None:
        """Data mode on offer, or why not."""
        s = self.ctx.state
        s.fv_data_ok = self._data_ok()
        s.fv_data_why = self._data_why

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
        self._refit()
        self.send_view()  # already settled in the browser

    def _on_mode(self, fv_mode: str, **_: Any) -> None:
        if fv_mode == "data" and self.data is not None:
            self.data.listen()  # before the worker's next frame tries to connect
            self._refit()
        self.send_view()

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

    def _on_frame(self, msg: dict[str, Any]) -> None:
        s = self.ctx.state
        if msg.get("scene", FIELD) != self.scenes.current:
            return  # rendered for another scene (a frame in flight)
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
        report = msg.get("report") or {}
        notes = list(report.get("notes") or [])
        self._bounds = report.get("bounds") or self._bounds
        if report.get("exported"):
            s.sc_msg = f"exported {report['exported']}"
        if msg["mode"] == "data":
            shown = self.data.show(report.get("extracts") or [])
            first, s.fv_data_shown = shown and not s.fv_data_shown, shown
            if first:  # fitted once the view shows (it had no size while hidden)
                self._refit()
            self.send_view()  # a source not extracted before comes with the next frame
            notes += [self.data.note] if self.data.note else []
            how = "data via Catalyst Live"
        else:
            s.fv_src = "data:image/png;base64," + msg["image"]
            how = "rendered in situ (Catalyst)"
        t = msg.get("time")
        ranks = f"{self.ctx.np} ranks" if self.ctx.np > 1 else ""
        parts = [f"t = {t:.6g}" if isinstance(t, (int, float)) else "", f"scene {self.scenes.title(msg.get('scene', FIELD))}", how, ranks]
        s.fv_msg = " · ".join(p for p in parts + [f"⚠ {n}" for n in notes] if p)
