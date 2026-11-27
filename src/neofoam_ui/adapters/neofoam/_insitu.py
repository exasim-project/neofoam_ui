"""The ``catalyst`` in-situ type of NeoFOAM's ``postProcess`` (see :func:`register_insitu`)."""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

import numpy as np
from neofoam.framework.context import Context
from neofoam.postprocess.insitu import InSitu
from pydantic import PrivateAttr

from neofoam_ui import catalyst
from neofoam_ui.adapters.neofoam._fields import _FieldAccess
from neofoam_ui.adapters.neofoam._mesh import _PolyMeshGeometry
from neofoam_ui.engine._fieldview import _merged_fields, _shown
from neofoam_ui.geometry import cell_count, merged

__all__ = ()


class _CaseCells(_FieldAccess, _PolyMeshGeometry):
    """A NeoFOAM context's mesh and cell fields, read as the stepper reads them."""


@InSitu.register
class CatalystView(InSitu):
    """One Catalyst view of the running case: ``field`` coloured by ``component``."""

    type: Literal["catalyst"] = "catalyst"
    field: Optional[str] = None
    component: str = "mag"

    _session: Any = PrivateAttr(default=None)
    _geometry: Optional[dict[str, Any]] = PrivateAttr(default=None)
    #: A multiRun's geometry per region on this process (and merged, under "+"-joined names).
    _geometries: dict[str, Any] = PrivateAttr(default_factory=dict)

    def execute(self, ctx: Context) -> None:
        requested = {"field": self.field or "", "component": self.component}
        driver = catalyst.frame_driver()
        if driver is not None:
            driver(requested)
            return
        self._render(ctx, requested)

    def execute_regions(self, regions: dict[str, Optional[Context]]) -> None:
        """A multiRun's regions in one view: merged into one grid, the field NaN where missing.

        Under MPI each process publishes its own region's cells; the field must then be
        named and be in every region (the processes draw the same array)."""
        requested = {"field": self.field or "", "component": self.component}
        driver = catalyst.frame_driver()
        if driver is not None:  # the stepper's field view draws its regions itself
            driver(requested)
            return
        here = {name: ctx for name, ctx in regions.items() if ctx is not None}
        cells = _CaseCells()
        parts = []
        for name, ctx in here.items():
            if name not in self._geometries:
                self._geometries[name] = cells.mesh(ctx)
            if self._geometries[name] is not None:
                fields = {k: v for k, v in cells.view_fields(ctx).items() if v.ndim in (1, 2)}
                parts.append((name, self._geometries[name], fields))
        if not parts:
            return
        alone = len(here) < len(regions)  # MPI: the other regions are on other processes
        if alone:
            missing = [name for name, _, fields in parts if requested["field"] not in fields]
            if missing:
                raise ValueError(
                    f"insitu {self.name}: under MPI the field must be in every region; "
                    f"{requested['field']!r} is not in {missing}"
                )
        key = "+".join(name for name, _, _ in parts)
        if key not in self._geometries:
            self._geometries[key] = merged([g for _, g, _ in parts]) if len(parts) > 1 else parts[0][1]
        geometry = self._geometries[key]
        if geometry is None:  # not mergeable (not polyhedral): the first region only
            _, geometry, fields = parts[0]
        else:
            fields = parts[0][2] if len(parts) == 1 else _merged_fields(
                [(cell_count(g), f) for _, g, f in parts])
        params = _shown((requested["field"], requested["component"]), fields)
        if params is None:
            return
        self._publish(ctx=next(iter(here.values())), geometry=geometry, fields=fields, params=params)

    def finalize(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.finalize()

    def _render(self, ctx: Context, requested: dict[str, str]) -> None:
        """Render on our own Catalyst session (no stepper in this process)."""
        cells = _CaseCells()
        if self._geometry is None:
            self._geometry = cells.mesh(ctx)
        fields = {k: v for k, v in cells.view_fields(ctx).items() if v.ndim in (1, 2)}
        params = _shown((requested["field"], requested["component"]), fields)
        if self._geometry is None or params is None:
            return
        self._publish(ctx=ctx, geometry=self._geometry, fields=fields, params=params)

    def _publish(self, ctx: Context, geometry: dict[str, Any], fields: dict[str, np.ndarray],
                 params: dict[str, str]) -> None:
        """One frame on this entry's own session (started on the first)."""
        if self._session is None:
            out_dir = catalyst.settings().out or os.path.join("postProcessing", self.name)
            self._session = catalyst.InSitu(out_dir=out_dir, capture=False)
        published = fields if catalyst.custom_script() else {params["field"]: fields[params["field"]]}
        time_value = float(np.asarray(getattr(ctx.time, "value", 0.0)))
        self._session.execute(geometry, published, time_value, params)
