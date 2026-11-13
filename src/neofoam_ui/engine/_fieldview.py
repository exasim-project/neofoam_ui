"""The field view of a session: render the viewed field in situ, at pauses and while running.

The data stays in the solver: each rank hands its cells and fields to a :class:`Renderer`
(by default Catalyst 2, :mod:`neofoam_ui.catalyst`). In ``image`` mode it returns one image of
the whole domain on rank 0, which goes to the UI in a ``frame`` message (base64 PNG), with the
pipeline diagram when it changed; in ``data`` mode Catalyst Live carries the viewed data to the
UI, and the frame only names what was published.

Frames come from the UI (a pause, a picked field, the live interval) or from the solver: a
case's in-situ operation calls :meth:`_FieldView.solver_frame` on every rank at its own
cadence, and from then on the UI's live interval leaves the running frames to it.
"""

from __future__ import annotations

import base64
import json
import time
import traceback
from typing import TYPE_CHECKING, Any, Optional, Sequence

import numpy as np

from neofoam_ui import catalyst
from neofoam_ui.engine._protocols import RenderBackend, Renderer
from neofoam_ui.engine._regions import _regions_of
from neofoam_ui.engine._tracking import _fingerprint
from neofoam_ui.geometry import cell_count, merged

if TYPE_CHECKING:
    from neofoam_ui.engine._session import Session

__all__ = ()

#: Where a custom pipeline script's own extracts go under the stepper (the worker runs in the case).
_SCRIPT_OUT = "postProcessing/catalyst"
#: A view request: field, component, mode (``image`` | ``data``), display options.
_View = tuple[Optional[str], str, str, dict[str, str]]
#: Seconds after which a live frame reads the renderer's pipeline back again (colour range etc.).
_DESCRIBE_EVERY = 5.0


def _merged_fields(parts: list[tuple[int, dict[str, np.ndarray]]]) -> dict[str, np.ndarray]:
    """The fields of regions merged into one grid (``parts``: each region's cell count and fields).

    A field missing in a region, or of another number of components there, is NaN in it."""
    shapes: dict[str, tuple[int, ...]] = {}
    for _, fields in parts:
        for k, v in fields.items():
            shapes.setdefault(k, v.shape[1:])
    out = {}
    for k, shape in shapes.items():
        out[k] = np.concatenate([
            f[k].astype(float, copy=False) if k in f and f[k].shape[1:] == shape else np.full((n, *shape), np.nan)
            for n, f in parts])
    out.setdefault("region", np.concatenate([np.full(n, float(i)) for i, (n, _) in enumerate(parts)]))
    return out


def _shown(view: Sequence[Optional[str]], fields: dict[str, np.ndarray]) -> Optional[dict[str, str]]:
    """The field and component to draw for ``view`` (field, component, ...): a default field for
    an unknown one, ``mag`` for an unknown component; ``None`` without fields."""
    name, comp = view[0], view[1]
    if name not in fields:
        name = next((k for k in ("U", "p", "T") if k in fields), next(iter(fields), None))
    if name is None:
        return None
    arr = fields[name]
    ncomp = int(arr.shape[1]) if arr.ndim == 2 else 1
    if ncomp == 1 or comp not in ["mag"] + [str(i) for i in range(ncomp)]:
        comp = "mag"
    return {"field": name, "component": comp}


def _params(view: _View, shown: Sequence[Optional[str]], fields: dict[str, np.ndarray]) -> Optional[dict[str, str]]:
    """The renderer's parameters of a frame: field and component of ``shown`` (:func:`_shown`),
    mode and options of ``view``."""
    params = _shown(shown, fields)
    return None if params is None else {**params, "mode": view[2], **view[3]}


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
        return catalyst.InSitu(out_dir=_SCRIPT_OUT if catalyst.custom_script() else None)


