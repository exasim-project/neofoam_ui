"""The field view of a session: render the viewed field in situ, at pauses and while running.

The data stays in the solver: each rank hands its cells and fields to a :class:`Renderer`
(by default Catalyst 2, :mod:`neofoam_ui.catalyst`), which writes one image; only its file
name goes to the UI in a ``frame`` message, with the pipeline diagram when it changed.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from neofoam_ui import catalyst
from neofoam_ui.engine._protocols import RenderBackend, Renderer
from neofoam_ui.engine._regions import _regions_of
from neofoam_ui.engine._tracking import _fingerprint
from neofoam_ui.geometry import cell_count

if TYPE_CHECKING:
    from neofoam_ui.engine._session import Session

__all__ = ()

#: Seconds after which a live frame reads the renderer's pipeline back again (colour range etc.).
_DESCRIBE_EVERY = 5.0


class _CatalystBackend:
    """The default :class:`RenderBackend`: Catalyst 2 + ParaView, configured by the environment.

    :mod:`neofoam_ui.catalyst` is looked up at each call (Catalyst itself is loaded by the first
    :class:`~neofoam_ui.catalyst.InSitu`)."""

    def unavailable(self) -> Optional[str]:
        if catalyst.configured():
            return None
        return f"in-situ view off: set {catalyst.ENV} to a Catalyst + ParaView install"

    def publishes_all(self) -> bool:
        return catalyst.custom_script()

    def create(self) -> Renderer:
        return catalyst.InSitu()


class _FieldView:
    """What the view shows, when it renders, and the frames it sends (for one session)."""

    def __init__(self, session: Session, backend: Optional[RenderBackend] = None) -> None:
        self._s = session
        self._backend = backend or _CatalystBackend()
        self._regions = _regions_of(session.solver)
        #: ``(field, component)`` to render at every pause, ``None`` = view closed.
        #: Replaced as a whole (:meth:`request`), so another thread may set it while running.
        self.view: Optional[tuple[Optional[str], str]] = None
        #: Counts closings of the view: a reopened view renders again even if nothing changed.
        self._epoch = 0
        self._steps_since_frame = 0
        self._frame_at = 0.0
        self.renderer: Any = None  # started on the first frame
        self._error: Optional[str] = None
        #: Geometry per region: absent = not built yet, ``False`` = the region has none.
        self._geometry: dict[str, Any] = {}
        self._sent: Optional[tuple[Any, ...]] = None
        #: The pipeline as the renderer last ran it (:meth:`Renderer.describe`, rank 0).
        self.pipeline: Optional[dict[str, Any]] = None
        self._pipeline_sent: Optional[str] = None
        self._describe_failed = False  # reported once, then no more diagrams
        self._described: Optional[tuple[Any, float]] = None  # (params, when) of the last read-back
        self.describe_every = _DESCRIBE_EVERY

    # -- requests -------------------------------------------------------------------------

    def request(self, field: Optional[str], component: str = "mag", on: bool = True) -> None:
        """Open or close the view from any thread; the next frame (live or pause) shows it."""
        if not on:
            self._epoch += 1  # before the swap: whoever sees the new view sees the new epoch
        self.view = (field, component) if on else None

    def time_step(self) -> None:
        """A time step while running: a frame if ``frame_every`` steps and ``frame_seconds`` passed."""
        s = self._s
        self._steps_since_frame += 1
        if self.view is None or self._steps_since_frame < max(1, s.frame_every):
            return
        if time.monotonic() - self._frame_at < s.frame_seconds:
            return
        self._steps_since_frame = 0
        self.send()

    # -- one frame ------------------------------------------------------------------------

    def _cells(self) -> Optional[tuple[str, dict[str, Any], dict[str, np.ndarray]]]:
        """The viewed region (the first), its geometry (built once) and its cell-sized fields;
        ``None`` = no geometry."""
        s = self._s
        region = self._regions(s.ctx)[0]
        geom = self._geometry.get(region.name)
        if geom is None:
            try:
                geom = region.mesh(s.ctx)
            except Exception:  # noqa: BLE001 - no view rather than a dead session
                s.emit({"type": "stdout", "text": "field view: " + traceback.format_exc()})
            geom = self._geometry[region.name] = geom or False
        if not geom:
            return None
        n = cell_count(geom)
        fields = {k: np.asarray(v) for k, v in region.fields(s.ctx).items()}
        return region.name, geom, {k: v for k, v in fields.items() if v.ndim in (1, 2) and len(v) == n}

    def _published(self, fields: dict[str, np.ndarray], params: dict[str, str]) -> dict[str, np.ndarray]:
        """What the renderer gets: the viewed field, or all for a renderer that uses them all."""
        if self._backend.publishes_all() or params.get("field") not in fields:
            return fields
        return {params["field"]: fields[params["field"]]}

    def _fail(self, text: str) -> None:
        if self._sent != ("error", self._epoch, text):
            self._sent = ("error", self._epoch, text)
            self._s.emit({"type": "frame", "file": None, "fields": [], "error": text})

    def send(self) -> None:
        """Render the viewed field if the view is open and the values changed; send the frame."""
        s = self._s
        view = self.view  # read once: the command reader may replace it meanwhile
        if view is None or s.ctx is None:
            return
        if self._error:
            return self._fail(self._error)
        why = self._backend.unavailable()
        if why is not None:
            return self._fail(why)
        cells = self._cells()
        if cells is None:
            return self._fail(f"{s.solver.name} provides no mesh geometry")
        region, _, fields = cells
        name, comp = view
        if name not in fields:
            name = next((k for k in ("U", "p", "T") if k in fields), next(iter(fields), None))
        if name is None:
            return
        arr = fields[name]
        ncomp = int(arr.shape[1]) if arr.ndim == 2 else 1
        if ncomp == 1 or comp not in ["mag"] + [str(i) for i in range(ncomp)]:
            comp = "mag"
        key = (self._epoch, region, name, comp, _fingerprint(arr))
        if key == self._sent:
            return
        self._sent = key
        self._frame_at = time.monotonic()
        params = {"field": name, "component": comp}
        if s.peers is not None:
            s.peers.render(params)
        file = self.render(params, describe=True)
        if file is None:
            return self._fail(self._error or "in-situ view failed")
        frame = {
            "type": "frame", "file": file, "field": name, "component": comp,
            "view": list(view),  # the request this frame answers (field/component may be a fallback)
            "time": s.solver.scalars(s.ctx).get("time"),
            "fields": [{"name": k, "ncomp": int(v.shape[1]) if v.ndim == 2 else 1}
                       for k, v in fields.items()],
        }
        if self.pipeline is not None:
            # The graph and its settings only when they changed; the timings with every frame.
            graph = {k: v for k, v in self.pipeline.items() if k != "ms"}
            key_ = json.dumps(graph, sort_keys=True)
            if key_ != self._pipeline_sent:
                self._pipeline_sent = key_
                frame["pipeline"] = graph
            frame["pipeline_ms"] = self.pipeline.get("ms", {})
        s.emit(frame)
        prune = getattr(self.renderer, "prune", None)
        if prune is not None:
            prune(s.keep_frames)
        if s.wait_ui and s.barrier is not None:
            s.barrier()

    def render(self, params: dict[str, str], describe: bool = False) -> Optional[str]:
        """Hand this rank's cells and fields to the renderer (collective); the image file name.

        ``describe``: afterwards also read back the pipeline (:attr:`pipeline`); one rank does.
        """
        s = self._s
        out: list[Optional[str]] = [None]

        def run() -> None:
            cells = self._cells() if s.ctx is not None else None
            if cells is None:
                return
            if self.renderer is None:
                self.renderer = self._backend.create()
            time_value = s.solver.scalars(s.ctx).get("time") or 0.0
            out[0] = self.renderer.execute(cells[1], self._published(cells[2], params), time_value, params)
            if describe and not self._describe_failed and hasattr(self.renderer, "describe"):
                try:
                    self._describe(params)
                except Exception:  # noqa: BLE001 - a frame without its diagram is still a frame
                    self.pipeline, self._describe_failed = None, True
                    s.emit({"type": "stdout", "text": "pipeline diagram: " + traceback.format_exc()})

        def guarded() -> None:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - the stepper outlives a broken view
                self._error = f"in-situ view failed: {exc!r}"
                s.emit({"type": "stdout", "text": "Catalyst: " + traceback.format_exc()})

        if self._error is None:
            s.in_solver_thread(guarded)
        return out[0]

    def _describe(self, params: dict[str, str]) -> None:
        """Read the pipeline back when it may have changed: the first frame, another field or
        component, or :attr:`describe_every` seconds on (colour range); else only the timings."""
        now = time.monotonic()
        last = self._described
        if self.pipeline is None or last is None or last[0] != params or now - last[1] >= self.describe_every:
            self.pipeline = self.renderer.describe()
            self._described = (dict(params), now)
            return
        ms = getattr(self.renderer, "ms", None)
        if isinstance(ms, dict):
            self.pipeline = {**self.pipeline, "ms": {k: round(v, 3) for k, v in ms.items()}}

    def finalize(self) -> None:
        """Finalize the renderer (collective) on the solver thread, if a frame was rendered."""
        if self.renderer is not None:
            renderer, self.renderer = self.renderer, None
            self._s.in_solver_thread(renderer.finalize)