class _FieldView:
    """What the view shows, when it renders, and the frames it sends (for one session)."""

    def __init__(self, session: Session, backend: Optional[RenderBackend] = None) -> None:
        self._s = session
        self._backend = backend or _CatalystBackend()
        self._regions = _regions_of(session.solver)
        #: ``(field, component, mode, options)`` to render at every pause, ``None`` = view closed;
        #: mode ``image`` (a picture) or ``data`` (Catalyst Live, drawn by the UI); options go to
        #: the renderer with every frame (image size, camera, colours, slice: see ``pipelines/view.py``).
        #: Replaced as a whole (:meth:`request`), so another thread may set it while running.
        self.view: Optional[_View] = None
        #: The display options the next :meth:`request` takes (replaced as a whole).
        self.options: dict[str, str] = {}
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
        #: The solver renders in situ itself (:meth:`solver_frame`): no live frames of our own.
        self._solver_renders = False
        #: Under MPI, rank 0's :attr:`view` at its last ``go`` (:meth:`share`): the same on every rank.
        self.shared: Optional[_View] = None
        self._described: Optional[tuple[Any, float]] = None  # (params, when) of the last read-back
        self.describe_every = _DESCRIBE_EVERY
        self._rendered: Optional[bytes] = None  # the image of the last render

    # -- requests -------------------------------------------------------------------------

    def request(self, field: Optional[str], component: str = "mag", on: bool = True, mode: str = "image") -> None:
        """Open or close the view from any thread; the next frame (live or pause) shows it, with
        the current :attr:`options`."""
        if not on:
            self._epoch += 1  # before the swap: whoever sees the new view sees the new epoch
        self.view = (field, component, mode, dict(self.options)) if on else None

    def share(self) -> Optional[_View]:
        """Rank 0, before the ranks resume: the view its solver frames draw until the next ``go``."""
        self.shared = self.view
        return self.shared

    def time_step(self) -> None:
        """A time step while running: a frame if ``frame_every`` steps and ``frame_seconds`` passed."""
        s = self._s
        self._steps_since_frame += 1
        if self._solver_renders or self.view is None or self._steps_since_frame < max(1, s.frame_every):
            return
        if time.monotonic() - self._frame_at < s.frame_seconds:
            return
        self._steps_since_frame = 0
        self.send()

    # -- one frame ------------------------------------------------------------------------

    def _region_cells(self, region: Any) -> Optional[tuple[dict[str, Any], dict[str, np.ndarray]]]:
        """One region's geometry (built once) and its cell-sized fields; ``None`` = no geometry."""
        s = self._s
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
        return geom, {k: v for k, v in fields.items() if v.ndim in (1, 2) and len(v) == n}

    def _cells(self) -> Optional[tuple[str, dict[str, Any], dict[str, np.ndarray]]]:
        """The viewed cells: every region with a mesh, drawn together; ``None`` = no geometry.

        Several regions (CHT) are merged into one grid: a field is the regions' values one
        after the other, NaN (drawn grey) where a region has no field of that name or shape;
        ``region`` numbers the regions. A region whose geometry cannot be merged (not
        polyhedral) leaves only the first region in view."""
        parts = [(r.name, c) for r in self._regions(self._s.ctx) if (c := self._region_cells(r)) is not None]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0][0], *parts[0][1]
        name = "+".join(n for n, _ in parts)
        geom = self._geometry.get(name)
        if geom is None:
            geom = self._geometry[name] = merged([g for _, (g, _) in parts]) or False
        if not geom:
            return parts[0][0], *parts[0][1]
        return name, geom, _merged_fields([(cell_count(g), f) for _, (g, f) in parts])

    def _published(self, fields: dict[str, np.ndarray], params: dict[str, str]) -> dict[str, np.ndarray]:
        """What the renderer gets: the viewed field, or all for a renderer that uses them all."""
        if self._backend.publishes_all() or params.get("field") not in fields:
            return fields
        return {params["field"]: fields[params["field"]]}

    def _fail(self, text: str) -> None:
        if self._sent != ("error", self._epoch, text):
            self._sent = ("error", self._epoch, text)
            self._s.emit({"type": "frame", "image": None, "fields": [], "error": text})

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
        params = _params(view, view, fields)
        if params is None:
            return
        key = (self._epoch, region, *params.values(), _fingerprint(fields[params["field"]]))
        if key == self._sent:
            return
        self._sent = key
        self._frame_at = time.monotonic()
        if s.peers is not None:
            s.peers.render(params)
        self._emit_frame(self.render(params, describe=True), params, view, fields)

    def solver_frame(self, requested: dict[str, str]) -> None:
        """A frame the solver's in-situ operation asks for, on the solver thread of every rank.

        The UI's open view wins over ``requested`` (field, component) once it names a field.
        Under MPI that is the view rank 0 had at its last ``go`` (:attr:`shared`), so every
        rank draws the same field without a message here: every rank is in the operation
        already. The pause after it renders again only if the values changed."""
        s = self._s
        self._solver_renders = True
        if s.ctx is None or self._error or self._backend.unavailable() is not None:
            return
        cells = self._cells()
        if cells is None:
            return
        region, _, fields = cells
        asked = (requested.get("field"), requested.get("component", "mag"))
        alone = s.peers is None and not s.follower
        # read once: the command reader may replace the view meanwhile
        view = (self.view if alone else self.shared) or (*asked, "image", {})
        params = _params(view, view if view[0] else asked, fields)
        if params is None:
            return
        self._sent = (self._epoch, region, *params.values(), _fingerprint(fields[params["field"]]))
        self._frame_at = time.monotonic()
        self._render_guarded(params, describe=not s.follower)
        if not s.follower:
            self._emit_frame(self._rendered, params, view, fields)

    def _emit_frame(self, image: Optional[bytes], params: dict[str, str], view: _View,
                    fields: dict[str, np.ndarray]) -> None:
        """Send the ``frame`` message of ``image`` (``None`` in image mode: the render failed)."""
        s = self._s
        if image is None and params["mode"] != "data":
            return self._fail(self._error or "in-situ view failed")
        frame = {
            "type": "frame", "image": base64.b64encode(image).decode("ascii") if image is not None else None,
            "mode": params["mode"], "field": params["field"], "component": params["component"],
            "view": list(view[:3]),  # the request this frame answers (field/component may be a fallback)
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
        report = getattr(self.renderer, "report", None)
        if report:  # what the renderer tells of the frame (domain bounds, notes)
            frame["report"] = report
        s.emit(frame)
        if s.wait_ui and s.barrier is not None:
            s.barrier()

    def render(self, params: dict[str, str], describe: bool = False) -> Optional[bytes]:
        """Hand this rank's cells and fields to the renderer (collective); the image (rank 0).

        ``describe``: afterwards also read back the pipeline (:attr:`pipeline`); one rank does.
        """
        self._rendered = None
        if self._error is None:
            self._s.in_solver_thread(lambda: self._render_guarded(params, describe))
        return self._rendered

    def _render_guarded(self, params: dict[str, str], describe: bool) -> None:
        """:meth:`_render_here`, a failure disabling the view (not the session)."""
        self._rendered = None
        try:
            self._render_here(params, describe)
        except Exception as exc:  # noqa: BLE001 - the stepper outlives a broken view
            self._error = f"in-situ view failed: {exc!r}"
            self._s.emit({"type": "stdout", "text": "Catalyst: " + traceback.format_exc()})

    def _render_here(self, params: dict[str, str], describe: bool) -> None:
        """Render on the calling (solver) thread; the image in :attr:`_rendered`."""
        s = self._s
        cells = self._cells() if s.ctx is not None else None
        if cells is None:
            return
        if self.renderer is None:
            self.renderer = self._backend.create()
        time_value = s.solver.scalars(s.ctx).get("time") or 0.0
        self._rendered = self.renderer.execute(cells[1], self._published(cells[2], params), time_value, params)
        if describe and not self._describe_failed and hasattr(self.renderer, "describe"):
            try:
                self._describe(params)
            except Exception:  # noqa: BLE001 - a frame without its diagram is still a frame
                self.pipeline, self._describe_failed = None, True
                s.emit({"type": "stdout", "text": "pipeline diagram: " + traceback.format_exc()})

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
